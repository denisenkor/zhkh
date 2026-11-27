"""HTTP client for the Крымгазсети personal account."""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation

import aiohttp
from bs4 import BeautifulSoup
from homeassistant.exceptions import HomeAssistantError

BASE_URL = "https://lkk.crimeagasnet.ru/"
LOGIN_URL = BASE_URL

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


class CrimeaGasnetAuthError(HomeAssistantError):
    """The provider rejected the supplied credentials."""


class CrimeaGasnetConnectionError(HomeAssistantError):
    """A temporary network error occurred while contacting the provider."""


class CrimeaGasnetClient:
    """Authenticate and read all personal accounts from the provider table."""

    def __init__(self, email: str, password: str) -> None:
        self._email = email
        self._password = password
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers=_BROWSER_HEADERS,
        )
        self._logged_in = False

    async def _request(self, method: str, *, data=None, stage: str) -> str:
        """Perform an HTML request without exposing credentials or response data."""
        try:
            async with self._session.request(
                method,
                LOGIN_URL,
                data=data,
                allow_redirects=True,
            ) as response:
                if response.status in (408, 429) or response.status >= 500:
                    raise CrimeaGasnetConnectionError(
                        f"{stage}: HTTP {response.status}"
                    )
                if response.status >= 400:
                    raise HomeAssistantError(f"{stage}: HTTP {response.status}")
                return await response.text()
        except (CrimeaGasnetConnectionError, HomeAssistantError):
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CrimeaGasnetConnectionError(
                f"{stage}: ошибка соединения ({type(err).__name__})"
            ) from err

    async def async_login(self) -> None:
        """Initialize cookies and submit the provider's login form."""
        # A preliminary GET is harmless and allows the server to issue session cookies.
        await self._request("GET", stage="загрузка страницы входа")
        html = await self._request(
            "POST",
            data={
                "mail": self._email,
                "password": self._password,
                "submit2": "",
            },
            stage="авторизация",
        )

        soup = BeautifulSoup(html, "html.parser")
        if self._is_login_page(soup):
            raise CrimeaGasnetAuthError("Поставщик отклонил авторизацию")

        # A successful login response is expected to contain the account table.
        if soup.select_one("table.table.table-hover.table-bordered") is None:
            raise HomeAssistantError(
                "После авторизации не найдена таблица лицевых счетов"
            )
        self._logged_in = True

    @staticmethod
    def _is_login_page(soup: BeautifulSoup) -> bool:
        """Detect the login form without relying on localized page text."""
        return (
            soup.select_one('input[name="mail"]') is not None
            and soup.select_one('input[name="password"]') is not None
        )

    @staticmethod
    def _parse_amount(raw_value: str) -> Decimal:
        """Parse a Russian-formatted debt/advance amount."""
        normalized = raw_value.replace("\u00a0", " ").replace("\u202f", " ")
        normalized = normalized.replace(" ", "").replace(",", ".")
        normalized = re.sub(r"[^0-9+.-]", "", normalized)
        try:
            return Decimal(normalized)
        except (InvalidOperation, ValueError) as err:
            raise HomeAssistantError(
                "Крымгазсети вернул некорректную сумму задолженности"
            ) from err

    @staticmethod
    def _parse_accounts(html: str) -> list[dict]:
        """Parse account rows; positive values mean debt, negative values mean advance."""
        soup = BeautifulSoup(html, "html.parser")
        table = soup.select_one("table.table.table-hover.table-bordered")
        if table is None:
            raise HomeAssistantError("Таблица лицевых счетов не найдена")

        accounts = []
        for row in table.select("tbody tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 5:
                continue

            account_number = cells[0].get_text(" ", strip=True)
            provider = cells[1].get_text(" ", strip=True)
            full_name = cells[2].get_text(" ", strip=True)
            address = cells[3].get_text(" ", strip=True)
            raw_balance = cells[4].get_text(" ", strip=True)
            identity = cells[0].get("val") or ""

            if not account_number or not identity:
                continue

            account_key = hashlib.sha256(
                identity.encode("utf-8")
            ).hexdigest()[:20]
            accounts.append(
                {
                    "id": account_key,
                    "account": account_number,
                    "provider": provider or "ГУП РК «Крымгазсети»",
                    "name": full_name,
                    "address": address,
                    "balance": CrimeaGasnetClient._parse_amount(raw_balance),
                }
            )

        if not accounts:
            raise HomeAssistantError(
                "В личном кабинете Крымгазсети не найдены лицевые счета"
            )
        return accounts

    async def async_get_accounts(self) -> list[dict]:
        """Return all personal accounts from the authenticated session."""
        if not self._logged_in:
            await self.async_login()

        html = await self._request("GET", stage="получение лицевых счетов")
        soup = BeautifulSoup(html, "html.parser")
        if self._is_login_page(soup):
            self._logged_in = False
            await self.async_login()
            html = await self._request(
                "GET", stage="повторное получение лицевых счетов"
            )
            soup = BeautifulSoup(html, "html.parser")
            if self._is_login_page(soup):
                raise CrimeaGasnetAuthError(
                    "Не удалось восстановить сессию Крымгазсети"
                )
        return self._parse_accounts(html)

    async def async_close(self) -> None:
        """Close the HTTP session."""
        if not self._session.closed:
            await self._session.close()
