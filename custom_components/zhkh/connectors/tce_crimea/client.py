"""HTTP client for the Крымтеплокоммунэнерго personal account."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

import aiohttp
from yarl import URL
from bs4 import BeautifulSoup
from homeassistant.exceptions import HomeAssistantError

BASE_URL = "https://lk.tce.crimea.ru"
LOGIN_URL = f"{BASE_URL}/cabinet/"
LOGIN_REFERER = f"{BASE_URL}/cabinet"
SERVICES_URL = f"{BASE_URL}/services"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 "
        "Safari/537.36 OPR/140.0.0.0"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ru,en-US;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}


class TceCrimeaAuthError(HomeAssistantError):
    """The provider rejected the supplied credentials."""


class TceCrimeaClient:
    """Authenticate with the provider and parse the current account balance."""

    def __init__(self, account: str, password: str) -> None:
        self._account = account
        self._password = password
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers=_BROWSER_HEADERS,
        )
        self._logged_in = False

    @staticmethod
    def _is_login_page(html: str) -> bool:
        return BeautifulSoup(html, "html.parser").select_one(
            "form#user-login-form"
        ) is not None

    def _ensure_browser_cookies(self) -> None:
        """Reproduce cookies set by the browser-side Drupal bootstrap."""
        current = self._session.cookie_jar.filter_cookies(URL(LOGIN_URL))
        missing = {}
        if "has_js" not in current:
            missing["has_js"] = "1"
        if "login_status" not in current:
            missing["login_status"] = "anonymous"
        if missing:
            self._session.cookie_jar.update_cookies(
                missing, response_url=URL(LOGIN_URL)
            )

    async def _async_get_services_html(self) -> tuple[str, str]:
        """Fetch services page and return its HTML and final URL."""
        async with self._session.get(
            SERVICES_URL, headers={"Referer": LOGIN_REFERER}
        ) as response:
            html = await response.text()
            response.raise_for_status()
            return html, str(response.url)

    async def async_login(self) -> None:
        """Fetch the Drupal login form, submit it, and verify the session."""
        stage = "загрузка страницы входа"
        try:
            async with self._session.get(LOGIN_URL) as response:
                login_html = await response.text()
                response.raise_for_status()

            self._ensure_browser_cookies()
            soup = BeautifulSoup(login_html, "html.parser")
            form = None
            for candidate in soup.select("form"):
                form_id_field = candidate.select_one('input[name="form_id"]')
                if (
                    form_id_field is not None
                    and form_id_field.get("value") == "user_login_central_block"
                ):
                    form = candidate
                    break

            if form is None:
                raise HomeAssistantError("Форма авторизации поставщика не найдена")

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
            stage = "отправка формы авторизации"
            async with self._session.post(
                post_url,
                data=payload,
                allow_redirects=True,
                headers={
                    "Origin": BASE_URL,
                    "Referer": LOGIN_REFERER,
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-User": "?1",
                },
            ) as response:
                post_html = await response.text()
                response.raise_for_status()

            if self._is_login_page(post_html):
                raise TceCrimeaAuthError("Поставщик отклонил авторизацию")

            stage = "проверка личного кабинета"
            html, final_url = await self._async_get_services_html()
            page = BeautifulSoup(html, "html.parser")

            if (
                "/services" not in final_url
                or page.select_one("form#user-login-form") is not None
            ):
                raise TceCrimeaAuthError("Не удалось войти в личный кабинет")

            if page.select_one(".info_column_primary") is None:
                raise HomeAssistantError("Данные абонента на странице не найдены")

            self._logged_in = True

        except (TceCrimeaAuthError, HomeAssistantError):
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            status = (
                f"HTTP {err.status}"
                if isinstance(err, aiohttp.ClientResponseError)
                else type(err).__name__
            )
            raise HomeAssistantError(
                f"Ошибка соединения при {stage} ({status})"
            ) from err

    async def async_get_balance(self) -> Decimal:
        """Get balance from the authenticated services page."""
        if not self._logged_in:
            await self.async_login()

        try:
            html, final_url = await self._async_get_services_html()
            page = BeautifulSoup(html, "html.parser")
            if (
                "/services" not in final_url
                or page.select_one("form#user-login-form") is not None
            ):
                self._logged_in = False
                await self.async_login()
                html, final_url = await self._async_get_services_html()
                page = BeautifulSoup(html, "html.parser")
                if (
                    "/services" not in final_url
                    or page.select_one("form#user-login-form") is not None
                ):
                    raise TceCrimeaAuthError(
                        "Не удалось восстановить сессию поставщика"
                    )

            return self._parse_balance(html)

        except (TceCrimeaAuthError, HomeAssistantError):
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            status = (
                f"HTTP {err.status}"
                if isinstance(err, aiohttp.ClientResponseError)
                else type(err).__name__
            )
            raise HomeAssistantError(
                f"Не удалось загрузить данные личного кабинета ({status})"
            ) from err

    @staticmethod
    def _parse_balance(html: str) -> Decimal:
        """Extract debt or overpayment amount from the subscriber information block."""
        soup = BeautifulSoup(html, "html.parser")
        block = soup.select_one(".info_column_primary")
        if block is None:
            raise HomeAssistantError("Блок данных абонента не найден")

        for title in block.select("div.title"):
            label = " ".join(title.get_text(" ", strip=True).lower().split())
            if (
                "состояние расчетов" not in label
                and "состояние расчётов" not in label
            ):
                continue

            value = title.find_next_sibling("div", class_="value")
            if value is None:
                raise HomeAssistantError("Не найдено значение состояния расчётов")

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
                (
                    r"(долг|задолженность|переплата)\s*"
                    r"([-+]?\d[\d\s]*(?:[,.]\d+)?)\s*"
                    r"(?:руб(?:\.|лей|ля)?|₽)"
                ),
                status,
            )
            if match is None:
                raise HomeAssistantError(
                    "Не удалось распознать сумму задолженности на странице поставщика"
                )

            try:
                amount = Decimal(
                    match.group(2).replace(" ", "").replace(",", ".")
                )
            except InvalidOperation as err:
                raise HomeAssistantError(
                    "Поставщик вернул некорректную сумму задолженности"
                ) from err

            return -amount if match.group(1) == "переплата" else amount

        raise HomeAssistantError("На странице поставщика не найдено состояние расчётов")

    async def async_close(self) -> None:
        """Close the HTTP session."""
        if not self._session.closed:
            await self._session.close()
