"""Data update coordinator."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .connectors.tce_crimea import TceCrimeaClient
from .const import CONF_ACCOUNT, CONF_PASSWORD, DOMAIN, UPDATE_INTERVAL


class ZhkhCoordinator(DataUpdateCoordinator):
    """Coordinate updates for one utility account."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = TceCrimeaClient(
            entry.data[CONF_ACCOUNT], entry.data[CONF_PASSWORD]
        )

    async def _async_update_data(self):
        try:
            return {"balance": await self.client.async_get_balance()}
        except Exception as err:
            raise UpdateFailed(f"Не удалось получить задолженность: {err}") from err

    async def async_close(self) -> None:
        await self.client.async_close()
