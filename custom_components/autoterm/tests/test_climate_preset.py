"""
Offline tests for the always-panel-mode climate redesign.

Covers:
  - Entity availability matrix (preset/mode → which entities are available)
  - Temperature source selection and _get_source_temp dispatch
  - coordinator.get_displayed_temp / climate.current_temperature fallback chain
  - Running locks (HVAC mode + preset changes blocked while active)
  - _apply_settings pending-preset protection (first-read-only guard)

No serial port, no hardware, no network.
Run with:  pytest custom_components/autoterm/tests/
"""

from __future__ import annotations

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock

from custom_components.autoterm.const import (
    PRESET_BY_POWER,
    PRESET_BY_TEMP,
    START_MODE_BY_CONTROLLER_TEMP,
    START_MODE_BY_POWER,
    TEMP_SOURCE_EXTERNAL,
    TEMP_SOURCE_HA_SENSOR,
    TEMP_SOURCE_INTERNAL,
)
from custom_components.autoterm.coordinator import AutotermCoordinator

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_status(
    status1: int = 0,
    status2: int = 1,
    heater_temp: int = 20,
    ext_temp: int | None = None,
    error: int = 0,
) -> MagicMock:
    st = MagicMock()
    st.status1 = status1
    st.status2 = status2
    st.heater_temp = heater_temp
    st.ext_temp = ext_temp
    st.ext_temp_present = ext_temp is not None
    st.error = error
    st.is_idle = status1 == 0
    st.is_starting = status1 in (1, 2)
    st.is_running = status1 == 3
    st.is_stopping = status1 == 4
    st.is_fan_only = status1 == 3 and status2 == 35
    st.is_fault = error != 0
    st.is_lockout = error == 33
    st.state_name = "idle" if status1 == 0 else "running"
    return st


def _make_coordinator(
    heating_preset: str = PRESET_BY_TEMP,
    temp_source: str = TEMP_SOURCE_INTERNAL,
    status: MagicMock | None = None,
    source_entity: str | None = None,
    staleness_threshold: int = 120,
) -> AutotermCoordinator:
    hass = MagicMock()
    client = MagicMock()
    entry = MagicMock()
    entry.options = {}
    if source_entity:
        entry.options[TEMP_SOURCE_HA_SENSOR] = source_entity  # not used directly
        # The coordinator reads CONF_TEMP_SOURCE_ENTITY from options
        from custom_components.autoterm.const import (
            CONF_STALENESS_THRESHOLD,
            CONF_TEMP_SOURCE_ENTITY,
        )

        entry.options[CONF_TEMP_SOURCE_ENTITY] = source_entity
        entry.options[CONF_STALENESS_THRESHOLD] = staleness_threshold

    coord = AutotermCoordinator.__new__(AutotermCoordinator)
    coord.hass = hass
    coord.client = client
    coord._entry = entry
    coord.heating_preset = heating_preset
    coord.temp_source = temp_source
    coord.target_temp = 20.0
    coord.power_level = 5
    coord.fan_level = 5
    coord._settings = None
    coord._last_settings_poll = 0.0
    coord._initial_settings_applied = False
    coord._consecutive_poll_failures = 0
    coord._priming = False
    coord._prime_task = None
    coord.prime_status = "idle"
    coord.last_update_success = True
    coord.data = status if status is not None else _make_status()
    return coord


def _make_settings(mode: int, setpoint: int = 20, power_level: int = 5) -> MagicMock:
    s = MagicMock()
    s.mode = mode
    s.setpoint = setpoint
    s.ventilation = 0
    s.power_level = power_level
    return s


# ── Availability matrix ───────────────────────────────────────────────────────


def test_power_level_available_only_in_by_power():
    from custom_components.autoterm.number import AutotermPowerLevel

    coord = _make_coordinator(heating_preset=PRESET_BY_POWER)
    coord.last_update_success = True
    ent = AutotermPowerLevel.__new__(AutotermPowerLevel)
    ent.coordinator = coord
    assert ent.available is True


def test_power_level_unavailable_in_by_temp():
    from custom_components.autoterm.number import AutotermPowerLevel

    coord = _make_coordinator(heating_preset=PRESET_BY_TEMP)
    coord.last_update_success = True
    ent = AutotermPowerLevel.__new__(AutotermPowerLevel)
    ent.coordinator = coord
    assert ent.available is False


def test_fan_level_available_only_in_fan_only():
    from custom_components.autoterm.number import AutotermFanLevel

    st = _make_status(status1=3, status2=35)  # is_fan_only
    coord = _make_coordinator(status=st)
    coord.last_update_success = True
    ent = AutotermFanLevel.__new__(AutotermFanLevel)
    ent.coordinator = coord
    assert ent.available is True


