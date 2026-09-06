"""Constants for the Autoterm USB integration."""

from __future__ import annotations

DOMAIN = "autoterm"

# ── Config / options keys ────────────────────────────────────────────────────

CONF_PORT = "port"
CONF_BAUD_RATE = "baud_rate"
CONF_POLL_INTERVAL = "poll_interval"
CONF_TEMP_SOURCE_ENTITY = "temp_source_entity"
CONF_STALENESS_THRESHOLD = "staleness_threshold"

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_BAUD = 2400  # CONFIRMED on real hardware
DEFAULT_POLL_INTERVAL = 5  # seconds
DEFAULT_NAME = "Autoterm USB"
DEFAULT_POWER_LEVEL = 5
DEFAULT_FAN_LEVEL = 5
DEFAULT_STALENESS_THRESHOLD = 120  # seconds before panel sensor feed is considered stale

# Stable by-id path hint shown as default in config flow
HINT_PORT = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_ABAKLQ9A-if00-port0"

# ── Protocol command IDs ─────────────────────────────────────────────────────

CMD_STATUS = 0x0F
CMD_STOP = 0x03
CMD_START = 0x01
CMD_GET_SETTINGS = 0x02
CMD_GET_VERSION = 0x06
# CONFIRMED: 1-byte payload = temperature in °C. Used for setpoint AND panel temp feed.
# Source: task spec + prclm/AutotermHeaterController + k3mpaxl/pekaway-ha-autoterm
CMD_SET_TEMP = 0x11
# FAN/VENTILATE only — PORTED from prclm (4D/44D) and k3mpaxl (2D). NOT confirmed on real hardware.
# k3mpaxl payload last byte = 0xFF; prclm (4D/44D) = 0x0F. We follow prclm as 4D-specific.
CMD_FAN_ONLY = 0x23

# ── START/SETTINGS frame: mode byte ──────────────────────────────────────────

START_MODE_BY_HEATER_TEMP = 0x01      # by internal heater sensor
START_MODE_BY_CONTROLLER_TEMP = 0x02  # by panel/controller-reported temperature
START_MODE_BY_EXTERNAL_TEMP = 0x03   # by external DS18B20 sensor
START_MODE_BY_POWER = 0x04           # CONFIRMED working

# ── Regulation source strings (used by select entity and coordinator) ─────────

REG_SOURCE_INTERNAL = "internal"
REG_SOURCE_PANEL = "panel"
REG_SOURCE_EXTERNAL = "external"
REG_SOURCE_POWER = "power"

REG_SOURCE_OPTIONS = [
    REG_SOURCE_INTERNAL,
    REG_SOURCE_PANEL,
    REG_SOURCE_EXTERNAL,
    REG_SOURCE_POWER,
]

# ── HA preset mode names ──────────────────────────────────────────────────────

PRESET_BY_TEMP  = "By Temperature"
PRESET_BY_POWER = "By Power"

# ── Temperature source names (HA-side; all fed as panel temp via 0x02 mode) ──

TEMP_SOURCE_INTERNAL  = "internal"    # feed heater's own intake sensor
TEMP_SOURCE_EXTERNAL  = "external"    # feed heater's DS18B20 external sensor
TEMP_SOURCE_HA_SENSOR = "ha_sensor"   # feed configured HA sensor entity

REG_SOURCE_TO_MODE: dict[str, int] = {
    REG_SOURCE_INTERNAL: START_MODE_BY_HEATER_TEMP,
    REG_SOURCE_PANEL: START_MODE_BY_CONTROLLER_TEMP,
    REG_SOURCE_EXTERNAL: START_MODE_BY_EXTERNAL_TEMP,
    REG_SOURCE_POWER: START_MODE_BY_POWER,
}

MODE_TO_REG_SOURCE: dict[int, str] = {v: k for k, v in REG_SOURCE_TO_MODE.items()}

# ── Temperature / power limits ────────────────────────────────────────────────

POWER_LEVEL_MIN = 1
POWER_LEVEL_MAX = 9

