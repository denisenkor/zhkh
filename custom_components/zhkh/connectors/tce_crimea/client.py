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


class TceCrimeaAuthError(HomeAssistantError):
    """The provider rejected the supplied credentials."""


class TceCrimeaClient:
    """Drupal form login and balance parser."""

    def __init__(self, account: str, password: str) -> None:
        self._account = account
        self._password = password
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={
                "User-Agent": "Home Assistant ЖКХ integration/0.1.1",
                "Referer": LOGIN_URL,
            },
        )
        self._logged_in = False

    async def async_login(self) -> None:
        """Fetch Drupal login form, submit it, then verify the session."""
        try:
            async with self._session.get(LOGIN_URL) as response:
                response.raise_for_status()
                login_html = await response.text()

            soup = BeautifulSoup(login_html, "html.parser")
            form = soup.select_one("form#user-login-form")
            if form is None:
                raise HomeAssistantError("Login form was not found")

            payload = {
                "name": self._account,
                "pass": self._password,
            }
            for field in ("form_build_id", "form_token", "form_id"):
                element = form.select_one(f'input[name="{field}"]')
                if element and element.get("value") is not None:
                    payload[field] = element["value"]

            submit = form.select_one('input[name="op"]')
            payload["op"] = submit.get("value", "Войти") if submit else "Войти"
            payload.setdefault("form_id", "user_login_central_block")

            action = form.get("action") or "/cabinet?destination=cabinet"
            post_url = urljoin(BASE_URL, action)
            async with self._session.post(
                post_url,
                data=payload,
                allow_redirects=True,
                headers={"Referer": LOGIN_URL},
            ) as response:
                response.raise_for_status()
                # Consume the response before requesting the authenticated page.
                await response.read()

            # Always verify authentication by requesting the page the user sees
            # after login. A successful POST status alone does not prove login.
            async with self._session.get(
                SERVICES_URL, headers={"Referer": LOGIN_URL}
            ) as response:
                response.raise_for_status()
                html = await response.text()
                final_url = str(response.url)

            check = BeautifulSoup(html, "html.parser")
            if (
                "/services" not in final_url
                or check.select_one("form#user-login-form") is not None
            ):
                raise TceCrimeaAuthError("Provider login was rejected")
            if check.select_one(".info_column_primary") is None:
                raise HomeAssistantError("Subscriber information block was not found")

            self._logged_in = True
        except TceCrimeaAuthError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError("Connection to provider failed") from err

    async def async_get_balance(self) -> Decimal:
        """Get balance from the authenticated services page."""
        if not self._logged_in:
            await self.async_login()
        try:
            async with self._session.get(SERVICES_URL) as response:
                response.raise_for_status()
                html = await response.text()
                final_url = str(response.url)

            page = BeautifulSoup(html, "html.parser")
            if (
                "/services" not in final_url
                or page.select_one("form#user-login-form") is not None
            ):
                self._logged_in = False
                await self.async_login()
                async with self._session.get(SERVICES_URL) as response:
                    response.raise_for_status()
                    html = await response.text()

            return self._parse_balance(html)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError("Could not load provider services page") from err

    @staticmethod
    def _parse_balance(html: str) -> Decimal:
        """Extract debt/overpayment amount from the info block."""
        soup = BeautifulSoup(html, "html.parser")
        block = soup.select_one(".info_column_primary")
        if block is None:
            raise HomeAssistantError("Subscriber information block is missing")

        for title in block.select("div.title"):
            label = " ".join(title.get_text(" ", strip=True).lower().split())
            if "состояние расчетов" not in label and "состояние расчётов" not in label:
                continue

            value = title.find_next_sibling("div", class_="value")
            if value is None:
                raise HomeAssistantError("Settlement status value is missing")

            status = value.get_text(" ", strip=True).lower()
            status = status.replace("\u00a0", " ").replace("\u202f", " ")
            status = " ".join(status.split())

            if any(
                phrase in status
                for phrase in (
                    "нет задолженности",
                    "задолженности нет",
                    "задолженность отсутствует",
                    "переплаты и задолженности нет",
                    "оплачено",
                )
            ):
                return Decimal("0")

            match = re.search(
                r"(долг|задолженность|переплата)\s*([-+]?\d[\d\s]*(?:[,.]\d+)?)\s*(?:руб(?:\.|лей|ля)?|₽)",
                status,
            )
            if match is None:
                raise HomeAssistantError("Settlement amount format is not recognized")

            try:
                amount = Decimal(
                    match.group(2).replace(" ", "").replace(",", ".")
                )
            except InvalidOperation as err:
                raise HomeAssistantError("Settlement amount is invalid") from err

            return -amount if match.group(1) == "переплата" else amount

        raise HomeAssistantError("Settlement status field was not found")

    async def async_close(self) -> None:
        """Close the HTTP session."""
        if not self._session.closed:
            await self._session.close()
