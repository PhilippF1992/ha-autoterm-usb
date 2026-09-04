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
    POWER_LEVEL_MAX,
    POWER_LEVEL_MIN,
    START_MODE_BY_POWER,
)
from .coordinator import AutotermCoordinator

_LOGGER = logging.getLogger(__name__)

# Default power level when no target temperature is set
_DEFAULT_LEVEL = 9

# ── Temperature → power level mapping ────────────────────────────────────────
# NOTE: mode=by-power (0x04) is CONFIRMED. The heater doesn't expose a true
# temperature setpoint that maps 1:1 to power, so we approximate linearly.
# Once mode=0x01 (by-heater-temp) is tested, this mapping can be replaced
# with a direct setpoint send.


def _temp_to_level(temp: float) -> int:
    frac = (temp - CLIMATE_TEMP_MIN) / (CLIMATE_TEMP_MAX - CLIMATE_TEMP_MIN)
    level = round(frac * (POWER_LEVEL_MAX - POWER_LEVEL_MIN)) + POWER_LEVEL_MIN
    return max(POWER_LEVEL_MIN, min(POWER_LEVEL_MAX, level))


# ── hvac_action from HeaterStatus ─────────────────────────────────────────────


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
        return HVACAction.COOLING  # purge/cooldown cycle
    return HVACAction.IDLE


# ── hvac_mode reflected from status ──────────────────────────────────────────


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
    _attr_name = None  # device name IS the entity name
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
    _attr_target_temperature = 20.0  # sane default until user changes it

    def __init__(self, coordinator: AutotermCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_climate"
        self._last_command = 0.0  # monotonic timestamp of last start/stop

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.unique_id or self._entry.entry_id)},
            name=self._entry.title,
            manufacturer="Autoterm",
            model="Air 4D (Planar 44D)",
        )

    # ── State reflection ──────────────────────────────────────────────────────

    @property
    def hvac_mode(self) -> HVACMode:
        return _hvac_mode(self.coordinator.data)

    @property
    def hvac_action(self) -> HVACAction:
        return _hvac_action(self.coordinator.data)

    @property
    def current_temperature(self) -> float | None:
        st = self.coordinator.data
        return float(st.heater_temp) if st else None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self.coordinator.data
        if st is None:
            return {}
        attrs: dict[str, Any] = {
            "state_name": st.state_name,
            "status1": st.status1,
            "status2": st.status2,
            "error_code": st.error,
            "power_level": _temp_to_level(self._attr_target_temperature or 20.0),
        }
        if st.is_lockout:
            attrs["lockout"] = True
            attrs["lockout_info"] = "Manual unlock procedure required (see manual)"
        return attrs

    # ── Commands ──────────────────────────────────────────────────────────────

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
            # Retryable faults (e.g. error 13 = ignition failed) are cleared by
            # the ECU on the next start attempt — allow the restart through.
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
            await self._async_start()

        elif hvac_mode == HVACMode.FAN_ONLY:
            # TODO: ventilation-only start frame is NOT yet confirmed.
            # The START payload has a ventilation flag (byte[4]=0x01) but the
            # correct mode for fan-only operation has not been tested.
            # Sending an unconfirmed frame risks unintended ignition.
            # This will be implemented once the frame is confirmed on real hardware.
            _LOGGER.warning(
                "FAN_ONLY mode is not yet implemented: "
                "the ventilation-only start frame has not been confirmed. "
                "Use HEAT mode to run the heater."
            )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            self._attr_target_temperature = float(temp)
            self.async_write_ha_state()
            # If already running, update power level
            st = self.coordinator.data
            if st and st.is_running and not self._debounced():
                await self._async_start()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _async_start(self) -> None:
        level = _temp_to_level(self._attr_target_temperature or 20.0)
        target = self._attr_target_temperature or 20.0
        _LOGGER.info("Sending START at power level %d (target=%.0f°C)", level, target)
        ok = await self.coordinator.client.send_start(level=level, mode=START_MODE_BY_POWER)
        self._last_command = time.monotonic()
        if not ok:
            _LOGGER.error("START command failed or got no acknowledgement")
        await self.coordinator.async_request_refresh()

    async def _async_stop(self) -> None:
        _LOGGER.info("Sending STOP — heater will complete purge/cooldown cycle")
        ok = await self.coordinator.client.send_stop()
        self._last_command = time.monotonic()
        if not ok:
            _LOGGER.error("STOP command failed — heater may not have received it")
        # Do NOT close the port — heater needs to complete its purge cycle.
        # Keep polling; entities reflect the stopping→idle transition naturally.
        await self.coordinator.async_request_refresh()
