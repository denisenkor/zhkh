"""HTTP client for the Крымэкоресурсы personal account."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup
from homeassistant.exceptions import HomeAssistantError

BASE_URL = "https://lk.crimea-ecor.ru/"
LOGIN_URL = urljoin(BASE_URL, "index.htm")
INFO_URL = urljoin(BASE_URL, "info.htm")

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


class CrimeaEcorAuthError(HomeAssistantError):
    """The provider rejected the supplied credentials."""


class CrimeaEcorConnectionError(HomeAssistantError):
    """A temporary network error occurred while contacting the provider."""


class CrimeaEcorClient:
    """Authenticate and read supplier and debt from the info page."""

    def __init__(self, account: str, password: str) -> None:
        self._account = account
        self._password = password
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers=_BROWSER_HEADERS,
        )
        self._logged_in = False

    async def _request(self, method: str, url: str, *, data=None, stage: str) -> str:
        """Perform a request without logging credentials or page contents."""
        try:
            async with self._session.request(
                method, url, data=data, allow_redirects=True
            ) as response:
                if response.status in (408, 429) or response.status >= 500:
                    raise CrimeaEcorConnectionError(
                        f"{stage}: HTTP {response.status}"
                    )
                if response.status >= 400:
                    raise HomeAssistantError(f"{stage}: HTTP {response.status}")
                return await response.text()
        except (CrimeaEcorConnectionError, HomeAssistantError):
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CrimeaEcorConnectionError(
                f"{stage}: ошибка соединения ({type(err).__name__})"
            ) from err

    async def async_login(self) -> None:
        """Read the dynamic hidden path and submit the login form."""
        login_html = await self._request(
            "GET", LOGIN_URL, stage="загрузка страницы входа"
        )
        soup = BeautifulSoup(login_html, "html.parser")
        form = soup.select_one('form[name="auth"]')
        if form is None:
            raise HomeAssistantError("На странице входа не найдена форма авторизации")

        path_input = form.select_one('input[type="hidden"][name="path"]')
        if path_input is None or not path_input.get("value"):
            raise HomeAssistantError("В форме входа не найден обязательный параметр path")

        action = form.get("action") or LOGIN_URL
        post_url = urljoin(LOGIN_URL, action)
        submit = form.select_one('input[type="submit"][name="auth"]')
        submit_value = submit.get("value", "Войти") if submit else "Войти"

        response_html = await self._request(
            "POST",
            post_url,
            data={
                "account": self._account,
                "accpassw": self._password,
                "path": path_input["value"],
                "auth": submit_value,
            },
            stage="авторизация",
        )

        info_html = await self._request(
            "GET", INFO_URL, stage="загрузка данных лицевого счёта"
        )
        info_soup = BeautifulSoup(info_html, "html.parser")
        if info_soup.select_one("table.service.hover") is None:
            response_soup = BeautifulSoup(response_html, "html.parser")
            if response_soup.select_one('form[name="auth"]') is not None:
                raise CrimeaEcorAuthError("Поставщик отклонил авторизацию")
            raise HomeAssistantError("После авторизации не найдена таблица услуг")
        self._logged_in = True

    @staticmethod
    def _parse_amount(raw_value: str) -> Decimal:
        """Parse a Russian-formatted amount."""
        normalized = raw_value.replace("\u00a0", " ").replace("\u202f", " ")
        normalized = normalized.replace(" ", "").replace(",", ".")
        normalized = re.sub(r"[^0-9+.-]", "", normalized)
        try:
            return Decimal(normalized)
        except (InvalidOperation, ValueError) as err:
            raise HomeAssistantError(
                "Крымэкоресурсы вернули некорректное значение задолженности"
            ) from err

    @classmethod
    def _parse_info(cls, html: str) -> dict:
        """Extract supplier and tariff amount from the service table."""
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("table.service.hover")
        if table is None:
            raise HomeAssistantError("Таблица услуг Крымэкоресурсы не найдена")

        providers = []
        amounts = []
        for row in table.select("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 3:
                continue
            provider = cells[1].get_text(" ", strip=True)
            raw_amount = cells[-1].get_text(" ", strip=True)
            if provider:
                providers.append(provider)
            if raw_amount:
                amounts.append(cls._parse_amount(raw_amount))

        if not providers or not amounts:
            raise HomeAssistantError(
                "В таблице услуг не удалось определить поставщика или задолженность"
            )

        # The provider table may contain more than one service row; combine the
        # tariff amounts into one account-level total.
        return {
            "provider": providers[0],
            "balance": sum(amounts, Decimal("0")),
        }

    async def async_get_data(self) -> dict:
        """Return the current supplier and debt."""
        if not self._logged_in:
            await self.async_login()
        html = await self._request(
            "GET", INFO_URL, stage="получение задолженности"
        )
        soup = BeautifulSoup(html, "html.parser")
        if soup.select_one("table.service.hover") is None:
            self._logged_in = False
            await self.async_login()
            html = await self._request(
                "GET", INFO_URL, stage="повторное получение задолженности"
            )
        return self._parse_info(html)

    async def async_close(self) -> None:
        """Close the HTTP session."""
        if not self._session.closed:
            await self._session.close()
