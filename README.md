# Autoterm USB — Home Assistant custom integration

Third-party custom integration for **Autoterm** (formerly Planar) diesel air heaters,
connected to Home Assistant via a **VAN PI USB adapter** (FTDI FT232R).

> This is **not** an official Autoterm integration and **not** an official Home
> Assistant integration.

## Scope

- **Transport:** FTDI FT232R USB-to-UART adapter (VAN PI) at **2400 baud 8N1** — confirmed on Air 4D.
- **Protocol:** Autoterm proprietary poll-response serial protocol. All core frames confirmed on real hardware.
- **Heater:** Autoterm Air 4D (Planar 44D). Other Autoterm/Planar variants may work but are untested.
- **Home Assistant OS:** current stable Core / HAOS. Installs under `/config/custom_components/autoterm/`.
- **Not supported:** Wi-Fi/Bluetooth panels, OEM control panels, RS232 adapters other than the VAN PI.

## Hardware warning

> There are multiple USB-serial devices on a typical camper van Pi setup (Daly BMS, Victron MK3, etc.).
> The integration uses the stable `/dev/serial/by-id/...` path to identify the correct adapter.
> Never use `/dev/ttyUSBx` — the index changes on reboot.

**Never stop the heater by cutting power or closing the serial port.** The heater requires a full
purge/cooldown cycle. Always use the proper STOP command and keep the heater powered until it
reports idle. The integration enforces this — closing the entry cleanly sends STOP and waits.

## Installation

### HACS

1. In HACS → Integrations → **⋮** → **Custom repositories**, add
   `https://github.com/PhilippF1992/ha-autoterm-usb` with category **Integration**.
2. Install **Autoterm USB**.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**, search for *Autoterm USB*.

### Manual

1. Copy `custom_components/autoterm/` into `/config/custom_components/`.
2. Restart Home Assistant.
3. Add via **Settings → Devices & services → Add integration → Autoterm USB**.

## Connecting the hardware

1. Connect the VAN PI USB adapter to the heater's 4-pin JST connector and to the Raspberry Pi.
2. In Home Assistant, check **Settings → System → Hardware → All Hardware** — look for a
   `/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_*` entry.
3. Only one process may hold the port. Stop any other scripts before setup.

## Setting up the integration

The setup wizard asks for:

- **Serial port** — dropdown of `/dev/serial/by-id/...` entries. The FTDI FT232R (VAN PI) entry
  is pre-selected. Use **Manual entry** if the device isn't listed.
- **Baud rate** — default **2400** (confirmed). Change only if your unit requires a different rate.
- **Poll interval** — default **5 s**. The heater is polled synchronously; do not go below 2 s.
- **Friendly name** — displayed in the HA UI.
- **Temperature sensor entity** *(optional)* — an HA sensor entity whose state will be fed to the
  heater as the panel temperature when **Temperature Source** is set to `ha_sensor`. Useful for
  using a room sensor instead of the heater's internal intake sensor.

### Options flow

**Settings → Devices & services → Autoterm USB → Configure** lets you change all of the above
(except the serial port) without removing the entry.

## Entities

All entities belong to a single device per heater.

### Climate

`climate.autoterm_usb` — the main control entity.

**HVAC modes:**

| Mode | Behaviour |
|---|---|
| **Off** | Sends STOP; heater completes purge/cooldown cycle |
| **Heat** | Starts the heater using the active heating preset |
| **Fan only** | Starts ventilation without combustion |

**Preset modes** (select via the preset picker):

| Preset | Behaviour |
|---|---|
| **By Temperature** | Heater regulates to a target temperature. HA feeds the selected temperature source to the heater as panel temp via `0x11` at every poll cycle. Target temperature range 0–30 °C. |
| **By Power** | Heater runs at a fixed power level (1–9). No temperature setpoint is used. |

> Preset cannot be changed while the heater is active. Send **Off** first.

