"""Select entity for Autoterm USB — regulation source."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    REG_SOURCE_OPTIONS,
    REG_SOURCE_TO_MODE,
)
from .coordinator import AutotermCoordinator

_LOGGER = logging.getLogger(__name__)

_OPTION_LABELS: dict[str, str] = {
    "internal": "Internal sensor",
    "panel": "Panel (HA-fed)",
    "external": "External sensor",
    "power": "Power level",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AutotermCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AutotermRegSourceSelect(coordinator, entry)])


class AutotermRegSourceSelect(CoordinatorEntity[AutotermCoordinator], SelectEntity):
    """Select entity for regulation source (internal / panel / external / power)."""

    _attr_has_entity_name = True
    _attr_name = "Regulate by"
    _attr_options = REG_SOURCE_OPTIONS
    _attr_icon = "mdi:thermometer-lines"

    def __init__(self, coordinator: AutotermCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_reg_source"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.unique_id or self._entry.entry_id)},
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def current_option(self) -> str:
        return self.coordinator.reg_source

    async def async_select_option(self, option: str) -> None:
        if option not in REG_SOURCE_OPTIONS:
            _LOGGER.error("Invalid regulation source option: %s", option)
            return

        mode = REG_SOURCE_TO_MODE[option]
        _LOGGER.info(
            "Setting regulation source to %s (mode=0x%02x)",
            _OPTION_LABELS.get(option, option),
            mode,
        )

        ok = await self.coordinator.client.send_write_settings(
            mode=mode,
            setpoint=int(round(self.coordinator.target_temp)),
            ventilation=0,
            power_level=self.coordinator.power_level,
        )
        if ok:
            self.coordinator.reg_source = option
            self.async_write_ha_state()
        else:
            _LOGGER.warning("Failed to set regulation source to %s", option)
