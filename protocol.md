# AUTOTERM Air 4D — Serial Protocol Reference

Reverse-engineered from live captures (September 2026) and cross-referenced with:
- `prclm/AutotermHeaterController` (messages_controller.md + autoterm_heater.py)
- `schroeder-robert/autoterm-air-2d-serial-control` (README)
- `k3mpaxl/pekaway-ha-autoterm` (protocol.py / const.py)
- Autoterm/Planar installation manual (DE), autoterm24.de

---

## Physical connection

| Parameter | Value |
|---|---|
| Adapter | VAN PI USB-to-JST (replaces OEM control panel) |
| Chip | FTDI FT232R |
| USB VID:PID | `0403:6001` |
| Kernel driver | `ftdi_sio` |
| Stable device path | `/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_ABAKLQ9A-if00-port0` |
| Real node | `/dev/ttyUSB2` (index may change; always use by-id path) |
| Baud rate | **2400** |
| Frame format | 8N1 |
| Direction | Half-duplex, poll-response (heater is silent unless polled) |

> **Note:** 9600 baud is documented in some software repos but produces no response from this unit. 2400 baud is confirmed working.

---

## Frame structure

```
[AA] [type] [payload_len] [00] [cmd] [payload…] [CRC_H] [CRC_L]
  0     1         2         3    4       5…n       n+1     n+2
```

- **Preamble:** `0xAA`
- **type:** `0x03` = request (controller → heater), `0x04` = response (heater → controller)
- **payload_len:** number of payload bytes (0 if no payload)
- **byte 3:** always `0x00`
- **cmd:** command/message ID
- **CRC:** CRC-16/Modbus, polynomial `0xA001`, init `0xFFFF`, **high byte first**

### CRC-16 (Python)

```python
def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return bytes([(crc >> 8) & 0xFF, crc & 0xFF])

def build(cmd: int, payload: bytes = b"") -> bytes:
    header = bytes([0xAA, 0x03, len(payload), 0x00, cmd])
    body = header + payload
    return body + crc16(body)
```

---

## Commands

### STATUS — `0x0F` (poll)

Sent periodically (every 1–2 s). Heater only responds when polled.

**Request (7 bytes):**
```
AA 03 00 00 0F 58 7C
```

**Response (17 bytes, Air 4D):**
```
AA 04 0A 00 0F [10 bytes payload] [CRC_H] [CRC_L]
```

#### STATUS payload layout (10 bytes, confirmed on Air 4D)

| Offset | Field | Scaling | Notes |
|---|---|---|---|
| `[0]` | `status1` | — | Primary state (see table below) |
| `[1]` | `status2` | — | Sub-state (see table below) |
| `[2]` | `error_code` | — | Error/fault code; `0x00` = no error |
| `[3]` | `heater_temp` | °C direct | Internal sensor (ambient air near heater) |
| `[4]` | `ext_temp` | °C direct, `0x7F` = no sensor | External sensor (if fitted) |
| `[5]` | *(unknown)* | — | Always `0x00` observed |
| `[6]` | `voltage` | ÷ 10 → V | Supply voltage × 10 (e.g. `0x84` = 13.2 V) |
| `[7]` | `flame_temp_hi` | — | MSB of 16-bit flame sensor value |
| `[8]` | `flame_temp_lo` | — | LSB; combined = Kelvin (e.g. `0x0127` = 295 K = 22 °C) |
| `[9]` | *(unknown)* | — | Observed `0x00` idle, `0x01` during start, `0x05` on fault |

> **Air 4D vs Air 2D:** The Air 2D returns a 19-byte payload with additional fan RPM and
> fuel-pump frequency fields. The Air 4D returns 10 bytes. The core fields (offsets 0–8)
> appear to share the same layout.

#### State codes (`status1` . `status2`)

| status1 | status2 | Meaning |
|---|---|---|
| `0` | `1` | Standby / idle (off) |
| `1` | — | Starting |
| `2` | `0` | Warming up (glow plug pre-heat) |
| `2` | `1` | Heating glow plug |
| `2` | `2` | Ignition attempt 1 |
| `2` | `3` | Ignition attempt 2 |
| `2` | `4` | Heating combustion chamber |
| `2` | `7` | *(observed between 2.2 and 2.3 — undocumented)* |
| `3` | `0` | Running / heating |
| `3` | `35` | Fan-only (ventilation mode) |
| `3` | `4` | Cooling down |
| `4` | `0` | Shutting down |

#### Error codes (`error_code`, offset `[2]`)

| Code | Meaning |
|---|---|
| `0x00` | No error |
| `0x0D` (13) | Ignition failed / fuel not yet primed (normal on first cold start; retry usually succeeds once fuel reaches the burner) |
| `0x1E` (30) | Observed in idle after a failed ignition attempt — may be a cumulative start-attempt counter rather than a live fault |

