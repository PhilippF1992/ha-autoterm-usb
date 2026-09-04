"""Constants for the Autoterm USB integration."""

from __future__ import annotations

DOMAIN = "autoterm"

# ── Config / options keys ────────────────────────────────────────────────────

CONF_PORT = "port"
CONF_BAUD_RATE = "baud_rate"
CONF_POLL_INTERVAL = "poll_interval"

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_BAUD = 2400  # CONFIRMED on real hardware
DEFAULT_POLL_INTERVAL = 5  # seconds
DEFAULT_NAME = "Autoterm USB"

# Stable by-id path hint shown as default in config flow
HINT_PORT = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_ABAKLQ9A-if00-port0"

# ── Protocol command IDs ─────────────────────────────────────────────────────

CMD_STATUS = 0x0F
CMD_STOP = 0x03
CMD_START = 0x01
CMD_GET_SETTINGS = 0x02
CMD_GET_VERSION = 0x06

# ── START frame: mode byte ────────────────────────────────────────────────────

START_MODE_BY_HEATER_TEMP = 0x01  # temperature-controlled (not yet confirmed)
START_MODE_BY_CONTROLLER_TEMP = 0x02  # not confirmed
START_MODE_BY_EXTERNAL_TEMP = 0x03  # not confirmed
START_MODE_BY_POWER = 0x04  # CONFIRMED working

# ── Temperature / power limits ────────────────────────────────────────────────

POWER_LEVEL_MIN = 1
POWER_LEVEL_MAX = 9

# Conservative climate target-temperature range (°C)
CLIMATE_TEMP_MIN = 0.0
CLIMATE_TEMP_MAX = 30.0
CLIMATE_TEMP_STEP = 1.0

# ── Safety timeouts ───────────────────────────────────────────────────────────

STOP_RESEND_INTERVAL = 10  # re-send STOP every N s during cooldown
COMMAND_DEBOUNCE = 5  # minimum seconds between start/stop commands

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

# Fault codes where a user-initiated restart is safe (heater clears them on next start attempt)
FAULT_RETRYABLE: frozenset[int] = frozenset({13})