def test_fan_level_unavailable_when_idle():
    from custom_components.autoterm.number import AutotermFanLevel

    st = _make_status(status1=0)  # idle
    coord = _make_coordinator(status=st)
    coord.last_update_success = True
    ent = AutotermFanLevel.__new__(AutotermFanLevel)
    ent.coordinator = coord
    assert ent.available is False


def test_fan_level_unavailable_when_heating():
    from custom_components.autoterm.number import AutotermFanLevel

    st = _make_status(status1=3, status2=0)  # running but not fan_only
    coord = _make_coordinator(status=st)
    coord.last_update_success = True
    ent = AutotermFanLevel.__new__(AutotermFanLevel)
    ent.coordinator = coord
    assert ent.available is False


def test_temp_source_available_only_in_by_temp():
    from custom_components.autoterm.select import AutotermTempSourceSelect

    coord = _make_coordinator(heating_preset=PRESET_BY_TEMP)
    coord.last_update_success = True
    ent = AutotermTempSourceSelect.__new__(AutotermTempSourceSelect)
    ent.coordinator = coord
    assert ent.available is True


def test_temp_source_unavailable_in_by_power():
    from custom_components.autoterm.select import AutotermTempSourceSelect

    coord = _make_coordinator(heating_preset=PRESET_BY_POWER)
    coord.last_update_success = True
    ent = AutotermTempSourceSelect.__new__(AutotermTempSourceSelect)
    ent.coordinator = coord
    assert ent.available is False


def test_target_temp_in_supported_features_when_by_temp():
    from homeassistant.components.climate import ClimateEntityFeature

    from custom_components.autoterm.climate import AutotermClimate

    coord = _make_coordinator(heating_preset=PRESET_BY_TEMP)
    ent = AutotermClimate.__new__(AutotermClimate)
    ent.coordinator = coord
    assert ent.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE


def test_target_temp_not_in_features_when_by_power():
    from homeassistant.components.climate import ClimateEntityFeature

    from custom_components.autoterm.climate import AutotermClimate

    coord = _make_coordinator(heating_preset=PRESET_BY_POWER)
    ent = AutotermClimate.__new__(AutotermClimate)
    ent.coordinator = coord
    assert not (ent.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE)


def test_preset_mode_in_supported_features_always():
    from homeassistant.components.climate import ClimateEntityFeature

    from custom_components.autoterm.climate import AutotermClimate

    for preset in (PRESET_BY_TEMP, PRESET_BY_POWER):
        coord = _make_coordinator(heating_preset=preset)
        ent = AutotermClimate.__new__(AutotermClimate)
        ent.coordinator = coord
        assert ent.supported_features & ClimateEntityFeature.PRESET_MODE


# ── Temp source select options ────────────────────────────────────────────────


def test_temp_source_options_without_external():
    from custom_components.autoterm.select import AutotermTempSourceSelect

    st = _make_status(ext_temp=None)
    coord = _make_coordinator(status=st)
    ent = AutotermTempSourceSelect.__new__(AutotermTempSourceSelect)
    ent.coordinator = coord
    opts = ent.options
    assert TEMP_SOURCE_EXTERNAL not in opts
    assert TEMP_SOURCE_INTERNAL in opts
    assert TEMP_SOURCE_HA_SENSOR in opts


def test_temp_source_options_with_external():
    from custom_components.autoterm.select import AutotermTempSourceSelect

    st = _make_status(ext_temp=5)
    coord = _make_coordinator(status=st)
    ent = AutotermTempSourceSelect.__new__(AutotermTempSourceSelect)
    ent.coordinator = coord
    opts = ent.options
    assert TEMP_SOURCE_EXTERNAL in opts
    assert TEMP_SOURCE_INTERNAL in opts
    assert TEMP_SOURCE_HA_SENSOR in opts


def test_temp_source_current_option_falls_back_when_external_gone():
    from custom_components.autoterm.select import AutotermTempSourceSelect

    st = _make_status(ext_temp=None)  # sensor gone
    coord = _make_coordinator(temp_source=TEMP_SOURCE_EXTERNAL, status=st)
    ent = AutotermTempSourceSelect.__new__(AutotermTempSourceSelect)
    ent.coordinator = coord
    assert ent.current_option == TEMP_SOURCE_INTERNAL


# ── Panel feed source selection (_get_source_temp) ────────────────────────────


def test_get_source_temp_internal_uses_heater_temp():
    st = _make_status(heater_temp=22)
    coord = _make_coordinator(temp_source=TEMP_SOURCE_INTERNAL, status=st)
    assert coord._get_source_temp() == 22


