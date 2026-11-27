"""HTTP client for lk.tce.crimea.ru."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup
from homeassistant.exceptions import HomeAssistantError

BASE_URL = "https://lk.tce.crimea.ru"
LOGIN_URL = f"{BASE_URL}/cabinet/"
SERVICES_URL = f"{BASE_URL}/services"


class TceCrimeaClient:
    """Drupal form login and balance parser."""

    def __init__(self, account: str, password: str) -> None:
        self._account = account
        self._password = password
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "Home Assistant ЖКХ integration/0.1.1"},
        )
        self._logged_in = False

    async def async_login(self) -> None:
        """Fetch Drupal login form and submit credentials."""
        try:
            async with self._session.get(LOGIN_URL) as response:
                response.raise_for_status()
                login_html = await response.text()

            soup = BeautifulSoup(login_html, "html.parser")
            form = soup.select_one("form#user-login-form")
            if form is None:
                raise HomeAssistantError("На странице не найдена форма входа")

            payload = {
                "name": self._account,
                "pass": self._password,
                "op": "Войти",
                "form_id": "user_login_central_block",
            }
            for field in ("form_build_id", "form_token", "form_id"):
                element = form.select_one(f'input[name="{field}"]')
                if element and element.get("value") is not None:
                    payload[field] = element["value"]

            action = form.get("action") or "/cabinet?destination=cabinet"
            post_url = urljoin(BASE_URL, action)
            async with self._session.post(
                post_url, data=payload, allow_redirects=True
            ) as response:
                response.raise_for_status()
                final_url = str(response.url)
                body = await response.text()

            if "/services" not in final_url:
                # Some Drupal configurations return a page with a login error.
                check = BeautifulSoup(body, "html.parser")
                if check.select_one("form#user-login-form"):
                    raise HomeAssistantError("Сайт отклонил логин или пароль")
                # Verify session directly; do not assume POST success from status alone.
                async with self._session.get(SERVICES_URL) as response:
                    response.raise_for_status()
                    body = await response.text()
                    final_url = str(response.url)

            if "/services" not in final_url or "info_column_primary" not in body:
                raise HomeAssistantError(
                    "После входа не удалось получить HTML личного кабинета"
                )
            self._logged_in = True
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError("Ошибка соединения с личным кабинетом") from err

    async def async_get_balance(self) -> Decimal:
        """Get balance from the authenticated services page."""
        if not self._logged_in:
            await self.async_login()
        try:
            async with self._session.get(SERVICES_URL) as response:
                response.raise_for_status()
                html = await response.text()
                final_url = str(response.url)
            if "/cabinet" in final_url or "user-login-form" in html:
                self._logged_in = False
                await self.async_login()
                async with self._session.get(SERVICES_URL) as response:
                    response.raise_for_status()
                    html = await response.text()
            return self._parse_balance(html)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError("Не удалось загрузить страницу услуг") from err

    @staticmethod
    def _parse_balance(html: str) -> Decimal:
        """Extract debt/overpayment amount from the info block."""
        soup = BeautifulSoup(html, "html.parser")
        block = soup.select_one(".info_column_primary")
        if block is None:
            raise HomeAssistantError("На странице отсутствует блок с данными абонента")

        titles = block.select("div.title")
        for title in titles:
            if "состояние расчетов" not in title.get_text(" ", strip=True).lower():
                continue
            value = title.find_next_sibling("div", class_="value")
            if value is None:
                raise HomeAssistantError("Не найдено значение состояния расчетов")
            text = value.get_text(" ", strip=True).lower().replace("\u00a0", " ")
            match = re.search(r"(долг|задолженность|переплата)\s*([-+]?\d[\d\s]*(?:[,.]\d+)?)\s*руб", text)
            if not match:
                if any(word in text for word in ("нет задолженности", "задолженности нет", "оплачено")):
                    return Decimal("0")
                raise HomeAssistantError("Не удалось распознать сумму задолженности")
            try:
                amount = Decimal(match.group(2).replace(" ", "").replace(",", "."))
            except InvalidOperation as err:
                raise HomeAssistantError("Некорректная сумма задолженности") from err
            return -amount if match.group(1) == "переплата" else amount

        raise HomeAssistantError("Не найдено поле «Состояние расчетов»")

    async def async_close(self) -> None:
        """Close the HTTP session."""
        if not self._session.closed:
            await self._session.close()
