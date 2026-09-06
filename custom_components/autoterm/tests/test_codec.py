"""
Offline unit tests for codec.py.
All frame fixtures are REAL captured values from protocol.md (live 2026-09-04)
or CONFIRMED values from the task spec.
No serial port, no hardware, no network.
Run with:  pytest custom_components/autoterm/tests/
"""

from __future__ import annotations

import pytest

# Stubs and sys.path are set up by the root conftest.py before collection.
from custom_components.autoterm.codec import (
    GET_SETTINGS_REQ,
    STATUS_REQ,
    STOP_CMD,
    SettingsPayload,
    build,
    build_fan_only,
    build_set_temp,
    build_start,
    build_write_settings,
    crc16,
    parse_settings,
    parse_status,
    read_frame_from_buffer,
)

# ── Confirmed frame fixtures (from protocol.md) ───────────────────────────────

# STATUS request (7 bytes): AA 03 00 00 0F 58 7C
STATUS_REQ_HEX = "aa0300000f587c"

# STOP command (7 bytes): AA 03 00 00 03 5D 7C
STOP_CMD_HEX = "aa030000035d7c"

# START level=9 (13 bytes): AA 03 06 00 01 FF FF 04 0F 00 09 7F 1F
START_L9_HEX = "aa03060001ffff040f00097f1f"

# START level=2 (13 bytes): AA 03 06 00 01 FF FF 04 0F 00 02 B8 5E
START_L2_HEX = "aa03060001ffff040f0002b85e"

# Idle STATUS response (17 bytes):
#   AA 04 0A 00 0F | 00 01 0D 13 7F 00 84 01 24 00 | 2D C0
#   status=0.1(idle) error=0x0D heat=19°C ext=none volt=13.2V flame=292K
STATUS_IDLE_HEX = "aa040a000f00010d137f00840124002dc0"

# STOP response (7 bytes): AA 04 00 00 03 29 7D
STOP_RESP_HEX = "aa04000003297d"


# ── CRC-16 tests ──────────────────────────────────────────────────────────────


def test_crc16_status_req_body():
    body = bytes.fromhex("aa0300000f")
    assert crc16(body) == bytes.fromhex("587c")


def test_crc16_stop_cmd_body():
    body = bytes.fromhex("aa03000003")
    assert crc16(body) == bytes.fromhex("5d7c")


def test_crc16_start_l9_body():
    body = bytes.fromhex("aa03060001ffff040f0009")
    assert crc16(body) == bytes.fromhex("7f1f")


def test_crc16_start_l2_body():
    body = bytes.fromhex("aa03060001ffff040f0002")
    assert crc16(body) == bytes.fromhex("b85e")


def test_crc16_idle_response():
    frame = bytes.fromhex(STATUS_IDLE_HEX)
    assert crc16(frame[:-2]) == frame[-2:]


# ── build() / constant frame tests ────────────────────────────────────────────


def test_constant_status_req():
    assert bytes.fromhex(STATUS_REQ_HEX) == STATUS_REQ


def test_constant_stop_cmd():
    assert bytes.fromhex(STOP_CMD_HEX) == STOP_CMD


def test_build_start_l9():
    assert build_start(level=9, setpoint=15, mode=0x04, ventilation=0) == bytes.fromhex(
        START_L9_HEX
    )


def test_build_start_l2():
    assert build_start(level=2, setpoint=15, mode=0x04, ventilation=0) == bytes.fromhex(
        START_L2_HEX
    )


def test_build_frame_structure():
    frame = build(0x0F)
    assert frame[0] == 0xAA
    assert frame[1] == 0x03
    assert frame[2] == 0x00
    assert frame[3] == 0x00
    assert frame[4] == 0x0F
    assert len(frame) == 7
    assert crc16(frame[:-2]) == frame[-2:]


def test_build_crc_appended_for_all_cmds():
    for cmd in (0x01, 0x02, 0x03, 0x06, 0x0F):
        frame = build(cmd)
        assert crc16(frame[:-2]) == frame[-2:], f"CRC wrong for cmd={cmd:#04x}"


# ── parse_status tests ────────────────────────────────────────────────────────


def test_parse_status_idle_frame():
    st = parse_status(bytes.fromhex(STATUS_IDLE_HEX))
    assert st is not None
    assert st.status1 == 0
    assert st.status2 == 1
    assert st.state_name == "idle"
    assert st.error == 0x0D
    assert st.heater_temp == 19
    assert st.ext_temp is None
    assert st.voltage == pytest.approx(13.2, abs=0.01)
    assert st.flame_k == 292
    assert st.flame_c == pytest.approx(292 - 273.15, abs=0.01)


