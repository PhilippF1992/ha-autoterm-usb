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
    REG_SOURCE_POWER,
    REG_SOURCE_TO_MODE,
)
from .coordinator import AutotermCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AutotermCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        AutotermPowerLevel(coordinator, entry),
        AutotermFanLevel(coordinator, entry),
    ])


class AutotermPowerLevel(CoordinatorEntity[AutotermCoordinator], NumberEntity):
    """
    Power level (1–9). Active in regulation mode "Power level" (mode=0x04).

    When the heater is running in power mode, changing this sends a settings
    write (0x02) to update the power level immediately.
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
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> float:
        return float(self.coordinator.power_level)

    async def async_set_native_value(self, value: float) -> None:
        level = int(round(value))
        level = max(POWER_LEVEL_MIN, min(POWER_LEVEL_MAX, level))
        self.coordinator.power_level = level
        self.async_write_ha_state()

        # Only push to heater when running in power mode
        if self.coordinator.reg_source == REG_SOURCE_POWER:
            ok = await self.coordinator.client.send_write_settings(
                mode=REG_SOURCE_TO_MODE[REG_SOURCE_POWER],
                setpoint=int(round(self.coordinator.target_temp)),
                ventilation=0,
                power_level=level,
            )
            if not ok:
                _LOGGER.warning("Failed to update power level to %d", level)
        else:
            _LOGGER.debug(
                "Power level stored as %d; will apply on next start in power mode", level
            )


class AutotermFanLevel(CoordinatorEntity[AutotermCoordinator], NumberEntity):
    """
    Fan speed for FAN_ONLY (ventilation) mode (1–9).

    This is used as the fan_level parameter for the 0x23 FAN_ONLY command.
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
        return self.coordinator.last_update_success

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
            # Heater is running in ventilation mode — re-send 0x23 with the new speed.
            # Fan speed is embedded in the FAN_ONLY frame; there is no separate command.
            _LOGGER.info("Fan level changed to %d while venting — re-sending FAN_ONLY", level)
            ok = await self.coordinator.client.send_fan_only(level)
            if not ok:
                _LOGGER.warning("Failed to update fan level to %d", level)
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.debug("Fan level stored as %d; applies on next FAN_ONLY command", level)
