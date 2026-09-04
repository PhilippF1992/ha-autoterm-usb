"""
Offline unit tests for codec.py.
All frame fixtures are REAL captured values from protocol.md (live 2026-09-04).
No serial port, no hardware, no network.
Run with:  pytest custom_components/autoterm/tests/
"""
from __future__ import annotations

import pytest

# Stubs and sys.path are set up by the root conftest.py before collection.
from custom_components.autoterm.codec import (
    STATUS_REQ,
    STOP_CMD,
    build,
    build_start,
    crc16,
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
    body = bytes.fromhex("aa030000" "03")
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
    assert build_start(level=9, setpoint=15, mode=0x04, ventilation=0) == bytes.fromhex(START_L9_HEX)


def test_build_start_l2():
    assert build_start(level=2, setpoint=15, mode=0x04, ventilation=0) == bytes.fromhex(START_L2_HEX)


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
    assert st.status1     == 0
    assert st.status2     == 1
    assert st.state_name  == "idle"
    assert st.error       == 0x0D
    assert st.heater_temp == 19
    assert st.ext_temp    is None
    assert st.voltage     == pytest.approx(13.2, abs=0.01)
    assert st.flame_k     == 292
    assert st.flame_c     == pytest.approx(292 - 273.15, abs=0.01)


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
    raw  = junk + bytearray(bytes.fromhex(STATUS_IDLE_HEX))
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
    bad  = bytearray(bytes.fromhex(STATUS_IDLE_HEX))
    bad[-1] ^= 0xFF
    good = bytes.fromhex(STATUS_IDLE_HEX)
    raw  = bad + bytearray(good)
    frame, _ = read_frame_from_buffer(raw)
    assert frame == good


# ── Round-trip: build synthetic response, parse back ─────────────────────────

def test_roundtrip_running_state():
    payload = bytes([
        3, 0,       # status1=3 (running), status2=0
        0x00,       # error = none
        0x2A,       # heater_temp = 42°C
        0x7F,       # ext_temp = no sensor
        0x00,       # unknown
        0x8C,       # voltage = 14.0 V  (0x8C=140 / 10)
        0x02, 0x08, # flame_k = 0x0208 = 520 K (247°C)
        0x01,       # activity byte (GUESSED field)
    ])
    hdr   = bytes([0xAA, 0x04, len(payload), 0x00, 0x0F])
    frame = hdr + payload + crc16(hdr + payload)

    st = parse_status(frame)
    assert st is not None
    assert st.is_running
    assert not st.is_fault
    assert st.heater_temp == 42
    assert st.ext_temp    is None
    assert st.voltage     == pytest.approx(14.0, abs=0.01)
    assert st.flame_k     == 520
    assert st.flame_c     == pytest.approx(520 - 273.15, abs=0.01)