def test_parse_status_rejects_bad_crc():
    frame = bytearray(bytes.fromhex(STATUS_IDLE_HEX))
    frame[-1] ^= 0xFF
    assert parse_status(bytes(frame)) is None


def test_parse_status_rejects_wrong_cmd():
    frame = bytearray(bytes.fromhex(STATUS_IDLE_HEX))
    frame[4] = 0x03
    frame[-2:] = crc16(bytes(frame[:-2]))
    assert parse_status(bytes(frame)) is None


def test_parse_status_rejects_truncated():
    assert parse_status(b"\xaa\x04\x0a\x00\x0f\x00\x01") is None


def test_parse_status_rejects_none_and_empty():
    assert parse_status(None) is None  # type: ignore[arg-type]
    assert parse_status(b"") is None


# ── HeaterStatus derived properties ───────────────────────────────────────────


def test_status_idle_properties():
    st = parse_status(bytes.fromhex(STATUS_IDLE_HEX))
    assert st.is_idle
    assert not st.is_running
    assert not st.is_starting
    assert not st.is_stopping
    assert not st.is_fan_only
    assert st.is_fault
    assert not st.is_lockout


def test_status_no_fault():
    frame = bytearray(bytes.fromhex(STATUS_IDLE_HEX))
    frame[5 + 2] = 0x00
    frame[-2:] = crc16(bytes(frame[:-2]))
    st = parse_status(bytes(frame))
    assert not st.is_fault


def test_status_lockout():
    frame = bytearray(bytes.fromhex(STATUS_IDLE_HEX))
    frame[5 + 2] = 33
    frame[-2:] = crc16(bytes(frame[:-2]))
    st = parse_status(bytes(frame))
    assert st.is_lockout
    assert st.is_fault


def test_status_ext_temp_present():
    frame = bytearray(bytes.fromhex(STATUS_IDLE_HEX))
    frame[5 + 4] = 0x12
    frame[-2:] = crc16(bytes(frame[:-2]))
    st = parse_status(bytes(frame))
    assert st.ext_temp == 18
    assert st.ext_temp_present


# ── read_frame_from_buffer tests ──────────────────────────────────────────────


def test_buffer_exact_frame():
    raw = bytearray(bytes.fromhex(STATUS_IDLE_HEX))
    frame, remaining = read_frame_from_buffer(raw)
    assert frame == bytes.fromhex(STATUS_IDLE_HEX)
    assert remaining == bytearray()


def test_buffer_junk_prefix_skipped():
    junk = bytearray([0x00, 0x11, 0xFF, 0xBB])
    raw = junk + bytearray(bytes.fromhex(STATUS_IDLE_HEX))
    frame, _ = read_frame_from_buffer(raw)
    assert frame == bytes.fromhex(STATUS_IDLE_HEX)


def test_buffer_two_frames():
    two = bytearray(bytes.fromhex(STATUS_IDLE_HEX) * 2)
    f1, rem = read_frame_from_buffer(two)
    assert f1 == bytes.fromhex(STATUS_IDLE_HEX)
    f2, rem2 = read_frame_from_buffer(rem)
    assert f2 == bytes.fromhex(STATUS_IDLE_HEX)
    assert rem2 == bytearray()


def test_buffer_partial_returns_none():
    partial = bytearray(bytes.fromhex(STATUS_IDLE_HEX)[:8])
    frame, _ = read_frame_from_buffer(partial)
    assert frame is None


def test_buffer_bad_crc_resyncs_to_next():
    bad = bytearray(bytes.fromhex(STATUS_IDLE_HEX))
    bad[-1] ^= 0xFF
    good = bytes.fromhex(STATUS_IDLE_HEX)
    raw = bad + bytearray(good)
    frame, _ = read_frame_from_buffer(raw)
    assert frame == good


# ── Round-trip: build synthetic response, parse back ─────────────────────────


