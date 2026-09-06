"""Select entity for Autoterm USB — temperature source."""

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
    TEMP_SOURCE_EXTERNAL,
    TEMP_SOURCE_HA_SENSOR,
    TEMP_SOURCE_INTERNAL,
)
from .coordinator import AutotermCoordinator

_LOGGER = logging.getLogger(__name__)

_VALID_OPTIONS = frozenset({TEMP_SOURCE_INTERNAL, TEMP_SOURCE_EXTERNAL, TEMP_SOURCE_HA_SENSOR})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AutotermCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AutotermTempSourceSelect(coordinator, entry)])


class AutotermTempSourceSelect(CoordinatorEntity[AutotermCoordinator], SelectEntity):
    """
    Select entity for the temperature source used in "By Temperature" preset.

    Offers Internal / External / HA sensor. The "External" option is only present
    when the heater reports an external sensor fitted (ext_temp_present). Changing
    source requires no wire command — the coordinator injection loop picks up the
    new value on the next poll cycle.

    Only available when heating_preset == "By Temperature".
    """

    _attr_has_entity_name = True
    _attr_name = "Temperature Source"
    _attr_icon = "mdi:thermometer-lines"

    def __init__(self, coordinator: AutotermCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_temp_source"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.unique_id or self._entry.entry_id)},
        )

    @property
    def options(self) -> list[str]:
        opts = [TEMP_SOURCE_INTERNAL, TEMP_SOURCE_HA_SENSOR]
        st = self.coordinator.data
        if st is not None and st.ext_temp_present:
            opts.insert(1, TEMP_SOURCE_EXTERNAL)
        return opts

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def current_option(self) -> str:
        src = self.coordinator.temp_source
        # Guard: if external was selected but the sensor has since disappeared, show internal.
        if src == TEMP_SOURCE_EXTERNAL:
            st = self.coordinator.data
            if st is None or not st.ext_temp_present:
                return TEMP_SOURCE_INTERNAL
        return src

    async def async_select_option(self, option: str) -> None:
        if option not in _VALID_OPTIONS:
            _LOGGER.error("Invalid temperature source option: %s", option)
            return
        self.coordinator.temp_source = option
        self.async_write_ha_state()
        # No wire command: coordinator injection loop applies the new source on next poll
        _LOGGER.info("Temperature source set to '%s'", option)