def test_get_source_temp_external_uses_ext_temp():
    st = _make_status(ext_temp=5, heater_temp=22)
    coord = _make_coordinator(temp_source=TEMP_SOURCE_EXTERNAL, status=st)
    assert coord._get_source_temp() == 5


def test_get_source_temp_external_returns_none_when_absent():
    st = _make_status(ext_temp=None, heater_temp=22)
    coord = _make_coordinator(temp_source=TEMP_SOURCE_EXTERNAL, status=st)
    assert coord._get_source_temp() is None


def test_get_source_temp_ha_sensor_valid(monkeypatch):
    st = _make_status(heater_temp=20)
    coord = _make_coordinator(
        temp_source=TEMP_SOURCE_HA_SENSOR, status=st, source_entity="sensor.room_temp"
    )
    sensor_state = MagicMock()
    sensor_state.state = "19.5"
    sensor_state.last_changed = datetime.datetime.now(datetime.UTC)
    coord.hass.states.get.return_value = sensor_state
    assert coord._get_source_temp() == 20  # round(19.5) == 20


def test_get_source_temp_ha_sensor_stale_returns_none(monkeypatch):
    st = _make_status(heater_temp=18)
    coord = _make_coordinator(
        temp_source=TEMP_SOURCE_HA_SENSOR,
        status=st,
        source_entity="sensor.room_temp",
        staleness_threshold=120,
    )
    sensor_state = MagicMock()
    sensor_state.state = "19.0"
    # Last changed 200 seconds ago — stale
    sensor_state.last_changed = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        seconds=200
    )
    coord.hass.states.get.return_value = sensor_state
    assert coord._get_source_temp() is None


def test_get_source_temp_ha_sensor_unavailable_returns_none():
    st = _make_status(heater_temp=20)
    coord = _make_coordinator(
        temp_source=TEMP_SOURCE_HA_SENSOR, status=st, source_entity="sensor.room_temp"
    )
    sensor_state = MagicMock()
    sensor_state.state = "unavailable"
    coord.hass.states.get.return_value = sensor_state
    assert coord._get_source_temp() is None


def test_get_source_temp_ha_sensor_unknown_returns_none():
    st = _make_status(heater_temp=20)
    coord = _make_coordinator(
        temp_source=TEMP_SOURCE_HA_SENSOR, status=st, source_entity="sensor.room_temp"
    )
    sensor_state = MagicMock()
    sensor_state.state = "unknown"
    coord.hass.states.get.return_value = sensor_state
    assert coord._get_source_temp() is None


# ── get_displayed_temp / current_temperature follows source ──────────────────


def test_get_displayed_temp_internal_returns_heater_temp():
    st = _make_status(heater_temp=22)
    coord = _make_coordinator(temp_source=TEMP_SOURCE_INTERNAL, status=st)
    assert coord.get_displayed_temp() == 22.0


def test_get_displayed_temp_external_returns_ext_temp():
    st = _make_status(ext_temp=5, heater_temp=22)
    coord = _make_coordinator(temp_source=TEMP_SOURCE_EXTERNAL, status=st)
    assert coord.get_displayed_temp() == 5.0


def test_get_displayed_temp_stale_ha_sensor_returns_heater_fallback():
    st = _make_status(heater_temp=18)
    coord = _make_coordinator(
        temp_source=TEMP_SOURCE_HA_SENSOR,
        status=st,
        source_entity="sensor.room_temp",
        staleness_threshold=120,
    )
    sensor_state = MagicMock()
    sensor_state.state = "19.0"
    sensor_state.last_changed = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        seconds=200
    )
    coord.hass.states.get.return_value = sensor_state
    # Stale → source returns None → fallback to heater_temp=18
    result = coord.get_displayed_temp()
    assert result == 18.0
    assert result is not None  # never blank


def test_get_displayed_temp_unavailable_ha_sensor_returns_heater_fallback():
    st = _make_status(heater_temp=20)
    coord = _make_coordinator(
        temp_source=TEMP_SOURCE_HA_SENSOR, status=st, source_entity="sensor.room_temp"
    )
    sensor_state = MagicMock()
    sensor_state.state = "unavailable"
    coord.hass.states.get.return_value = sensor_state
    assert coord.get_displayed_temp() == 20.0


def test_get_displayed_temp_external_falls_back_to_heater_when_absent():
    st = _make_status(ext_temp=None, heater_temp=15)
    coord = _make_coordinator(temp_source=TEMP_SOURCE_EXTERNAL, status=st)
    # ext_temp absent → _get_source_temp returns None → falls back to heater_temp
    assert coord.get_displayed_temp() == 15.0


