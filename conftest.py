"""
Root conftest: register homeassistant stubs before any test collection.
This must be at the repo root so it executes before pytest imports any
custom_components package that has `from homeassistant...` at module level.
"""
from __future__ import annotations

import sys
import types


def _stub(name: str, **attrs: object) -> types.ModuleType:
    m = sys.modules.get(name) or types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# ── homeassistant core ────────────────────────────────────────────────────────
_stub("homeassistant")
_stub("homeassistant.config_entries",
      ConfigEntry=object, ConfigFlow=object, OptionsFlow=object)
_stub("homeassistant.const",
      Platform=types.SimpleNamespace(
          CLIMATE="climate", SENSOR="sensor", BINARY_SENSOR="binary_sensor"
      ))
_stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
_stub("homeassistant.exceptions", ConfigEntryNotReady=Exception)

# Generic base classes that support subscript (Coordinator[T] etc.)
class _Subscriptable:
    def __class_getitem__(cls, item: object) -> type:
        return cls
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)

# helpers
_stub("homeassistant.helpers")
_stub("homeassistant.helpers.entity", DeviceInfo=dict)
_stub("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
_stub("homeassistant.helpers.update_coordinator",
      DataUpdateCoordinator=_Subscriptable,
      UpdateFailed=Exception,
      CoordinatorEntity=_Subscriptable)
_stub("homeassistant.helpers.selector")  # used by config_flow

# component platforms
_stub("homeassistant.components")
_stub("homeassistant.components.climate",
      ClimateEntity=object,
      ClimateEntityFeature=types.SimpleNamespace(
          TARGET_TEMPERATURE=1, TURN_ON=512, TURN_OFF=1024
      ),
      HVACAction=types.SimpleNamespace(
          OFF="off", IDLE="idle", HEATING="heating",
          PREHEATING="preheating", FAN="fan", COOLING="cooling"
      ),
      HVACMode=types.SimpleNamespace(
          OFF="off", HEAT="heat", FAN_ONLY="fan_only"
      ))
_stub("homeassistant.components.sensor",
      SensorEntity=object, SensorEntityDescription=object,
      SensorDeviceClass=types.SimpleNamespace(TEMPERATURE="temperature", VOLTAGE="voltage"),
      SensorStateClass=types.SimpleNamespace(MEASUREMENT="measurement"))
_stub("homeassistant.components.binary_sensor",
      BinarySensorEntity=object, BinarySensorEntityDescription=object,
      BinarySensorDeviceClass=types.SimpleNamespace(
          RUNNING="running", PROBLEM="problem", CONNECTIVITY="connectivity"
      ))

# units
_stub("homeassistant.const",
      UnitOfTemperature=types.SimpleNamespace(CELSIUS="°C"),
      UnitOfElectricPotential=types.SimpleNamespace(VOLT="V"),
      ATTR_TEMPERATURE="temperature",
      Platform=types.SimpleNamespace(
          CLIMATE="climate", SENSOR="sensor", BINARY_SENSOR="binary_sensor"
      ))

# ── const stub (full — covers codec.py and client.py imports) ────────────────
_STATE_NAMES = {
    (0, 1): "idle",          (1, 0): "starting",
    (2, 0): "warmup",        (2, 1): "glow_plug",
    (2, 2): "ignition_1",    (2, 3): "ignition_2",
    (2, 4): "heat_chamber",  (2, 6): "ignition_glow",
    (2, 7): "ignition_retry",(3, 0): "running",
    (3, 35):"fan_only",      (3, 4): "cooling_down",
    (4, 0): "shutdown",
}

_stub("custom_components.autoterm.const",
    DOMAIN="autoterm",
    CMD_STATUS=0x0F, CMD_STOP=0x03, CMD_START=0x01,
    CMD_GET_SETTINGS=0x02, CMD_GET_VERSION=0x06,
    START_MODE_BY_POWER=0x04,
    START_MODE_BY_HEATER_TEMP=0x01,
    START_MODE_BY_CONTROLLER_TEMP=0x02,
    START_MODE_BY_EXTERNAL_TEMP=0x03,
    DEFAULT_BAUD=2400,
    DEFAULT_POLL_INTERVAL=5,
    DEFAULT_NAME="Autoterm Air 4D",
    CONF_PORT="port",
    CONF_BAUD_RATE="baud_rate",
    CONF_POLL_INTERVAL="poll_interval",
    HINT_PORT="/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_ABAKLQ9A-if00-port0",
    POWER_LEVEL_MIN=1, POWER_LEVEL_MAX=9,
    CLIMATE_TEMP_MIN=8.0, CLIMATE_TEMP_MAX=30.0, CLIMATE_TEMP_STEP=1.0,
    STATE1_IDLE=0, STATE1_STARTING=1, STATE1_WARMUP=2, STATE1_RUNNING=3, STATE1_SHUTDOWN=4,
    STOP_RESEND_INTERVAL=10,
    COMMAND_DEBOUNCE=5,
    FAULT_LOCKOUT=33,
    FAULT_CODES={},
    STATE_NAMES=_STATE_NAMES,
)

import os as _os  # noqa: E402

# Stub custom_components as a namespace, then make custom_components.autoterm
# look like a real package (with __path__) so submodule imports (codec, etc.)
# resolve to actual files without running __init__.py.
_cc_stub = types.ModuleType("custom_components")
_cc_stub.__path__ = []
sys.modules.setdefault("custom_components", _cc_stub)

_pkg_dir = _os.path.abspath(_os.path.join(_os.path.dirname(__file__),
                                           "custom_components", "autoterm"))
_ca_stub = types.ModuleType("custom_components.autoterm")
_ca_stub.__path__ = [_pkg_dir]
_ca_stub.__package__ = "custom_components.autoterm"
_ca_stub.__spec__ = None
sys.modules["custom_components.autoterm"] = _ca_stub
