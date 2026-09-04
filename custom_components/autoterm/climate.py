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
    CONF_TEMP_SOURCE_ENTITY,
    DOMAIN,
    FAULT_RETRYABLE,
    POWER_LEVEL_MAX,
    POWER_LEVEL_MIN,
    REG_SOURCE_PANEL,
    REG_SOURCE_POWER,
    REG_SOURCE_TO_MODE,
)
from .coordinator import AutotermCoordinator

_LOGGER = logging.getLogger(__name__)

_DEFAULT_LEVEL = 5


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
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
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
        Return the current room temperature.

        Uses the configured HA sensor (e.g. DS18B20) when one is set in options,
        so the climate card reflects the actual room temp the thermostat is
        regulating against.  Falls back to the heater's internal sensor when no
        source entity is configured or its state is not a valid number.
        """
        source_entity: str | None = self._entry.options.get(CONF_TEMP_SOURCE_ENTITY)
        if source_entity:
            state = self.hass.states.get(source_entity)
            if state is not None and state.state not in ("unknown", "unavailable"):
                try:
                    return float(state.state)
                except ValueError:
                    pass
        st = self.coordinator.data
        return float(st.heater_temp) if st else None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self.coordinator.data
        attrs: dict[str, Any] = {
            "regulation_source": self.coordinator.reg_source,
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

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if self._debounced():
            return

        st = self.coordinator.data

        if hvac_mode == HVACMode.OFF:
            await self._async_stop()

        elif hvac_mode == HVACMode.HEAT:
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

        st = self.coordinator.data
        if st and (st.is_running or st.is_starting):
            # Live setpoint update via 0x11 — does not require a full restart.
            # In panel mode the coordinator loop feeds temp, so only send in
            # non-panel temperature modes and power mode.
            if self.coordinator.reg_source != REG_SOURCE_PANEL and not self._debounced():
                ok = await self.coordinator.client.send_set_temp(int(round(temp)))
                self._last_command = time.monotonic()
                if not ok:
                    _LOGGER.warning("SET_TEMP(0x11) for %.0f°C got no ack", temp)
                await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def _async_start_heat(self) -> None:
        """
        Start the heater in HEAT mode using the current regulation source.

        Regulation behaviour:
          - internal / panel / external  → mode = 0x01/0x02/0x03; setpoint = target_temp.
            After START, also send 0x11 setpoint frame for immediate effect.
            In panel mode the coordinator loop takes over the 0x11 injection.
          - power → mode = 0x04; power_level = coordinator.power_level.
        """
        mode = REG_SOURCE_TO_MODE.get(self.coordinator.reg_source, 0x04)
        setpoint = int(round(self.coordinator.target_temp))
        level = self.coordinator.power_level

        _LOGGER.info(
            "Sending START: mode=%s(0x%02x) setpoint=%d°C power_level=%d",
            self.coordinator.reg_source,
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

        # In temperature modes, send an immediate setpoint frame as well.
        # In panel mode the coordinator loop will feed the measured temp via 0x11.
        if self.coordinator.reg_source not in (REG_SOURCE_POWER, REG_SOURCE_PANEL):
            await self.coordinator.client.send_set_temp(setpoint)

        await self.coordinator.async_request_refresh()

    async def _async_start_fan_only(self) -> None:
        """
        Start ventilation-only (FAN_ONLY) mode via 0x23.

        PORTED-BUT-UNVERIFIED: frame 0x23 has not been confirmed on the 44D.
        Source: prclm (4D/44D) + k3mpaxl (2D) both confirm command ID 0x23.
        The last payload byte differs between sources (0x0F vs 0xFF); we follow prclm.
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