def test_roundtrip_running_state():
    payload = bytes(
        [
            3,
            0,  # status1=3 (running), status2=0
            0x00,  # error = none
            0x2A,  # heater_temp = 42°C
            0x7F,  # ext_temp = no sensor
            0x00,  # unknown
            0x8C,  # voltage = 14.0 V  (0x8C=140 / 10)
            0x02,
            0x08,  # flame_k = 0x0208 = 520 K (247°C)
            0x01,  # activity byte (GUESSED field)
        ]
    )
    hdr = bytes([0xAA, 0x04, len(payload), 0x00, 0x0F])
    frame = hdr + payload + crc16(hdr + payload)

    st = parse_status(frame)
    assert st is not None
    assert st.is_running
    assert not st.is_fault
    assert st.heater_temp == 42
    assert st.ext_temp is None
    assert st.voltage == pytest.approx(14.0, abs=0.01)
    assert st.flame_k == 520
    assert st.flame_c == pytest.approx(520 - 273.15, abs=0.01)


# ── New command frames ────────────────────────────────────────────────────────
# SET_TEMP (0x11), WRITE_SETTINGS (0x02), GET_SETTINGS constant, FAN_ONLY (0x23)
# ─────────────────────────────────────────────────────────────────────────────


# SET_TEMP (0x11) — CONFIRMED: build_set_temp(20) == AA 03 01 00 11 14 B2 51
# Body = AA 03 01 00 11 14; CRC per task spec (confirmed on real hardware) = B2 51.
_SET_TEMP_20_BODY = bytes([0xAA, 0x03, 0x01, 0x00, 0x11, 0x14])
_SET_TEMP_20_CRC = bytes([0xB2, 0x51])


def test_crc16_set_temp_20_body():
    """CRC check: confirmed frame body for SET_TEMP(20)."""
    assert crc16(_SET_TEMP_20_BODY) == _SET_TEMP_20_CRC


def test_build_set_temp_20():
    """build_set_temp(20) must match the confirmed frame AA 03 01 00 11 14 B2 51."""
    frame = build_set_temp(20)
    assert frame == _SET_TEMP_20_BODY + _SET_TEMP_20_CRC


def test_build_set_temp_structure():
    """SET_TEMP frames have correct header, cmd=0x11, 1-byte payload."""
    for temp in (0, 15, 20, 30):
        frame = build_set_temp(temp)
        assert frame[0] == 0xAA
        assert frame[1] == 0x03
        assert frame[2] == 0x01  # payload_len=1
        assert frame[3] == 0x00
        assert frame[4] == 0x11  # cmd = SET_TEMP
        assert frame[5] == temp  # single-byte payload
        assert len(frame) == 8  # 5 header + 1 payload + 2 CRC
        assert crc16(frame[:-2]) == frame[-2:]


def test_build_set_temp_crc_valid_range():
    """CRC must be correct for the full 0–30°C range."""
    for temp in range(0, 31):
        frame = build_set_temp(temp)
        assert crc16(frame[:-2]) == frame[-2:], f"CRC wrong for SET_TEMP({temp})"


# ── GET_SETTINGS constant ──────────────────────────────────────────────────────

# From protocol.md: AA 03 00 00 02 9D BD
GET_SETTINGS_REQ_HEX = "aa030000029dbd"


def test_constant_get_settings_req():
    """GET_SETTINGS_REQ must match protocol.md: AA 03 00 00 02 9D BD."""
    assert bytes.fromhex(GET_SETTINGS_REQ_HEX) == GET_SETTINGS_REQ


def test_get_settings_req_crc_valid():
    assert crc16(GET_SETTINGS_REQ[:-2]) == GET_SETTINGS_REQ[-2:]


# ── WRITE_SETTINGS (0x02) ─────────────────────────────────────────────────────


def test_build_write_settings_structure():
    """WRITE_SETTINGS frame has correct header, cmd=0x02, 6-byte payload."""
    frame = build_write_settings(mode=0x04, setpoint=15, ventilation=0, power_level=5)
    assert frame[0] == 0xAA
    assert frame[1] == 0x03
    assert frame[2] == 0x06  # payload_len=6
    assert frame[3] == 0x00
    assert frame[4] == 0x02  # cmd = GET_SETTINGS (write variant)
    assert frame[5] == 0xFF  # reserved[0]
    assert frame[6] == 0xFF  # reserved[1]
    assert frame[7] == 0x04  # mode=by-power
    assert frame[8] == 0x0F  # setpoint=15°C
    assert frame[9] == 0x00  # ventilation=off
    assert frame[10] == 0x05  # power_level=5
    assert len(frame) == 13
    assert crc16(frame[:-2]) == frame[-2:]


def test_build_write_settings_crc_modes():
    """CRC must be correct for all four regulation modes."""
    for mode in (0x01, 0x02, 0x03, 0x04):
        frame = build_write_settings(mode=mode, setpoint=20, ventilation=0, power_level=5)
        assert crc16(frame[:-2]) == frame[-2:], f"CRC wrong for WRITE_SETTINGS mode={mode:#04x}"