# Climate target-temperature range (°C) — confirmed 0–30 per task spec
CLIMATE_TEMP_MIN = 0.0
CLIMATE_TEMP_MAX = 30.0
CLIMATE_TEMP_STEP = 1.0

# Clamp range for panel-fed sensor values (safety guard against wild readings)
PANEL_TEMP_MIN = -30
PANEL_TEMP_MAX = 60

# ── Coordinator timing ────────────────────────────────────────────────────────

STOP_RESEND_INTERVAL = 10      # re-send STOP every N s during cooldown
COMMAND_DEBOUNCE = 5           # minimum seconds between start/stop commands
SETTINGS_READ_INTERVAL = 60    # seconds between periodic settings re-reads from heater

# ── Primary state codes (status1) ────────────────────────────────────────────

STATE1_IDLE = 0
STATE1_STARTING = 1
STATE1_WARMUP = 2
STATE1_RUNNING = 3
STATE1_SHUTDOWN = 4

# ── State (status1, status2) → name map ──────────────────────────────────────

STATE_NAMES: dict[tuple[int, int], str] = {
    (0, 1): "idle",
    (1, 0): "starting",
    (2, 0): "warmup",
    (2, 1): "glow_plug",
    (2, 2): "ignition_1",
    (2, 3): "ignition_2",
    (2, 4): "heat_chamber",
    (2, 6): "ignition_glow",  # observed live 2026-09-04
    (2, 7): "ignition_retry",  # undocumented, observed briefly
    (3, 0): "running",
    (3, 35): "fan_only",
    (3, 4): "cooling_down",
    (4, 0): "shutdown",
}

# ── Fault code → (title, description) ────────────────────────────────────────
# Source: errors.md / Autoterm installation manual. Confidence per errors.md.

FAULT_CODES: dict[int, tuple[str, str]] = {
    1: ("Overheating (heat exchanger)", "Blocked air flow or faulty overheat sensor"),
    2: ("Overheating (intake sensor)", "Control unit overheated; ensure purge completes"),
    3: ("Flame failure during operation", "Air in fuel line, insufficient fuel, pump fault"),
    4: ("Failure to ignite", "Empty tank, air in line, blocked filter/pump"),
    5: ("Faulty HX temp sensor", "Short/open circuit in sensor or wiring"),
    6: ("Faulty control-unit sensor", "Internal sensor fault; replace control unit"),
    7: ("Overheat sensor open circuit", "Defective sensor or broken wire"),
    8: ("Start failure", "Fuel-supply fault; see code 29"),
    9: ("Faulty glow plug", "Short/open/worn glow plug"),
    10: ("Glow plug circuit fault", "Wiring or control-unit driver fault"),
    11: ("Flame indicator fault", "Sensor malfunction or wiring fault"),
    12: ("Temp sensor / flame fault", "See code 5"),
    13: ("Does not start (2 attempts)", "No fuel, air in line, blocked filter/exhaust"),
    16: ("Undervoltage", "Battery low or wiring voltage drop"),
    17: ("Overvoltage", "Charging fault or wrong supply"),
    20: ("Control unit fault", "Internal control unit fault"),
    27: ("Fan motor not rotating", "Blocked fan or faulty motor"),
    28: ("Fan overspeed / wrong RPM", "Motor or speed-sensor fault"),
    29: ("Flame out / fuel fault", "Air in fuel, pump fault, or empty tank"),
    30: ("No communication", "Damaged harness or broken data wire"),
    31: ("Overheating (outlet sensor)", "Restricted hot-air flow or sensor fault"),
    32: ("Temp sensor fault (won't start)", "Faulty sensor; heater won't ignite until resolved"),
    33: ("LOCKOUT", "Repeated critical faults; manual unlock required"),
    34: ("Communication fault", "Harness/connector/data-wire fault"),
}

FAULT_LOCKOUT = 33

# Fault codes where a user-initiated restart is safe (heater clears them on next start attempt).
# 13 = does not start / no fuel (retryable on dry line).
# 30, 34 = communication faults: triggered when the HA integration closes the serial port
#   (e.g. HA restart). If the integration is now polling normally the cause is already gone.
FAULT_RETRYABLE: frozenset[int] = frozenset({13, 30, 34})
