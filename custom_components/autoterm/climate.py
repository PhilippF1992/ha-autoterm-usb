"""Climate entity for the Autoterm USB heater."""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .codec import HeaterStatus
from .const import (
    CLIMATE_TEMP_MAX,
    CLIMATE_TEMP_MIN,
    CLIMATE_TEMP_STEP,
    COMMAND_DEBOUNCE,
    DOMAIN,
    FAULT_RETRYABLE,
    PRESET_BY_POWER,
    PRESET_BY_TEMP,
    START_MODE_BY_CONTROLLER_TEMP,
    START_MODE_BY_POWER,
)
from .coordinator import AutotermCoordinator

_LOGGER = logging.getLogger(__name__)


def _hvac_action(status: HeaterStatus | None) -> HVACAction:
    if status is None:
        return HVACAction.OFF
    s1 = status.status1
    if s1 == 0:
        return HVACAction.IDLE
    if s1 in (1, 2):
        return HVACAction.PREHEATING
    if s1 == 3:
        if status.is_fan_only:
            return HVACAction.FAN
        return HVACAction.HEATING
    if s1 == 4:
        return HVACAction.COOLING
    return HVACAction.IDLE


def _hvac_mode(status: HeaterStatus | None) -> HVACMode:
    if status is None or status.is_idle:
        return HVACMode.OFF
    if status.is_fan_only:
        return HVACMode.FAN_ONLY
    return HVACMode.HEAT


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AutotermCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AutotermClimate(coordinator, entry)])


