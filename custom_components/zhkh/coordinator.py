"""Data update coordinator."""

from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import datetime
from decimal import Decimal

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .connectors.tce_crimea import TceCrimeaClient, TceCrimeaConnectionError
from .const import (
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_UPDATE_DAY,
    DEFAULT_UPDATE_DAY,
    DOMAIN,
    UPDATE_HOUR,
)

_LOGGER = logging.getLogger(__name__)

_UPDATE_ATTEMPTS = 3
_RETRY_DELAY = 10


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
        """Restore cached balance and update metadata without contacting provider."""
        stored = await self._store.async_load() or {}
        last_updated = stored.get("last_updated")
        if isinstance(last_updated, str):
            self._last_updated = dt_util.parse_datetime(last_updated)

        self._last_scheduled_period = stored.get("last_scheduled_period")
        balance = stored.get("balance")
        if self._last_updated is not None:
            self.data = {
                "balance": Decimal(str(balance)) if balance is not None else None,
                "last_updated": self._last_updated,
            }

    async def _async_save_metadata(self) -> None:
        """Persist balance and update metadata."""
        await self._store.async_save(
            {
                "balance": (
                    str(self.data["balance"])
                    if self.data and self.data.get("balance") is not None
                    else None
                ),
                "last_updated": (
                    self._last_updated.isoformat() if self._last_updated else None
                ),
                "last_scheduled_period": self._last_scheduled_period,
            }
        )

    async def _async_update_data(self):
        for attempt in range(1, _UPDATE_ATTEMPTS + 1):
            try:
                balance = await self.client.async_get_balance()
                break
            except TceCrimeaConnectionError as err:
                if attempt >= _UPDATE_ATTEMPTS:
                    raise UpdateFailed(
                        f"Не удалось получить задолженность после {_UPDATE_ATTEMPTS} попыток: {err}"
                    ) from err

                _LOGGER.warning(
                    "Ошибка соединения при получении задолженности "
                    "(попытка %s из %s). Повтор через %s секунд: %s",
                    attempt,
                    _UPDATE_ATTEMPTS,
                    _RETRY_DELAY,
                    err,
                )
                await asyncio.sleep(_RETRY_DELAY)
            except HomeAssistantError as err:
                raise UpdateFailed(
                    f"Не удалось получить задолженность: {err}"
                ) from err
            except Exception as err:  # noqa: BLE001
                raise UpdateFailed(
                    "Не удалось получить задолженность "
                    f"из-за внутренней ошибки ({type(err).__name__})."
                ) from err

        self._last_updated = dt_util.now()
        result = {
            "balance": balance,
            "last_updated": self._last_updated,
        }
        # Keep a usable cache across Home Assistant restarts.
        try:
            await self._store.async_save(
                {
                    "balance": str(balance),
                    "last_updated": self._last_updated.isoformat(),
                    "last_scheduled_period": self._last_scheduled_period,
                }
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Не удалось сохранить данные ЖКХ для восстановления после перезапуска")

        return result

    async def async_start_schedule(self) -> None:
        """Start the monthly scheduler; startup itself never triggers an update."""
        self._unsub_time_change = async_track_time_change(
            self.hass,
            self._handle_time_change,
            hour=UPDATE_HOUR,
            minute=0,
            second=0,
        )

    @callback
    def _handle_time_change(self, now: datetime) -> None:
        """Check the monthly schedule at 06:00 local Home Assistant time."""
        self.hass.async_create_task(self._async_check_schedule(now))

    async def _async_check_schedule(self, now: datetime | None = None) -> None:
        """Update only on the configured calendar day at 06:00."""
        now = now or dt_util.now()
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        scheduled_day = min(self.update_day, days_in_month)

        if now.day != scheduled_day:
            return

        period = now.strftime("%Y-%m")
        if self._last_scheduled_period == period:
            return

        await self.async_refresh()
        if self.last_update_success:
            self._last_scheduled_period = period
            try:
                await self._async_save_metadata()
            except Exception:  # noqa: BLE001
                _LOGGER.warning("Не удалось сохранить расписание обновления ЖКХ")

    async def async_close(self) -> None:
        """Stop scheduled callbacks and close the provider session."""
        if self._unsub_time_change:
            self._unsub_time_change()
            self._unsub_time_change = None
        await self.client.async_close()