> Error code 13 source: Autoterm/Planar installation manual (DE). As noted in the manual,
> this typically clears on the next start attempt once fuel has reached the heater.

---

### STOP — `0x03`

Initiates a safe shutdown and purge cycle. Must be sent and the heater must be kept powered
until status returns to `0.x`. Resend every ~10 s until idle if status does not clear.
**Never cut power as a substitute for STOP.**

**Request (7 bytes, no payload):**
```
AA 03 00 00 03 5D 7C
```

**Response (7 bytes):**
```
AA 04 00 00 03 29 7D
```

---

### START — `0x01`

Starts the heater. Must be sent **twice** in quick succession (< 1 s apart); the heater
responds to each. Use the same settings both times.

**Request (13 bytes):**
```
AA 03 06 00 01 FF FF [mode] [setpoint] [ventilation] [power_level] [CRC_H] [CRC_L]
```

**Payload fields:**

| Byte | Field | Values |
|---|---|---|
| `[0]` | *(reserved)* | `0xFF` |
| `[1]` | *(reserved)* | `0xFF` |
| `[2]` | `mode` | `0x01`=by heater temp, `0x02`=by controller temp, `0x03`=by external temp, `0x04`=by power |
| `[3]` | `setpoint` | Temperature in °C (ignored when mode=4) |
| `[4]` | `ventilation` | `0x00`=off, `0x01`=on |
| `[5]` | `power_level` | `0`–`9` (0=lowest, 9=highest) |

**Confirmed working frame — mode=by-power, level=2, setpoint=15 °C:**
```
AA 03 06 00 01 FF FF 04 0F 00 02 B8 5E
```

**Response (13 bytes — echoes settings back):**
```
AA 04 06 00 01 00 78 04 0F 01 02 E3 4E
```

---

### GET SETTINGS — `0x02`

Read current heater settings (no payload = read; with payload = write new settings).

**Read request (7 bytes):**
```
AA 03 00 00 02 9D BD
```

**Response payload layout** (same 6-byte format as START payload, offsets 2–5):

| Offset | Field |
|---|---|
| `[0]`–`[1]` | *(status/reserved bytes — `0x00 0x78` observed)* |
| `[2]` | mode |
| `[3]` | setpoint (°C) |
| `[4]` | ventilation |
| `[5]` | power_level |

---

### GET VERSION — `0x06`

**Request (7 bytes):**
```
AA 03 00 00 06 5E BC
```

**Response payload (5 bytes):** firmware version as `major.minor.patch.build` + blackbox version.

---

## Observed idle baseline

Captured 2026-09-04, heater cold (ambient ~15–20 °C), 12 V system:

```
AA 04 0A 00 0F 00 01 0D 13 7F 00 84 01 24 00 2D C0
```

Parsed:
- status: `0.1` (standby)
- heater_temp: 19 °C
- ext_temp: no sensor
- voltage: 13.2 V
- flame_temp: 292 K (19 °C ambient — no flame)
- error: `0x0D` (13, present at idle — likely historical fault counter)

---

## Startup sequence observed

| Time | State | Notes |
|---|---|---|
| T+0 s | START sent ×2 | Response: `AA 04 06 00 01 00 78 04 0F 01 02 E3 4E` |
| T+4 s | `2.0` | Warming up — glow plug heating, V drops to ~12.8 V |
| T+30 s | `2.1` | Heating glow plug |
| T+40 s | `2.2` | Ignition attempt 1 |
| T+90 s | `2.7` | Undocumented sub-state (brief) |
| T+95 s | `2.3` | Ignition attempt 2 |
| T+~5 min | `4.0` | Auto-shutdown: ignition failed (error 13 — first cold start, fuel not yet primed) |

> The `2.7` sub-state is not documented in any reference implementation. Appears briefly
> as a transition between `2.2` and `2.3`. Could be a retry or internal timing state.

---

## Integration notes

- **Always open port with `-hupcl clocal`** to prevent DTR toggling on open/close.
- **Poll at ~1–2 s cadence**; do not spam.
- **START must be sent twice** per the protocol.
- **STOP must be re-sent every ~10 s** during cooldown until `status1 == 0`.
- **Never power-cycle** as a substitute for STOP — the heater must complete its purge cycle.
- The heater will **auto-stop with error 13** on the first start if fuel hasn't reached the burner yet. A second start attempt shortly after typically succeeds.
- **pyserial** (Python) or `stty`+`printf`+`dd` (shell) both work. Shell approach confirmed on HAOS (no Python available on host).
