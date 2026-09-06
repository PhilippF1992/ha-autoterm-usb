"""Number entities for Autoterm USB — power level and fan speed."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    POWER_LEVEL_MAX,
    POWER_LEVEL_MIN,
    PRESET_BY_POWER,
    START_MODE_BY_POWER,
)
from .coordinator import AutotermCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AutotermCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            AutotermPowerLevel(coordinator, entry),
            AutotermFanLevel(coordinator, entry),
        ]
    )


class AutotermPowerLevel(CoordinatorEntity[AutotermCoordinator], NumberEntity):
    """
    Power level (1–9). Active only in "By Power" heating preset.

    When the heater is running in power mode, changing this sends a settings
    write (0x02) to update the power level immediately.
    Only available when heating_preset == "By Power".
    """

    _attr_has_entity_name = True
    _attr_name = "Power Level"
    _attr_native_min_value = POWER_LEVEL_MIN
    _attr_native_max_value = POWER_LEVEL_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:fire"

    def __init__(self, coordinator: AutotermCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_power_level"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.unique_id or self._entry.entry_id)},
        )

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.coordinator.heating_preset == PRESET_BY_POWER
        )

    @property
    def native_value(self) -> float:
        return float(self.coordinator.power_level)

    async def async_set_native_value(self, value: float) -> None:
        level = int(round(value))
        level = max(POWER_LEVEL_MIN, min(POWER_LEVEL_MAX, level))
        self.coordinator.power_level = level
        self.async_write_ha_state()

        if self.coordinator.heating_preset == PRESET_BY_POWER:
            ok = await self.coordinator.client.send_write_settings(
                mode=START_MODE_BY_POWER,
                setpoint=int(round(self.coordinator.target_temp)),
                ventilation=0,
                power_level=level,
            )
            if not ok:
                _LOGGER.warning("Failed to update power level to %d", level)
        else:
            _LOGGER.debug("Power level stored as %d; will apply on next start in power mode", level)


class AutotermFanLevel(CoordinatorEntity[AutotermCoordinator], NumberEntity):
    """
    Fan speed for FAN_ONLY (ventilation) mode (1–9).

    Only available when the heater is actively running in fan-only mode.
    Changing the value re-sends the 0x23 FAN_ONLY command so the heater
    picks up the new speed immediately (no separate set-speed command exists).
    PORTED-BUT-UNVERIFIED: the 0x23 command itself is not yet confirmed on the 44D.
    """

    _attr_has_entity_name = True
    _attr_name = "Fan Speed"
    _attr_native_min_value = POWER_LEVEL_MIN
    _attr_native_max_value = POWER_LEVEL_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:fan"

    def __init__(self, coordinator: AutotermCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_fan_level"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.unique_id or self._entry.entry_id)},
        )

    @property
    def available(self) -> bool:
        st = self.coordinator.data
        return self.coordinator.last_update_success and st is not None and st.is_fan_only

    @property
    def native_value(self) -> float:
        return float(self.coordinator.fan_level)

    async def async_set_native_value(self, value: float) -> None:
        level = int(round(value))
        level = max(POWER_LEVEL_MIN, min(POWER_LEVEL_MAX, level))
        self.coordinator.fan_level = level
        self.async_write_ha_state()

        st = self.coordinator.data
        if st is not None and st.is_fan_only:
            _LOGGER.info("Fan level changed to %d while venting — re-sending FAN_ONLY", level)
            ok = await self.coordinator.client.send_fan_only(level)
            if not ok:
                _LOGGER.warning("Failed to update fan level to %d", level)
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.debug("Fan level stored as %d; applies on next FAN_ONLY command", level)