# ── parse_settings round-trips ────────────────────────────────────────────────

# From protocol.md START response (confirmed): AA 04 06 00 01 00 78 04 0F 01 02 E3 4E
# mode=0x04 by-power, setpoint=15°C, ventilation=1, power_level=2
START_RESP_CONFIRMED_HEX = "aa040600010078040f0102e34e"


def test_parse_settings_from_start_response():
    """parse_settings accepts a confirmed START (0x01) response and decodes it correctly."""
    frame = bytes.fromhex(START_RESP_CONFIRMED_HEX)
    s = parse_settings(frame)
    assert s is not None
    assert isinstance(s, SettingsPayload)
    assert s.mode == 0x04
    assert s.setpoint == 0x0F  # 15°C
    assert s.ventilation == 0x01
    assert s.power_level == 0x02


def test_parse_settings_roundtrip_write():
    """Build a WRITE_SETTINGS frame, synthesise a plausible response, parse it back."""
    # Build the write frame — used to confirm payload byte layout only.
    # The heater echoes back the settings in 0x02 response (same payload layout).
    # Synthesise a response frame manually:
    payload = bytes([0x00, 0x78, 0x01, 0x14, 0x00, 0x07])  # mode=internal, 20°C, no vent, level=7
    hdr = bytes([0xAA, 0x04, len(payload), 0x00, 0x02])
    frame = hdr + payload + crc16(hdr + payload)

    s = parse_settings(frame)
    assert s is not None
    assert s.mode == 0x01
    assert s.setpoint == 0x14  # 20°C
    assert s.ventilation == 0x00
    assert s.power_level == 0x07


def test_parse_settings_rejects_bad_crc():
    payload = bytes([0x00, 0x78, 0x04, 0x0F, 0x00, 0x05])
    hdr = bytes([0xAA, 0x04, len(payload), 0x00, 0x02])
    frame = bytearray(hdr + payload + crc16(hdr + payload))
    frame[-1] ^= 0xFF
    assert parse_settings(bytes(frame)) is None


def test_parse_settings_rejects_truncated():
    assert parse_settings(b"\xaa\x04\x06\x00\x02\x00") is None


def test_parse_settings_rejects_none_and_empty():
    assert parse_settings(None) is None  # type: ignore[arg-type]
    assert parse_settings(b"") is None


def test_parse_settings_rejects_status_frame():
    """A STATUS response frame must not be parsed as settings."""
    assert parse_settings(bytes.fromhex(STATUS_IDLE_HEX)) is None


# ── FAN_ONLY (0x23) ──────────────────────────────────────────────────────────
# PORTED-BUT-UNVERIFIED: command not confirmed on 44D real hardware.
# Source: prclm (4D/44D reference) — payload FF FF <fan_level> 0x0F


def test_build_fan_only_structure():
    """FAN_ONLY frame has correct header, cmd=0x23, 4-byte payload per prclm."""
    frame = build_fan_only(5)
    assert frame[0] == 0xAA
    assert frame[1] == 0x03
    assert frame[2] == 0x04  # payload_len=4
    assert frame[3] == 0x00
    assert frame[4] == 0x23  # cmd = FAN_ONLY
    assert frame[5] == 0xFF  # reserved
    assert frame[6] == 0xFF  # reserved
    assert frame[7] == 0x05  # fan_level=5
    assert frame[8] == 0x0F  # last byte per prclm (k3mpaxl uses 0xFF)
    assert len(frame) == 11
    assert crc16(frame[:-2]) == frame[-2:]


def test_build_fan_only_crc_all_levels():
    """CRC must be correct for all valid fan levels 1–9."""
    for level in range(1, 10):
        frame = build_fan_only(level)
        assert crc16(frame[:-2]) == frame[-2:], f"CRC wrong for FAN_ONLY level={level}"


@pytest.mark.parametrize("level", range(1, 10))
def test_build_fan_only_level_encoding(level: int) -> None:
    """Fan speed must be encoded at payload[2] (frame byte 7) for every valid level.

    This is the critical byte the heater reads to set the fan RPM.  A re-send of
    the FAN_ONLY (0x23) frame is the only way to change speed while ventilating —
    there is no separate set-speed command.
    """
    frame = build_fan_only(level)
    assert frame[7] == level, f"Expected fan_level={level} at byte 7, got {frame[7]}"
    assert crc16(frame[:-2]) == frame[-2:]