def test_climate_current_temperature_uses_get_displayed_temp():
    from custom_components.autoterm.climate import AutotermClimate

    st = _make_status(heater_temp=21, ext_temp=8)
    coord = _make_coordinator(
        heating_preset=PRESET_BY_TEMP,
        temp_source=TEMP_SOURCE_EXTERNAL,
        status=st,
    )
    ent = AutotermClimate.__new__(AutotermClimate)
    ent.coordinator = coord
    # current_temperature should equal what _get_source_temp returns (ext_temp=8)
    assert ent.current_temperature == 8.0


def test_climate_current_temperature_by_power_uses_heater_temp():
    from custom_components.autoterm.climate import AutotermClimate

    st = _make_status(heater_temp=25)
    coord = _make_coordinator(heating_preset=PRESET_BY_POWER, status=st)
    ent = AutotermClimate.__new__(AutotermClimate)
    ent.coordinator = coord
    assert ent.current_temperature == 25.0


def test_climate_current_temperature_stale_source_shows_fallback_not_none():
    from custom_components.autoterm.climate import AutotermClimate

    st = _make_status(heater_temp=18)
    coord = _make_coordinator(
        heating_preset=PRESET_BY_TEMP,
        temp_source=TEMP_SOURCE_HA_SENSOR,
        status=st,
        source_entity="sensor.room_temp",
        staleness_threshold=120,
    )
    sensor_state = MagicMock()
    sensor_state.state = "22.0"
    sensor_state.last_changed = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        seconds=300
    )
    coord.hass.states.get.return_value = sensor_state
    ent = AutotermClimate.__new__(AutotermClimate)
    ent.coordinator = coord
    result = ent.current_temperature
    assert result is not None
    assert result == 18.0  # fallback to heater internal, never blank


# ── Running lock logic ────────────────────────────────────────────────────────


def test_hvac_mode_change_blocked_when_running():
    from homeassistant.components.climate import HVACMode

    from custom_components.autoterm.climate import AutotermClimate

    st = _make_status(status1=3, status2=0)  # running
    coord = _make_coordinator(status=st)
    ent = AutotermClimate.__new__(AutotermClimate)
    ent.coordinator = coord
    ent._last_command = 0.0  # no debounce
    ent.async_write_ha_state = MagicMock()
    asyncio.run(ent.async_set_hvac_mode(HVACMode.FAN_ONLY))
    # No command should have been sent
    coord.client.send_fan_only.assert_not_called()


def test_hvac_mode_change_allowed_when_idle():
    from homeassistant.components.climate import HVACMode

    from custom_components.autoterm.climate import AutotermClimate

    st = _make_status(status1=0)  # idle
    coord = _make_coordinator(status=st)
    coord.client.send_fan_only = AsyncMock(return_value=True)
    coord.async_request_refresh = AsyncMock()
    ent = AutotermClimate.__new__(AutotermClimate)
    ent.coordinator = coord
    ent._last_command = 0.0
    ent.async_write_ha_state = MagicMock()
    asyncio.run(ent.async_set_hvac_mode(HVACMode.FAN_ONLY))
    coord.client.send_fan_only.assert_called_once()


def test_preset_change_blocked_when_running():
    from custom_components.autoterm.climate import AutotermClimate

    st = _make_status(status1=3, status2=0)  # running
    coord = _make_coordinator(heating_preset=PRESET_BY_TEMP, status=st)
    coord.client.send_write_settings = AsyncMock(return_value=True)
    ent = AutotermClimate.__new__(AutotermClimate)
    ent.coordinator = coord
    ent.async_write_ha_state = MagicMock()
    original_preset = coord.heating_preset
    asyncio.run(ent.async_set_preset_mode(PRESET_BY_POWER))
    # Preset must not have changed and no wire command sent
    assert coord.heating_preset == original_preset
    coord.client.send_write_settings.assert_not_called()


def test_preset_change_allowed_when_idle():
    from custom_components.autoterm.climate import AutotermClimate

    st = _make_status(status1=0)  # idle
    coord = _make_coordinator(heating_preset=PRESET_BY_TEMP, status=st)
    coord.client.send_write_settings = AsyncMock(return_value=True)
    ent = AutotermClimate.__new__(AutotermClimate)
    ent.coordinator = coord
    ent.async_write_ha_state = MagicMock()
    asyncio.run(ent.async_set_preset_mode(PRESET_BY_POWER))
    assert coord.heating_preset == PRESET_BY_POWER
    coord.client.send_write_settings.assert_called_once()
    call_kwargs = coord.client.send_write_settings.call_args.kwargs
    assert call_kwargs["mode"] == START_MODE_BY_POWER


