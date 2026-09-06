"""
Autoterm USB — pure codec: CRC, frame build/parse.

No I/O anywhere in this module. Safe to import in tests and integration alike.
All confirmed field mappings are from protocol.md (live-captured 2026-09-04).
Fields marked GUESSED have not been verified against real running data.
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import (
    CMD_FAN_ONLY,
    CMD_GET_SETTINGS,
    CMD_SET_TEMP,
    CMD_START,
    CMD_STATUS,
    CMD_STOP,
    START_MODE_BY_POWER,
    STATE_NAMES,
)

# ── CRC-16 / Modbus ───────────────────────────────────────────────────────────


def crc16(data: bytes) -> bytes:
    """CRC-16/Modbus — polynomial 0xA001, init 0xFFFF, high byte first."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return bytes([crc >> 8, crc & 0xFF])


# ── Frame builder ─────────────────────────────────────────────────────────────


def build(cmd: int, payload: bytes = b"") -> bytes:
    """Build a controller→heater request frame.

    Layout: AA 03 <payload_len> 00 <cmd> [payload…] <CRC_H> <CRC_L>
    """
    hdr = bytes([0xAA, 0x03, len(payload), 0x00, cmd])
    body = hdr + payload
    return body + crc16(body)


# ── Pre-built constant frames (CONFIRMED against real hardware) ───────────────

STATUS_REQ: bytes = build(CMD_STATUS)  # AA 03 00 00 0F 58 7C
STOP_CMD: bytes = build(CMD_STOP)  # AA 03 00 00 03 5D 7C
GET_SETTINGS_REQ: bytes = build(CMD_GET_SETTINGS)  # AA 03 00 00 02 9D BD


def build_start(
    level: int,
    setpoint: int = 15,
    mode: int = START_MODE_BY_POWER,
    ventilation: int = 0,
) -> bytes:
    """
    Build a START (0x01) frame.

    mode=0x04 (by-power) is CONFIRMED. All temperature modes (0x01–0x03) are
    PORTED from reference repos but not independently confirmed on the 44D.
    ventilation=1 starts fan-only; use build_fan_only() for dedicated fan-only.

    Confirmed working frames:
      level=9:  AA 03 06 00 01 FF FF 04 0F 00 09 7F 1F
      level=2:  AA 03 06 00 01 FF FF 04 0F 00 02 B8 5E
    """
    payload = bytes(
        [
            0xFF,  # reserved
            0xFF,  # reserved
            mode & 0xFF,
            setpoint & 0xFF,
            ventilation & 0xFF,
            level & 0xFF,
        ]
    )
    return build(CMD_START, payload)


def build_set_temp(temp_c: int) -> bytes:
    """
    Build a SET_TEMP (0x11) frame. 1-byte payload = temperature in °C.

    This is used for two purposes:
      1. Setting the temperature setpoint directly.
      2. Feeding the current measured room temperature (panel mode, ~1 Hz).

    CONFIRMED frame: build_set_temp(20) → AA 03 01 00 11 14 B2 51
    Source: task spec (confirmed on real hardware) + prclm/AutotermHeaterController
            + k3mpaxl/pekaway-ha-autoterm (CMD_SET_TEMP = 0x11).
    """
    return build(CMD_SET_TEMP, bytes([temp_c & 0xFF]))


def build_write_settings(
    mode: int,
    setpoint: int,
    ventilation: int,
    power_level: int,
) -> bytes:
    """
    Build a WRITE SETTINGS (0x02) frame. 6-byte payload identical to START payload.

    PORTED-BUT-UNVERIFIED for the write path.
    Source: protocol.md (payload format) + prclm (reserved bytes FF FF for write).
    Read path (GET_SETTINGS_REQ) is confirmed per protocol.md.
    """
    payload = bytes(
        [
            0xFF,
            0xFF,
            mode & 0xFF,
            setpoint & 0xFF,
            ventilation & 0xFF,
            power_level & 0xFF,
        ]
    )
    return build(CMD_GET_SETTINGS, payload)


def build_fan_only(fan_level: int = 5) -> bytes:
    """
    Build a FAN_ONLY (0x23) frame. 4-byte payload.

    PORTED-BUT-UNVERIFIED on the 44D — not confirmed on real hardware.
    Source: prclm/AutotermHeaterController (4D/44D reference, last byte 0x0F).
    Note: k3mpaxl (2D reference) uses last byte 0xFF instead of 0x0F.
    We follow prclm as the 4D-specific reference.
    Sent TWICE (same as START) per prclm.
    """
    return build(CMD_FAN_ONLY, bytes([0xFF, 0xFF, fan_level & 0xFF, 0x0F]))


# ── Frame extractor (works on a buffer, no I/O) ───────────────────────────────


