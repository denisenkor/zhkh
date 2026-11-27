"""HTTP client for the Crimea Energy personal account API."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

import aiohttp
from homeassistant.exceptions import HomeAssistantError

API_URL = "https://lapi.crimea-energy.ru/"


class CrimeaEnergyAuthError(HomeAssistantError):
    """The provider rejected the supplied credentials."""


class CrimeaEnergyConnectionError(HomeAssistantError):
    """A temporary network or server error occurred."""


class CrimeaEnergyClient:
    """Client for the Crimea Energy JSON API."""

    def __init__(self, email: str, password: str) -> None:
        self._email = email
        self._password = password
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/157.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
            },
        )
        self._token: str | None = None

    async def _request_json(self, *, method: str, params=None, data=None, stage: str):
        """Perform an API request and translate retryable failures."""
        try:
            async with self._session.request(
                method, API_URL, params=params, data=data
            ) as response:
                if response.status in (408, 429) or response.status >= 500:
                    raise CrimeaEnergyConnectionError(
                        f"{stage}: HTTP {response.status}"
                    )
                if response.status >= 400:
                    raise HomeAssistantError(f"{stage}: HTTP {response.status}")
                try:
                    return await response.json(content_type=None)
                except (ValueError, json.JSONDecodeError) as err:
                    raise HomeAssistantError(
                        f"{stage}: сервер вернул некорректный JSON"
                    ) from err
        except CrimeaEnergyConnectionError:
            raise
        except HomeAssistantError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CrimeaEnergyConnectionError(
                f"{stage}: ошибка соединения ({type(err).__name__})"
            ) from err

    async def async_login(self) -> None:
        """Authenticate and keep the returned token in memory."""
        form = aiohttp.FormData(default_to_multipart=True)
        form.add_field("type", "user")
        form.add_field("action", "login")
        form.add_field("token", "undefined")
        form.add_field(
            "data",
            json.dumps(
                {"email": self._email, "password": self._password},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )

        result = await self._request_json(
            method="POST", data=form, stage="авторизация"
        )
        if not isinstance(result, dict) or result.get("auth") != "successful":
            raise CrimeaEnergyAuthError("Поставщик отклонил авторизацию")
        if result.get("tfa"):
            raise HomeAssistantError(
                "Поставщик требует двухфакторную авторизацию, "
                "которая пока не поддерживается"
            )

        token = result.get("token")
        if not isinstance(token, str) or not token:
            raise HomeAssistantError("Сервер не вернул токен авторизации")
        self._token = token

    async def async_get_bills(self) -> list[dict]:
        """Fetch all personal accounts associated with the authenticated user."""
        if self._token is None:
            await self.async_login()

        result = await self._request_json(
            method="GET",
            params={
                "type": "bills",
                "action": "getBills",
                # The web client sends the token as a JSON string, including quotes.
                "token": json.dumps(self._token),
            },
            stage="получение лицевых счетов",
        )

        if not isinstance(result, list):
            raise HomeAssistantError("Сервер вернул неверный формат списка счетов")

        bills = []
        for item in result:
            if not isinstance(item, dict):
                continue
            account_id = item.get("id")
            account_number = item.get("acc_id")
            name = item.get("fullname")
            raw_balance = item.get("balance")
            if account_id is None or account_number is None or raw_balance is None:
                continue
            try:
                balance = Decimal(str(raw_balance).replace(",", "."))
            except InvalidOperation as err:
                raise HomeAssistantError(
                    "Поставщик вернул некорректный баланс лицевого счёта"
                ) from err
            bills.append(
                {
                    "id": str(account_id),
                    "account": str(account_number),
                    "name": str(name or "Энергосбыт"),
                    "balance": balance,
                    "provider_update": item.get("p_up_date"),
                    "address": item.get("adress"),
                    "serial": item.get("serial"),
                    "penalty": item.get("penalty"),
                }
            )

        if not bills:
            raise HomeAssistantError("В личном кабинете не найдено лицевых счетов")
        return bills

    async def async_close(self) -> None:
        """Close the HTTP session."""
        if not self._session.closed:
            await self._session.close()
