"""Config flow for ЖКХ."""

from __future__ import annotations

import hashlib
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_PROVIDER,
    CONF_UPDATE_DAY,
    DEFAULT_UPDATE_DAY,
    DOMAIN,
    PROVIDER_TCE,
    PROVIDER_TCE_NAME,
)
from .connectors.tce_crimea import TceCrimeaAuthError, TceCrimeaClient

_LOGGER = logging.getLogger(__name__)


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
            account_key = hashlib.sha256(account.encode("utf-8")).hexdigest()[:20]
            await self.async_set_unique_id(f"{PROVIDER_TCE}_{account_key}")
            self._abort_if_unique_id_configured()

            client = TceCrimeaClient(account, user_input[CONF_PASSWORD])
            try:
                await client.async_login()
                await client.async_get_balance()
            except TceCrimeaAuthError:
                errors["base"] = "invalid_auth"
            except HomeAssistantError as err:
                _LOGGER.warning(
                    "Provider validation failed (%s): %s",
                    type(err).__name__,
                    str(err),
                )
                errors["base"] = "cannot_connect"
            except Exception as err:  # noqa: BLE001
                _LOGGER.error(
                    "Unexpected provider validation error (%s)",
                    type(err).__name__,
                )
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

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return ZhkhOptionsFlow()


class ZhkhOptionsFlow(config_entries.OptionsFlowWithReload):
    """Configure the monthly update day."""

    async def async_step_init(self, user_input=None):
        """Manage integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_DAY,
                    default=self.config_entry.options.get(
                        CONF_UPDATE_DAY, DEFAULT_UPDATE_DAY
                    ),
                ): vol.In(list(range(1, 32)))
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
