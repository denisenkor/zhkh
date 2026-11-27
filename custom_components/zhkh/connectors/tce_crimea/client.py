"""HTTP client for lk.tce.crimea.ru."""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup
from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)

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
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Upgrade-Insecure-Requests": "1",
                "Referer": LOGIN_URL,
            },
        )
        self._logged_in = False

    def _log_response(self, stage: str, response: aiohttp.ClientResponse, body: str) -> None:
        """Log useful response diagnostics without exposing credentials or private data."""
        safe_headers = {
            key: ("<present; value redacted>" if key == "Location" else response.headers.get(key))
            for key in ("Content-Type", "Content-Length", "Location", "Server", "Via")
            if response.headers.get(key) is not None
        }
        cookies = []
        for cookie in response.cookies.values():
            cookies.append({
                "name": cookie.key,
                "value": "<redacted>",
                "domain": cookie["domain"] or response.url.host,
                "path": cookie["path"] or "/",
                "secure": bool(cookie["secure"]),
                "httponly": bool(cookie["httponly"]),
                "samesite": cookie["samesite"],
            })

        soup = BeautifulSoup(body, "html.parser")
        for node in soup.select("script, style, .info_column_primary"):
            node.decompose()
        for field in soup.select("input"):
            field.attrs.pop("value", None)
        text_preview = " ".join(soup.get_text(" ", strip=True).split())[:500]
        if self._account:
            text_preview = text_preview.replace(self._account, "<account-redacted>")
        if self._password:
            text_preview = text_preview.replace(self._password, "<password-redacted>")
        text_preview = re.sub(
            r"(?i)\\beyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\b",
            "<token-redacted>",
            text_preview,
        )

        _LOGGER.debug(
            "TCE %s response: status=%s url=%s final_url=%s headers=%s "
            "cookies=%s cookie_jar_names=%s body_length=%s body_preview=%r",
            stage,
            response.status,
            response.request_info.real_url.with_query(None),
            response.url.with_query(None),
            safe_headers,
            cookies,
            sorted(self._session.cookie_jar.filter_cookies(response.url).keys()),
            len(body),
            text_preview,
        )

    async def async_login(self) -> None:
        """Fetch Drupal login form, submit it, then verify the session."""
        stage = "GET login page"
        request_url = LOGIN_URL
        try:
            async with self._session.get(LOGIN_URL) as response:
                login_html = await response.text()
                self._log_response(stage, response, login_html)
                response.raise_for_status()

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
            stage = "POST login form"
            request_url = post_url
            async with self._session.post(
                post_url,
                data=payload,
                allow_redirects=True,
                headers={"Referer": LOGIN_URL},
            ) as response:
                post_html = await response.text()
                self._log_response(stage, response, post_html)
                response.raise_for_status()

            stage = "GET services page after login"
            request_url = SERVICES_URL
            async with self._session.get(
                SERVICES_URL, headers={"Referer": LOGIN_URL}
            ) as response:
                html = await response.text()
                self._log_response(stage, response, html)
                response.raise_for_status()
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
            detail = f"HTTP {err.status}" if isinstance(err, aiohttp.ClientResponseError) else type(err).__name__
            raise HomeAssistantError(
                f"Connection failed during {stage} ({detail}), URL: {request_url}"
            ) from err

    async def async_get_balance(self) -> Decimal:
        """Get balance from the authenticated services page."""
        if not self._logged_in:
            await self.async_login()
        try:
            async with self._session.get(SERVICES_URL) as response:
                html = await response.text()
                self._log_response("GET services page", response, html)
                response.raise_for_status()
                final_url = str(response.url)

            page = BeautifulSoup(html, "html.parser")
            if (
                "/services" not in final_url
                or page.select_one("form#user-login-form") is not None
            ):
                self._logged_in = False
                await self.async_login()
                async with self._session.get(SERVICES_URL) as response:
                    html = await response.text()
                    self._log_response("GET services page after re-login", response, html)
                    response.raise_for_status()

            return self._parse_balance(html)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Could not load provider services page ({type(err).__name__}), "
                f"URL: {SERVICES_URL}"
            ) from err

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