class AutotermClimate(CoordinatorEntity[AutotermCoordinator], ClimateEntity):
    """Climate entity representing the Autoterm USB."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.FAN_ONLY]
    _attr_preset_modes = [PRESET_BY_TEMP, PRESET_BY_POWER]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = CLIMATE_TEMP_STEP
    _attr_min_temp = CLIMATE_TEMP_MIN
    _attr_max_temp = CLIMATE_TEMP_MAX

    def __init__(self, coordinator: AutotermCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_climate"
        self._last_command = 0.0

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.unique_id or self._entry.entry_id)},
            name=self._entry.title,
            manufacturer="Autoterm",
            model="Air 4D (Planar 44D)",
        )

    @property
    def supported_features(self) -> ClimateEntityFeature:
        base = (
            ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.PRESET_MODE
        )
        if self.coordinator.heating_preset == PRESET_BY_TEMP:
            return base | ClimateEntityFeature.TARGET_TEMPERATURE
        return base

    @property
    def preset_mode(self) -> str:
        return self.coordinator.heating_preset

    @property
    def target_temperature(self) -> float:
        return self.coordinator.target_temp

    @property
    def hvac_mode(self) -> HVACMode:
        return _hvac_mode(self.coordinator.data)

    @property
    def hvac_action(self) -> HVACAction:
        return _hvac_action(self.coordinator.data)

    @property
    def current_temperature(self) -> float | None:
        """
        Return the current temperature.

        In "By Temperature" preset, returns the same value being fed to the heater
        as panel temp — so the card always shows what's actually driving regulation.
        Falls back to heater internal sensor when the configured source is unavailable.
        In other modes, returns the heater's internal sensor reading directly.
        """
        if self.coordinator.heating_preset == PRESET_BY_TEMP:
            return self.coordinator.get_displayed_temp()
        st = self.coordinator.data
        return float(st.heater_temp) if st else None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self.coordinator.data
        attrs: dict[str, Any] = {
            "heating_preset": self.coordinator.heating_preset,
            "temp_source": self.coordinator.temp_source,
            "power_level": self.coordinator.power_level,
        }
        if st is not None:
            attrs["state_name"] = st.state_name
            attrs["status1"] = st.status1
            attrs["status2"] = st.status2
            attrs["error_code"] = st.error
            if st.is_lockout:
                attrs["lockout"] = True
                attrs["lockout_info"] = "Manual unlock procedure required (see manual)"
        return attrs

    def _debounced(self) -> bool:
        elapsed = time.monotonic() - self._last_command
        if elapsed < COMMAND_DEBOUNCE:
            _LOGGER.warning(
                "Command ignored — last command was %.1f s ago (debounce=%d s)",
                elapsed,
                COMMAND_DEBOUNCE,
            )
            return True
        return False

    def _is_active(self) -> bool:
        """True when the heater is running, starting, or stopping (not idle/off)."""
        st = self.coordinator.data
        return st is not None and (st.is_running or st.is_starting or st.is_stopping)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in (PRESET_BY_TEMP, PRESET_BY_POWER):
            return
        if self._is_active():
            _LOGGER.warning("Cannot change preset while heater is active; send OFF first")
            return
        self.coordinator.heating_preset = preset_mode
        self.async_write_ha_state()
        mode = (
            START_MODE_BY_CONTROLLER_TEMP if preset_mode == PRESET_BY_TEMP else START_MODE_BY_POWER
        )
        await self.coordinator.client.send_write_settings(
            mode=mode,
            setpoint=int(round(self.coordinator.target_temp)),
            ventilation=0,
            power_level=self.coordinator.power_level,
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if self._debounced():
            return

        st = self.coordinator.data

        if hvac_mode == HVACMode.OFF:
            await self._async_stop()
            return

        # Block mode changes while the heater is active
        if self._is_active():
            _LOGGER.warning("Cannot switch HVAC mode while heater is active; send OFF first")
            return

        if hvac_mode == HVACMode.HEAT:
            if st and st.is_fault and st.error not in FAULT_RETRYABLE and not st.is_lockout:
                _LOGGER.warning(
                    "Cannot start: active fault code %d (%s). Clear the fault before restarting.",
                    st.error,
                    st.state_name,
                )
                return
            if st and st.is_lockout:
                _LOGGER.error(
                    "Cannot start: heater is in LOCKOUT (code 33). "
                    "Perform the manual unlock procedure first."
                )
                return
            await self._async_start_heat()

        elif hvac_mode == HVACMode.FAN_ONLY:
            await self._async_start_fan_only()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        temp = float(temp)
        temp = max(CLIMATE_TEMP_MIN, min(CLIMATE_TEMP_MAX, temp))
        self.coordinator.target_temp = temp
        self.async_write_ha_state()

        if self.coordinator.heating_preset == PRESET_BY_POWER:
            return  # power mode doesn't use a temperature setpoint

        if self._debounced():
            return

        ok = await self.coordinator.client.send_write_settings(
            mode=START_MODE_BY_CONTROLLER_TEMP,
            setpoint=int(round(temp)),
            ventilation=0,
            power_level=self.coordinator.power_level,
        )
        self._last_command = time.monotonic()
        if not ok:
            _LOGGER.warning("WRITE_SETTINGS for %.0f°C got no ack", temp)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def _async_start_heat(self) -> None:
        """
        Start the heater in HEAT mode.

        By Temperature: always uses mode=0x02 (by_controller_temp / panel).
          The coordinator injection loop feeds the selected temperature source via 0x11.
        By Power: uses mode=0x04; power_level controls output.
        """
        if self.coordinator.heating_preset == PRESET_BY_TEMP:
            mode = START_MODE_BY_CONTROLLER_TEMP  # 0x02 — always, regardless of temp source
        else:
            mode = START_MODE_BY_POWER  # 0x04
        setpoint = int(round(self.coordinator.target_temp))
        level = self.coordinator.power_level

        _LOGGER.info(
            "Sending START: preset=%s mode=0x%02x setpoint=%d°C power_level=%d",
            self.coordinator.heating_preset,
            mode,
            setpoint,
            level,
        )
        ok = await self.coordinator.client.send_start(
            level=level,
            setpoint=setpoint,
            mode=mode,
        )
        self._last_command = time.monotonic()
        if not ok:
            _LOGGER.error("START command failed or got no acknowledgement")
            return
        # Coordinator loop handles 0x11 injection every poll cycle for PRESET_BY_TEMP
        await self.coordinator.async_request_refresh()

    async def _async_start_fan_only(self) -> None:
        """
        Start ventilation-only (FAN_ONLY) mode via 0x23.

        PORTED-BUT-UNVERIFIED: frame 0x23 has not been confirmed on the 44D.
        """
        _LOGGER.info(
            "Sending FAN_ONLY (0x23) at fan_level=%d — PORTED, not yet confirmed on 44D",
            self.coordinator.fan_level,
        )
        ok = await self.coordinator.client.send_fan_only(self.coordinator.fan_level)
        self._last_command = time.monotonic()
        if not ok:
            _LOGGER.error("FAN_ONLY command failed or got no acknowledgement")
        await self.coordinator.async_request_refresh()

    async def _async_stop(self) -> None:
        _LOGGER.info("Sending STOP — heater will complete purge/cooldown cycle")
        ok = await self.coordinator.client.send_stop()
        self._last_command = time.monotonic()
        if not ok:
            _LOGGER.error("STOP command failed — heater may not have received it")
        await self.coordinator.async_request_refresh()
