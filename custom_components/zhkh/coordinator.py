"""Data update coordinator."""

from __future__ import annotations

import calendar
import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .connectors.tce_crimea import TceCrimeaClient
from .const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_UPDATE_DAY,
    DEFAULT_UPDATE_DAY,
    DOMAIN,
    UPDATE_HOUR,
)

_LOGGER = logging.getLogger(__name__)


class ZhkhCoordinator(DataUpdateCoordinator):
    """Coordinate updates for one utility account."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=None,
        )
        self.entry = entry
        self.client = TceCrimeaClient(
            entry.data[CONF_ACCOUNT], entry.data[CONF_PASSWORD]
        )
        self.update_day = int(
            entry.options.get(CONF_UPDATE_DAY, DEFAULT_UPDATE_DAY)
        )
        self._store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}")
        self._last_updated: datetime | None = None
        self._last_scheduled_period: str | None = None
        self._unsub_time_change = None

    async def async_initialize(self) -> None:
        """Restore persisted update metadata."""
        stored = await self._store.async_load() or {}
        last_updated = stored.get("last_updated")
        if isinstance(last_updated, str):
            self._last_updated = dt_util.parse_datetime(last_updated)
        self._last_scheduled_period = stored.get("last_scheduled_period")

    async def _async_save_metadata(self) -> None:
        """Persist the last successful update and scheduled month."""
        await self._store.async_save(
            {
                "last_updated": (
                    self._last_updated.isoformat() if self._last_updated else None
                ),
                "last_scheduled_period": self._last_scheduled_period,
            }
        )

    async def _async_update_data(self):
        try:
            balance = await self.client.async_get_balance()
        except Exception as err:
            raise UpdateFailed(
                f"Не удалось получить задолженность: {err}"
            ) from err

        self._last_updated = dt_util.now()
        try:
            await self._async_save_metadata()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Не удалось сохранить дату обновления ЖКХ")

        return {
            "balance": balance,
            "last_updated": self._last_updated,
        }

    def async_start_schedule(self) -> None:
        """Start the daily scheduler and catch up after a missed schedule."""
        self._unsub_time_change = async_track_time_change(
            self.hass,
            self._handle_time_change,
            hour=UPDATE_HOUR,
            minute=0,
            second=0,
        )
        self.hass.async_create_task(self._async_check_schedule(startup=True))

    @callback
    def _handle_time_change(self, now: datetime) -> None:
        """Check the monthly schedule at 06:00 local Home Assistant time."""
        self.hass.async_create_task(self._async_check_schedule())

    async def _async_check_schedule(self, startup: bool = False) -> None:
        """Run the monthly update once, including after downtime."""
        now = dt_util.now()
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        scheduled_day = min(self.update_day, days_in_month)

        if now.day >= scheduled_day:
            period_date = now
        else:
            # If HA was offline across the month boundary, catch up the
            # previous month's missed run after the next startup.
            year = now.year
            month = now.month - 1
            if month == 0:
                year -= 1
                month = 12
            period_date = now.replace(year=year, month=month, day=1)

        period = period_date.strftime("%Y-%m")
        if self._last_scheduled_period == period:
            return

        # A successful initial refresh also satisfies a missed monthly run.
        if startup and self.last_update_success:
            self._last_scheduled_period = period
            try:
                await self._async_save_metadata()
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Не удалось сохранить расписание обновления ЖКХ")
            return

        # Save before requesting data to prevent duplicate attempts after restart.
        self._last_scheduled_period = period
        try:
            await self._async_save_metadata()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Не удалось сохранить расписание обновления ЖКХ")

        await self.async_refresh()

    async def async_close(self) -> None:
        """Stop scheduled callbacks and close the provider session."""
        if self._unsub_time_change:
            self._unsub_time_change()
            self._unsub_time_change = None
        await self.client.async_close()
