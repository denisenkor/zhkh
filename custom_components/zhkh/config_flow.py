"""Config flow for ЖКХ."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_PROVIDER,
    DOMAIN,
    PROVIDER_TCE,
    PROVIDER_TCE_NAME,
)
from .connectors.tce_crimea import TceCrimeaClient


class ZhkhConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ЖКХ."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Select provider."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["tce_crimea"],
        )

    async def async_step_tce_crimea(self, user_input=None):
        """Configure a Crimea heating account."""
        errors = {}
        if user_input is not None:
            account = user_input[CONF_ACCOUNT].strip()
            await self.async_set_unique_id(f"{PROVIDER_TCE}_{account}")
            self._abort_if_unique_id_configured()

            client = TceCrimeaClient(account, user_input[CONF_PASSWORD])
            try:
                await client.async_login()
                await client.async_get_balance()
            except (HomeAssistantError, Exception):
                errors["base"] = "cannot_connect"
            finally:
                await client.async_close()

            if not errors:
                return self.async_create_entry(
                    title=f"{PROVIDER_TCE_NAME} ({account})",
                    data={
                        CONF_PROVIDER: PROVIDER_TCE,
                        CONF_ACCOUNT: account,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_ACCOUNT): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="tce_crimea",
            data_schema=schema,
            errors=errors,
            description_placeholders={"provider": PROVIDER_TCE_NAME},
        )