def read_frame_from_buffer(buf: bytearray) -> tuple[bytes | None, bytearray]:
    """
    Extract the first CRC-valid frame from a bytearray buffer.

    Returns (frame_bytes, remaining_buf).  frame_bytes is None if no
    complete valid frame is available yet; buf is advanced past consumed bytes.
    """
    while True:
        # Sync to 0xAA preamble
        while buf and buf[0] != 0xAA:
            buf.pop(0)
        if len(buf) < 3:
            return None, buf
        frame_len = 5 + buf[2] + 2  # header(5) + payload + CRC(2)
        if len(buf) < frame_len:
            return None, buf  # wait for more data
        frame = bytes(buf[:frame_len])
        if crc16(frame[:-2]) == frame[-2:]:
            del buf[:frame_len]
            return frame, buf
        buf.pop(0)  # bad CRC → drop preamble, re-sync


# ── Status payload decoder ────────────────────────────────────────────────────


@dataclass(frozen=True)
class HeaterStatus:
    # ── CONFIRMED fields (live-captured, protocol.md) ─────────────────────────
    status1: int  # primary state (0=idle,1=starting,2=warmup,3=running,4=shutdown)
    status2: int  # sub-state
    state_name: str  # human-readable from STATE_NAMES
    error: int  # fault code byte; 0 = no fault
    heater_temp: int  # °C — internal ambient sensor near heater
    ext_temp: int | None  # °C; None when byte is 0x7F (no sensor fitted)
    voltage: float  # supply voltage V (raw byte / 10)
    flame_k: int  # heat-exchanger temp in Kelvin (big-endian 2-byte)
    # payload_len preserved for diagnostics / future field mapping
    raw_payload: bytes

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def flame_c(self) -> float:
        return self.flame_k - 273.15

    @property
    def is_idle(self) -> bool:
        return self.status1 == 0

    @property
    def is_starting(self) -> bool:
        return self.status1 in (1, 2)

    @property
    def is_running(self) -> bool:
        return self.status1 == 3

    @property
    def is_stopping(self) -> bool:
        return self.status1 == 4

    @property
    def is_fan_only(self) -> bool:
        return self.status1 == 3 and self.status2 == 35

    @property
    def is_fault(self) -> bool:
        return self.error != 0

    @property
    def is_lockout(self) -> bool:
        return self.error == 33

    @property
    def ext_temp_present(self) -> bool:
        return self.ext_temp is not None


# CONFIRMED STATUS response payload length for Air 4D (idle + running observed)
_MIN_PAYLOAD = 9


def parse_status(frame: bytes) -> HeaterStatus | None:
    """
    Decode a STATUS (0x0F) response frame into a HeaterStatus.

    Returns None on: bad CRC, wrong frame type, truncated payload.
    CRC is re-validated here even though the caller may have already checked it —
    defence in depth: never act on a frame with bad CRC.
    """
    if not frame or len(frame) < 7:
        return None
    if crc16(frame[:-2]) != frame[-2:]:
        return None
    # Must be heater→controller response (type 0x04) to STATUS cmd (0x0F)
    if frame[1] != 0x04 or frame[4] != 0x0F:
        return None
    p = frame[5 : 5 + frame[2]]
    if len(p) < _MIN_PAYLOAD:
        return None

    s1, s2 = p[0], p[1]
    return HeaterStatus(
        status1=s1,
        status2=s2,
        state_name=STATE_NAMES.get((s1, s2), f"{s1}.{s2}"),
        error=p[2],
        heater_temp=p[3],
        ext_temp=None if p[4] == 0x7F else p[4],
        # p[5] = unknown (always 0x00 observed) — excluded from public fields
        voltage=p[6] / 10.0,
        flame_k=p[7] * 256 + p[8],
        # p[9] = GUESSED activity byte (0x00 idle, 0x01 starting, 0x05 fault)
        raw_payload=bytes(p),
    )


# ── Settings payload ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SettingsPayload:
    """Decoded heater settings from a 0x02 response frame."""

    mode: int  # 1=internal, 2=panel, 3=external, 4=by-power
    setpoint: int  # °C
    ventilation: int  # 0=off, 1=on
    power_level: int  # 1–9


def parse_settings(frame: bytes) -> SettingsPayload | None:
    """
    Decode a SETTINGS (0x02) response frame into a SettingsPayload.

    Also accepts a START (0x01) response frame since both share the same
    6-byte payload layout (protocol.md).

    Returns None on: bad CRC, wrong type byte, truncated payload.
    CRC re-validated for defence in depth.
    """
    if not frame or len(frame) < 7:
        return None
    if crc16(frame[:-2]) != frame[-2:]:
        return None
    if frame[1] != 0x04:
        return None
    if frame[4] not in (0x01, 0x02):
        return None
    p = frame[5 : 5 + frame[2]]
    if len(p) < 6:
        return None
    # p[0:2] = reserved (0x00 0x78 in observed responses)
    return SettingsPayload(
        mode=p[2],
        setpoint=p[3],
        ventilation=p[4],
        power_level=p[5],
    )
