"""HTTP client for the НО «РФ КРМД РК» personal account."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup
from homeassistant.exceptions import HomeAssistantError

BASE_URL = "https://lk091.eisgkh.ru/"
LOGIN_URL = urljoin(BASE_URL, "index.htm")
PAY_URL = urljoin(BASE_URL, "pay.htm")

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


class EisgkhAuthError(HomeAssistantError):
    """The provider rejected the supplied credentials."""


class EisgkhConnectionError(HomeAssistantError):
    """A temporary network error occurred while contacting the provider."""


class EisgkhClient:
    """Authenticate and read the total payment amount from pay.htm."""

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
                    raise EisgkhConnectionError(f"{stage}: HTTP {response.status}")
                if response.status >= 400:
                    raise HomeAssistantError(f"{stage}: HTTP {response.status}")
                return await response.text()
        except (EisgkhConnectionError, HomeAssistantError):
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EisgkhConnectionError(
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
            raise HomeAssistantError("На странице НО «РФ КРМД РК» не найдена форма авторизации")

        path_input = form.select_one('input[type="hidden"][name="path"]')
        if path_input is None or not path_input.get("value"):
            raise HomeAssistantError("В форме входа НО «РФ КРМД РК» не найден параметр path")

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
            stage="авторизация НО «РФ КРМД РК»",
        )

        pay_html = await self._request(
            "GET", PAY_URL, stage="загрузка страницы оплаты НО «РФ КРМД РК»"
        )
        pay_soup = BeautifulSoup(pay_html, "html.parser")
        if pay_soup.select_one('input[name="sum"]') is None:
            response_soup = BeautifulSoup(response_html, "html.parser")
            if response_soup.select_one('form[name="auth"]') is not None:
                raise EisgkhAuthError("НО «РФ КРМД РК» отклонила учётные данные")
            raise HomeAssistantError(
                "После авторизации НО «РФ КРМД РК» не найдено поле итоговой суммы платежа"
            )
        self._logged_in = True

    @staticmethod
    def _parse_amount(raw_value: str) -> Decimal:
        """Parse a Russian-formatted payment amount."""
        normalized = raw_value.replace("\u00a0", " ").replace("\u202f", " ")
        match = re.search(r"[+-]?\d[\d ]*(?:[.,]\d+)?", normalized)
        if match is None:
            raise HomeAssistantError("НО «РФ КРМД РК» вернула некорректную сумму платежа")
        normalized = match.group(0).replace(" ", "").replace(",", ".")
        try:
            return Decimal(normalized)
        except (InvalidOperation, ValueError) as err:
            raise HomeAssistantError(
                "НО «РФ КРМД РК» вернула некорректную сумму платежа"
            ) from err

    @classmethod
    def _parse_data(cls, html: str) -> dict:
        """Extract the total payment amount from the hidden sum field."""
        soup = BeautifulSoup(html, "html.parser")
        amount_input = soup.select_one('input[name="sum"]')
        if amount_input is None or not amount_input.get("value"):
            raise HomeAssistantError(
                "На странице оплаты НО «РФ КРМД РК» не найдено значение итоговой суммы"
            )
        return {
            "provider": "НО «РФ КРМД РК»",
            "balance": cls._parse_amount(amount_input["value"]),
        }

    async def async_get_data(self) -> dict:
        """Return the current total payment amount."""
        if not self._logged_in:
            await self.async_login()
        html = await self._request(
            "GET", PAY_URL, stage="получение суммы платежа НО «РФ КРМД РК»"
        )
        if BeautifulSoup(html, "html.parser").select_one('input[name="sum"]') is None:
            self._logged_in = False
            await self.async_login()
            html = await self._request(
                "GET", PAY_URL, stage="повторное получение суммы платежа НО «РФ КРМД РК»"
            )
        return self._parse_data(html)

    async def async_close(self) -> None:
        """Close the HTTP session."""
        if not self._session.closed:
            await self._session.close()
