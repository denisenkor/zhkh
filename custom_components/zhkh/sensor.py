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

from .const import (
    DOMAIN,
    PROVIDER_CRIMEA_ENERGY,
    PROVIDER_TCE_NAME,
)
from .coordinator import ZhkhCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up provider sensors."""
    coordinator: ZhkhCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.provider == PROVIDER_CRIMEA_ENERGY:
        entities = []
        for bill in (coordinator.data or {}).get("bills", []):
            entities.extend(CrimeaEnergyBillSensors(coordinator, entry, bill).entities)
        async_add_entities(entities)
        return

    async_add_entities(
        [
            ProviderSensor(coordinator, entry),
            BalanceSensor(coordinator, entry),
            LastUpdateSensor(coordinator, entry),
        ]
    )


class ZhkhBaseSensor(CoordinatorEntity, SensorEntity):
    """Base entity for a single-account provider."""

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


class CrimeaEnergyBillSensors:
    """Create a separate device and sensors for one personal account."""

    def __init__(self, coordinator, entry, bill: dict) -> None:
        account_id = str(bill["id"])
        device = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{account_id}")},
            name=f"{bill['name']} ({bill['account']})",
            manufacturer="Крымэнерго",
            model="Лицевой счёт",
            configuration_url="https://lk.crimea-energy.ru",
        )
        prefix = f"{entry.entry_id}_{account_id}"
        self.entities = [
            CrimeaEnergyProviderSensor(coordinator, bill, device, f"{prefix}_provider"),
            CrimeaEnergyAccountSensor(coordinator, bill, device, f"{prefix}_account"),
            CrimeaEnergyBalanceSensor(coordinator, bill, device, f"{prefix}_balance"),
            CrimeaEnergyLastUpdateSensor(coordinator, device, f"{prefix}_last_update"),
        ]


class CrimeaEnergyBillEntity(CoordinatorEntity, SensorEntity):
    """Base entity for a Crimea Energy personal account."""

    def __init__(self, coordinator, bill, device, unique_id: str) -> None:
        super().__init__(coordinator)
        self._bill_id = str(bill["id"])
        self._attr_device_info = device
        self._attr_unique_id = unique_id

    @property
    def bill(self):
        """Return the latest version of this bill."""
        for item in (self.coordinator.data or {}).get("bills", []):
            if str(item["id"]) == self._bill_id:
                return item
        return None


class CrimeaEnergyProviderSensor(CrimeaEnergyBillEntity):
    """Supplier / regional office name."""

    def __init__(self, coordinator, bill, device, unique_id) -> None:
        super().__init__(coordinator, bill, device, unique_id)
        self._attr_name = "Поставщик"
        self._attr_icon = "mdi:domain"

    @property
    def native_value(self):
        return self.bill.get("name") if self.bill else None


class CrimeaEnergyAccountSensor(CrimeaEnergyBillEntity):
    """Personal account number."""

    def __init__(self, coordinator, bill, device, unique_id) -> None:
        super().__init__(coordinator, bill, device, unique_id)
        self._attr_name = "Лицевой счёт"
        self._attr_icon = "mdi:card-account-details-outline"

    @property
    def native_value(self):
        return self.bill.get("account") if self.bill else None


class CrimeaEnergyBalanceSensor(CrimeaEnergyBillEntity):
    """Current account balance as returned by the provider."""

    def __init__(self, coordinator, bill, device, unique_id) -> None:
        super().__init__(coordinator, bill, device, unique_id)
        self._attr_name = "Баланс"
        self._attr_native_unit_of_measurement = "RUB"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:cash"

    @property
    def native_value(self):
        return self.bill.get("balance") if self.bill else None

    @property
    def extra_state_attributes(self):
        if not self.bill:
            return None
        return {
            "address": self.bill.get("address"),
            "serial": self.bill.get("serial"),
            "provider_update": self.bill.get("provider_update"),
            "penalty": self.bill.get("penalty"),
        }


class CrimeaEnergyLastUpdateSensor(CrimeaEnergyBillEntity):
    """Timestamp of the last successful API synchronization."""

    def __init__(self, coordinator, device, unique_id) -> None:
        # A lightweight placeholder bill is sufficient for the base class.
        super().__init__(coordinator, {"id": unique_id}, device, unique_id)
        self._bill_id = None
        self._attr_name = "Последнее обновление"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:clock-check-outline"

    @property
    def bill(self):
        return {}

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("last_updated")
        return self.coordinator._last_updated
