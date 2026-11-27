"""HTTP client for lk.tce.crimea.ru."""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from html import escape as html_escape
from http.cookies import CookieError, SimpleCookie
from urllib.parse import quote, quote_plus, urljoin

import aiohttp
from bs4 import BeautifulSoup
from homeassistant.exceptions import HomeAssistantError
from yarl import URL

_LOGGER = logging.getLogger(__name__)

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
    """Drupal form login and balance parser."""

    def __init__(self, account: str, password: str) -> None:
        self._account = account
        self._password = password
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers=_BROWSER_HEADERS,
        )
        self._logged_in = False

    @staticmethod
    def _cookie_names(cookie_header: str | None) -> list[str]:
        """Return cookie names only; never return cookie values."""
        if not cookie_header:
            return []
        parsed = SimpleCookie()
        try:
            parsed.load(cookie_header)
        except (CookieError, TypeError, ValueError):
            return ["<unparsed-cookie-header>"]
        return sorted(parsed.keys())

    @staticmethod
    def _cookie_metadata(cookies) -> list[dict[str, str | bool | None]]:
        """Return cookie attributes while always hiding cookie values."""
        details = []
        for name, morsel in cookies.items():
            details.append(
                {
                    "name": name,
                    "value": "<redacted>",
                    "domain": morsel["domain"] or None,
                    "path": morsel["path"] or None,
                    "secure": bool(morsel["secure"]),
                    "httponly": bool(morsel["httponly"]),
                    "samesite": morsel["samesite"] or None,
                    "max_age": morsel["max-age"] or None,
                    "expires": morsel["expires"] or None,
                }
            )
        return details

    @staticmethod
    def _safe_form_payload(payload: dict[str, str]) -> dict[str, str]:
        """Expose ordinary form values but mask credentials and session tokens."""
        sensitive_fields = {
            "name",
            "pass",
            "password",
            "form_token",
            "csrf_token",
            "token",
            "access_token",
            "auth_token",
            "form_build_id",
        }
        safe = {}
        for name, value in payload.items():
            if name.lower() in sensitive_fields:
                safe[name] = f"<redacted; length={len(str(value))}>"
            else:
                safe[name] = str(value)
        return safe

    def _log_request(
        self,
        stage: str,
        method: str,
        url: str,
        payload: dict[str, str] | None = None,
    ) -> None:
        """Log request metadata and safe form fields, never secret values."""
        cookie_jar = self._session.cookie_jar.filter_cookies(URL(url))
        cookie_names = sorted(cookie_jar.keys())
        _LOGGER.debug(
            "TCE HTTP request: stage=%s method=%s url=%s "
            "outgoing_cookie_names=%s outgoing_cookie_attributes=%s "
            "form_payload=%s",
            stage,
            method,
            URL(url).with_query(None).with_fragment(None),
            cookie_names,
            self._cookie_metadata(cookie_jar),
            self._safe_form_payload(payload) if payload is not None else None,
        )

    def _log_response(
        self, stage: str, response: aiohttp.ClientResponse, body: str
    ) -> None:
        """Log HTTP diagnostics while redacting credentials and session data."""
        safe_headers = {
            key: (
                "<present; value redacted>"
                if key == "Location"
                else response.headers.get(key)
            )
            for key in (
                "Content-Type",
                "Content-Length",
                "Location",
                "Server",
                "Via",
            )
            if response.headers.get(key) is not None
        }

        response_cookie_names = sorted(response.cookies.keys())
        response_cookie_details = self._cookie_metadata(response.cookies)
        outgoing_cookie_names = self._cookie_names(
            response.request_info.headers.get("Cookie")
        )
        jar_cookies = self._session.cookie_jar.filter_cookies(response.url)
        jar_cookie_names = sorted(jar_cookies.keys())
        request_headers = {
            key: response.request_info.headers.get(key)
            for key in (
                "User-Agent",
                "Accept",
                "Accept-Language",
                "Origin",
                "Referer",
                "Content-Type",
                "Sec-Fetch-Dest",
                "Sec-Fetch-Mode",
                "Sec-Fetch-Site",
                "Sec-Fetch-User",
            )
            if response.request_info.headers.get(key) is not None
        }

        soup = BeautifulSoup(body, "html.parser")
        for node in soup.select("script, style, noscript, .info_column_primary"):
            node.decompose()
        for field in soup.select("input"):
            field.attrs.pop("value", None)
        for field in soup.select("textarea"):
            field.clear()

        text_preview = " ".join(soup.get_text(" ", strip=True).split())
        for secret, replacement in (
            (self._account, "<account-redacted>"),
            (self._password, "<password-redacted>"),
        ):
            if secret:
                for secret_variant in {
                    secret,
                    quote(secret),
                    quote_plus(secret),
                    html_escape(secret),
                }:
                    text_preview = text_preview.replace(
                        secret_variant, replacement
                    )

        # Redact JWTs and common token/credential values that may be echoed by the site.
        text_preview = re.sub(
            (
                r"(?i)\beyJ[A-Za-z0-9_-]{10,}\."
                r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
            ),
            "<token-redacted>",
            text_preview,
        )
        text_preview = re.sub(
            r"(?i)\b(token|csrf(?:_token)?|form_token|access_token|auth_token|"
            r"password|pass)\s*[:=]\s*"
            r"[^\s&,;]+",
            r"\1=<redacted>",
            text_preview,
        )
        text_preview = text_preview[:500]

        # Include the redirect path/statuses, but never query strings or fragments.
        redirects = [
            {
                "status": item.status,
                "url": str(item.url.with_query(None).with_fragment(None)),
            }
            for item in response.history
        ]
        log_method = _LOGGER.warning if response.status >= 400 else _LOGGER.debug
        log_method(
            "TCE HTTP %s: status=%s url=%s final_url=%s redirects=%s "
            "response_headers=%s request_headers=%s "
            "response_cookie_names=%s response_cookie_details=%s "
            "response_request_cookie_names=%s cookie_jar_names=%s "
            "cookie_jar_attributes=%s body_length=%s body_preview=%r",
            stage,
            response.status,
            response.request_info.real_url.with_query(None).with_fragment(None),
            response.url.with_query(None).with_fragment(None),
            redirects,
            safe_headers,
            request_headers,
            response_cookie_names,
            response_cookie_details,
            outgoing_cookie_names,
            jar_cookie_names,
            self._cookie_metadata(jar_cookies),
            len(body),
            text_preview,
        )

    def _ensure_browser_cookies(self) -> None:
        """Reproduce cookies set by the browser-side Drupal bootstrap."""
        current = self._session.cookie_jar.filter_cookies(URL(LOGIN_URL))
        missing: dict[str, str] = {}
        if "has_js" not in current:
            missing["has_js"] = "1"
        if "login_status" not in current:
            missing["login_status"] = "anonymous"
        if missing:
            self._session.cookie_jar.update_cookies(
                missing, response_url=URL(LOGIN_URL)
            )
            _LOGGER.debug(
                "TCE initialized missing browser cookies: %s",
                sorted(missing),
            )

    @staticmethod
    def _is_login_page(html: str) -> bool:
        return BeautifulSoup(html, "html.parser").select_one(
            "form#user-login-form"
        ) is not None

    async def _async_get_services_html(self, stage: str) -> tuple[str, str]:
        """Fetch services page and return its HTML and final URL."""
        self._log_request(stage, "GET", SERVICES_URL)
        async with self._session.get(
            SERVICES_URL, headers={"Referer": LOGIN_REFERER}
        ) as response:
            html = await response.text()
            self._log_response(stage, response, html)
            response.raise_for_status()
            return html, str(response.url)

    async def async_login(self) -> None:
        """Fetch Drupal login form, submit it, then verify the session."""
        stage = "GET login page"
        request_url = LOGIN_URL
        try:
            self._log_request(stage, "GET", LOGIN_URL)
            async with self._session.get(LOGIN_URL) as response:
                login_html = await response.text()
                self._log_response(stage, response, login_html)
                response.raise_for_status()

            self._ensure_browser_cookies()
            soup = BeautifulSoup(login_html, "html.parser")
            form = None
            for candidate in soup.select("form"):
                form_id_field = candidate.select_one(
                    'input[name="form_id"]'
                )
                if (
                    form_id_field is not None
                    and form_id_field.get("value") == "user_login_central_block"
                ):
                    form = candidate
                    break

            if form is None:
                available_form_ids = [
                    field.get("value")
                    for field in soup.select('form input[name="form_id"]')
                ]
                _LOGGER.warning(
                    "TCE expected login form was not found; available form_ids=%s",
                    available_form_ids,
                )
                raise HomeAssistantError(
                    "Expected central login form was not found"
                )

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
            _LOGGER.debug(
                "TCE login form: action=%s method=%s enctype=%s "
                "field_names=%s hidden_fields=%s",
                action.split("?", 1)[0],
                form.get("method"),
                form.get("enctype"),
                [field.get("name") for field in form.select("input[name]")],
                {
                    field.get("name"): self._safe_form_payload(
                        {field.get("name"): field.get("value", "")}
                    )[field.get("name")]
                    for field in form.select('input[type="hidden"][name]')
                },
            )
            self._log_request(stage, "POST", post_url, payload)
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
                self._log_response(stage, response, post_html)
                response.raise_for_status()

            if self._is_login_page(post_html):
                _LOGGER.warning(
                    "TCE authentication rejected: POST returned the login form "
                    "(HTTP %s)",
                    response.status,
                )
                raise TceCrimeaAuthError("Provider rejected the login form")

            stage = "GET services page after login"
            request_url = SERVICES_URL
            html, final_url = await self._async_get_services_html(stage)

            check = BeautifulSoup(html, "html.parser")
            if (
                "/services" not in final_url
                or check.select_one("form#user-login-form") is not None
            ):
                _LOGGER.warning(
                    "TCE authentication rejected: services page redirected "
                    "to login or returned a login form"
                )
                raise TceCrimeaAuthError("Provider login was rejected")
            if check.select_one(".info_column_primary") is None:
                raise HomeAssistantError("Subscriber information block was not found")

            self._logged_in = True
            _LOGGER.debug("TCE authentication completed successfully")
        except (TceCrimeaAuthError, HomeAssistantError):
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            detail = (
                f"HTTP {err.status}"
                if isinstance(err, aiohttp.ClientResponseError)
                else type(err).__name__
            )
            raise HomeAssistantError(
                f"Connection failed during {stage} ({detail}), URL: {request_url}"
            ) from err

    async def async_get_balance(self) -> Decimal:
        """Get balance from the authenticated services page."""
        if not self._logged_in:
            await self.async_login()
        try:
            html, final_url = await self._async_get_services_html(
                "GET services page"
            )
            page = BeautifulSoup(html, "html.parser")
            if (
                "/services" not in final_url
                or page.select_one("form#user-login-form") is not None
            ):
                _LOGGER.debug("TCE session expired; attempting to authenticate again")
                self._logged_in = False
                await self.async_login()
                html, final_url = await self._async_get_services_html(
                    "GET services page after re-login"
                )
                page = BeautifulSoup(html, "html.parser")
                if (
                    "/services" not in final_url
                    or page.select_one("form#user-login-form") is not None
                ):
                    _LOGGER.warning(
                        "TCE authentication failed: session could not be restored"
                    )
                    raise TceCrimeaAuthError(
                        "Provider session could not be restored"
                    )

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
            if (
                "состояние расчетов" not in label
                and "состояние расчётов" not in label
            ):
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
                (
                    r"(долг|задолженность|переплата)\s*"
                    r"([-+]?\d[\d\s]*(?:[,.]\d+)?)\s*"
                    r"(?:руб(?:\.|лей|ля)?|₽)"
                ),
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
