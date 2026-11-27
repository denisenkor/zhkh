"""Buttons for ЖКХ."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    DOMAIN,
    PROVIDER_CRIMEA_ENERGY,
    PROVIDER_CRIMEAGASNET,
    PROVIDER_CRIMEAGASNET_NAME,
    PROVIDER_CRIMEA_ECOR,
    PROVIDER_CRIMEA_ECOR_NAME,
    PROVIDER_EISGKH,
    PROVIDER_EISGKH_NAME,
)
from .coordinator import ZhkhCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Set up refresh buttons."""
    coordinator: ZhkhCoordinator = hass.data[DOMAIN][entry.entry_id]

    if coordinator.provider == PROVIDER_CRIMEA_ENERGY:
        # Remove the legacy account-wide button and its now-obsolete device.
        entity_registry = er.async_get(hass)
        legacy_entity_id = entity_registry.async_get_entity_id(
            "button", DOMAIN, f"{entry.entry_id}_refresh"
        )
        if legacy_entity_id:
            entity_registry.async_remove(legacy_entity_id)

        device_registry = dr.async_get(hass)
        legacy_device = device_registry.async_get_device(
            identifiers={(DOMAIN, entry.entry_id)},
            connections=set(),
        )
        if legacy_device:
            device_registry.async_remove_device(legacy_device.id)

        entities = []
        for bill in (coordinator.data or {}).get("bills", []):
            account_id = str(bill["id"])
            entities.append(
                RefreshButton(
                    coordinator,
                    f"{entry.entry_id}_{account_id}_refresh",
                    DeviceInfo(
                        identifiers={(DOMAIN, f"{entry.entry_id}_{account_id}")},
                        name=f"{bill['name']} ({bill['account']})",
                        manufacturer="Крымэнерго",
                        model="Лицевой счёт",
                        configuration_url="https://lk.crimea-energy.ru",
                    ),
                )
            )
        async_add_entities(entities)
        return

    if coordinator.provider == PROVIDER_CRIMEA_ECOR:
        async_add_entities(
            [
                RefreshButton(
                    coordinator,
                    f"{entry.entry_id}_refresh",
                    DeviceInfo(
                        identifiers={(DOMAIN, entry.entry_id)},
                        name=entry.title,
                        manufacturer=PROVIDER_CRIMEA_ECOR_NAME,
                        model="Лицевой счёт",
                        configuration_url="https://lk.crimea-ecor.ru/index.htm",
                    ),
                )
            ]
        )
        return

    if coordinator.provider == PROVIDER_EISGKH:
        async_add_entities(
            [
                RefreshButton(
                    coordinator,
                    f"{entry.entry_id}_refresh",
                    DeviceInfo(
                        identifiers={(DOMAIN, entry.entry_id)},
                        name=entry.title,
                        manufacturer=PROVIDER_EISGKH_NAME,
                        model="Лицевой счёт",
                        configuration_url="https://lk091.eisgkh.ru/index.htm",
                    ),
                )
            ]
        )
        return

    if coordinator.provider == PROVIDER_CRIMEAGASNET:
        entities = []
        for account in (coordinator.data or {}).get("accounts", []):
            account_id = str(account["id"])
            entities.append(
                RefreshButton(
                    coordinator,
                    f"{entry.entry_id}_{account_id}_refresh",
                    DeviceInfo(
                        identifiers={(DOMAIN, f"{entry.entry_id}_{account_id}")},
                        name=f"Лицевой счёт {account['account']}",
                        manufacturer=PROVIDER_CRIMEAGASNET_NAME,
                        model="Лицевой счёт",
                        configuration_url="https://lkk.crimeagasnet.ru",
                    ),
                )
            )
        async_add_entities(entities)
        return

    async_add_entities(
        [
            RefreshButton(
                coordinator,
                f"{entry.entry_id}_refresh",
                DeviceInfo(
                    identifiers={(DOMAIN, entry.entry_id)},
                    name=entry.title,
                    manufacturer="ЖКХ",
                ),
            )
        ]
    )


class RefreshButton(ButtonEntity):
    """Manually refresh provider data."""

    _attr_name = "Обновить данные"
    _attr_icon = "mdi:refresh"

    def __init__(
        self,
        coordinator: ZhkhCoordinator,
        unique_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        """Request an immediate update."""
        await self.coordinator.async_refresh()