def test_temp_source_change_allowed_when_running():
    from custom_components.autoterm.select import AutotermTempSourceSelect

    st = _make_status(status1=3, status2=0, ext_temp=5)
    coord = _make_coordinator(
        heating_preset=PRESET_BY_TEMP,
        temp_source=TEMP_SOURCE_INTERNAL,
        status=st,
    )
    ent = AutotermTempSourceSelect.__new__(AutotermTempSourceSelect)
    ent.coordinator = coord
    ent.async_write_ha_state = MagicMock()
    asyncio.run(ent.async_select_option(TEMP_SOURCE_EXTERNAL))
    assert coord.temp_source == TEMP_SOURCE_EXTERNAL
    coord.client.send_write_settings.assert_not_called()


# ── _apply_settings pending-preset protection ─────────────────────────────────


def test_apply_settings_mode04_sets_by_power_on_first_read():
    coord = _make_coordinator(heating_preset=PRESET_BY_TEMP)
    coord._initial_settings_applied = False
    coord._apply_settings(_make_settings(mode=START_MODE_BY_POWER, setpoint=20, power_level=7))
    assert coord.heating_preset == PRESET_BY_POWER
    assert coord.power_level == 7
    assert coord._initial_settings_applied is True


def test_apply_settings_mode02_sets_by_temp_on_first_read():
    coord = _make_coordinator(heating_preset=PRESET_BY_POWER)
    coord._initial_settings_applied = False
    coord._apply_settings(
        _make_settings(mode=START_MODE_BY_CONTROLLER_TEMP, setpoint=22, power_level=3)
    )
    assert coord.heating_preset == PRESET_BY_TEMP
    assert coord.target_temp == 22.0


def test_apply_settings_does_not_overwrite_after_first_read():
    coord = _make_coordinator(heating_preset=PRESET_BY_TEMP)
    coord._initial_settings_applied = True  # already applied
    # Heater still echoes back old mode=0x04 (before HA START confirms it)
    coord._apply_settings(_make_settings(mode=START_MODE_BY_POWER, setpoint=20, power_level=5))
    # Must NOT revert to by-power
    assert coord.heating_preset == PRESET_BY_TEMP


def test_apply_settings_does_not_update_temp_source():
    coord = _make_coordinator(temp_source=TEMP_SOURCE_HA_SENSOR)
    coord._initial_settings_applied = False
    coord._apply_settings(_make_settings(mode=START_MODE_BY_CONTROLLER_TEMP, setpoint=20))
    # temp_source is purely HA-managed; settings reads must not touch it
    assert coord.temp_source == TEMP_SOURCE_HA_SENSOR


def test_apply_settings_always_syncs_power_level():
    coord = _make_coordinator(heating_preset=PRESET_BY_POWER)
    coord._initial_settings_applied = True
    coord._apply_settings(_make_settings(mode=START_MODE_BY_POWER, setpoint=15, power_level=9))
    assert coord.power_level == 9


# ── Start command uses correct mode ──────────────────────────────────────────


def test_start_heat_by_temp_uses_mode_0x02():
    from custom_components.autoterm.climate import AutotermClimate

    st = _make_status(status1=0)
    coord = _make_coordinator(heating_preset=PRESET_BY_TEMP, status=st)
    coord.client.send_start = AsyncMock(return_value=True)
    coord.async_request_refresh = AsyncMock()
    ent = AutotermClimate.__new__(AutotermClimate)
    ent.coordinator = coord
    ent._last_command = 0.0
    ent.async_write_ha_state = MagicMock()
    asyncio.run(ent._async_start_heat())
    coord.client.send_start.assert_called_once()
    call_kwargs = coord.client.send_start.call_args.kwargs
    assert call_kwargs["mode"] == START_MODE_BY_CONTROLLER_TEMP  # always 0x02


def test_start_heat_by_power_uses_mode_0x04():
    from custom_components.autoterm.climate import AutotermClimate

    st = _make_status(status1=0)
    coord = _make_coordinator(heating_preset=PRESET_BY_POWER, status=st)
    coord.client.send_start = AsyncMock(return_value=True)
    coord.async_request_refresh = AsyncMock()
    ent = AutotermClimate.__new__(AutotermClimate)
    ent.coordinator = coord
    ent._last_command = 0.0
    ent.async_write_ha_state = MagicMock()
    asyncio.run(ent._async_start_heat())
    coord.client.send_start.assert_called_once()
    call_kwargs = coord.client.send_start.call_args.kwargs
    assert call_kwargs["mode"] == START_MODE_BY_POWER  # 0x04
