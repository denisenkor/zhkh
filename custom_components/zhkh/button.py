"""Buttons for ЖКХ."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .coordinator import ZhkhCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    coordinator: ZhkhCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RefreshButton(coordinator, entry)])


class RefreshButton(ButtonEntity):
    """Manually refresh provider data."""

    _attr_name = "Обновить данные"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: ZhkhCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="ЖКХ",
        )

    async def async_press(self) -> None:
        """Request an immediate update."""
        await self.coordinator.async_refresh()
