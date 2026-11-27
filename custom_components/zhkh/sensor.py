"""Sensors for ЖКХ."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PROVIDER_TCE_NAME
from .coordinator import ZhkhCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    coordinator: ZhkhCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ProviderSensor(coordinator, entry),
            BalanceSensor(coordinator, entry),
            LastUpdateSensor(coordinator, entry),
        ]
    )


class ZhkhBaseSensor(CoordinatorEntity, SensorEntity):
    """Base entity."""

    def __init__(self, coordinator, entry, suffix: str) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="ЖКХ",
        )
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"


class ProviderSensor(ZhkhBaseSensor):
    """Provider name sensor."""

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "provider")
        self._attr_name = "Поставщик"
        self._attr_icon = "mdi:domain"

    @property
    def native_value(self):
        return PROVIDER_TCE_NAME


class BalanceSensor(ZhkhBaseSensor):
    """Debt amount sensor."""

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "balance")
        self._attr_name = "Задолженность"
        self._attr_native_unit_of_measurement = "RUB"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get("balance") if self.coordinator.data else None


class LastUpdateSensor(ZhkhBaseSensor):
    """Timestamp of the last successful data update."""

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "last_update")
        self._attr_name = "Последнее обновление"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:clock-check-outline"

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("last_updated")
        return self.coordinator._last_updated