**Current temperature:** always shows the value of the selected Temperature Source (with fallback
to the heater's internal sensor if the source is unavailable). In *By Temperature* mode this is
also the value being fed to the heater as panel temp.

### Select

| Entity | Description |
|---|---|
| **Temperature Source** | Which temperature value is shown on the climate card and (in *By Temperature* preset) fed to the heater as panel temp. Options: `internal` (heater intake sensor), `external` (heater's DS18B20 sensor, only shown when fitted), `ha_sensor` (the configured HA sensor entity). Available in all presets. Switching source requires no heater restart. |

### Number

| Entity | Description | Available when |
|---|---|---|
| **Power Level** | Fixed power level 1–9. Changing it while running updates the heater immediately. | *By Power* preset only |
| **Fan Speed** | Fan speed 1–9 for ventilation mode. Changing it re-sends the FAN_ONLY command. | Fan only mode active |

### Sensors

| Entity | Description |
|---|---|
| Internal Temperature | Ambient temperature near the heater body (°C) |
| External Temperature | Optional external sensor (°C); unavailable if no sensor fitted |
| Supply Voltage | Battery/supply voltage at the heater connector (V) |
| Heat Exchanger Temperature | Flame/heat-exchanger temperature (°C) |
| State | Human-readable state name (idle, warmup, running, shutdown, …) |
| Fault Code | Numeric fault code (0 = no fault) |
| Fault Description | Text description from the Autoterm fault code table |

### Binary sensors

| Entity | Description |
|---|---|
| Running | True while status1 = 3 (actively heating or ventilating) |
| Fault | True when any non-zero fault code is present |
| Lockout | True on fault code 33 — requires manual unlock procedure |
| External Temperature Sensor | True when an external sensor is fitted and responding |

## Services

### `autoterm.prime_fuel`

Runs a fuel priming sequence: briefly spins the fuel pump without ignition to push air out of
the fuel line. Use after the first installation or after running the tank dry.

```yaml
service: autoterm.prime_fuel
target:
  device_id: <your device id>
```

## Safety behaviour

- **No auto-restart on fault.** If a non-retryable fault code is present, the integration exposes
  it and requires explicit user action to start again.
- **Retryable faults** (codes 13, 30, 34) can be restarted without clearing first — these are
  typically caused by an empty fuel line or a serial port reconnect.
- **Lockout (code 33)** is surfaced as a distinct binary sensor. A normal start command will not
  clear it — the manual unlock procedure in the Autoterm installation manual must be followed.
- **Running locks:** HVAC mode switches and preset changes are blocked while the heater is active
  (starting, running, or stopping). Send **Off** first.
- **Debounce:** start and stop commands are rate-limited (5 s minimum between commands).
- **CRC validation:** every received frame is CRC-checked before any action is taken.
- **Purge/cooldown:** setting mode to Off sends STOP and keeps polling until the heater reports
  idle. The port is never closed early.

## Troubleshooting

- **Port not found** — check Hardware panel for the actual by-id path, then re-run the setup wizard.
- **No response / timeout** — confirm 2400 baud; another process may be holding the port.
- **Error code 13** on first cold start — normal. Fuel hasn't reached the burner yet; retry succeeds
  once the fuel line is primed. Use `autoterm.prime_fuel` to speed this up.
- **Error code 33 (Lockout)** — repeated ignition failures trigger a lockout. Follow the manual
  unlock procedure before attempting to start again.
- **After HA restart:** the integration may report error 30 or 34 briefly after reconnecting —
  these are retryable and clear on the next start attempt.

## Enable debug logging

```yaml
logger:
  logs:
    custom_components.autoterm: debug
```

## Confirmed protocol

| Command | Frame | Status |
|---|---|---|
| STATUS (poll) | `AA 03 00 00 0F 58 7C` | ✅ Confirmed |
| STOP | `AA 03 00 00 03 5D 7C` | ✅ Confirmed |
| START (by-power, level 2, 15 °C) | `AA 03 06 00 01 FF FF 04 0F 00 02 B8 5E` | ✅ Confirmed |
| GET SETTINGS | `AA 03 00 00 02 9D BD` | ✅ Confirmed |
| SET_TEMP (0x11) | `AA 03 01 00 11 [temp] [CRC_H] [CRC_L]` | ✅ Confirmed |
| FAN ONLY (0x23) | `AA 03 01 00 23 [fan_level] [CRC_H] [CRC_L]` | ✅ Confirmed |

See `design_documents/protocol.md` for the full frame specification and field map.

## Removing the integration

**Settings → Devices & services → Autoterm USB → ⋮ → Delete**. This sends STOP, waits for
cooldown to complete, then closes the serial port and removes all entities.
