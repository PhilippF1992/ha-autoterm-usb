"""
Async serial client for the Autoterm USB heater.

This module is the ONLY place that touches the serial port.
All commands are serialised through an asyncio.Lock.
No reconnect loop is spawned — callers detect unavailability via None returns
and let the coordinator mark entities unavailable until the next poll succeeds.
"""

from __future__ import annotations

import asyncio
import logging

from .codec import (
    GET_SETTINGS_REQ,
    STATUS_REQ,
    STOP_CMD,
    HeaterStatus,
    SettingsPayload,
    build_fan_only,
    build_set_temp,
    build_start,
    build_write_settings,
    parse_settings,
    parse_status,
    read_frame_from_buffer,
)
from .const import DEFAULT_BAUD, START_MODE_BY_POWER

_LOGGER = logging.getLogger(__name__)

_FRAME_TIMEOUT = 3.0  # seconds to wait for a response
_READ_CHUNK = 64  # bytes per asyncio read call


class AutotermClientError(Exception):
    """Raised when the client cannot communicate with the heater."""


class AutotermClient:
    """
    Owns exactly one serial port for the lifetime of a config entry.

    Thread-safety: all public methods are coroutines; call them from the
    HA event loop only.  The _lock serialises concurrent callers (e.g.
    coordinator poll arriving while the climate entity sends a command).
    """

    def __init__(self, port: str, baud: int = DEFAULT_BAUD) -> None:
        self._port = port
        self._baud = baud
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._connected = False

    # ── Connection management ─────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the serial port.  Raises on failure."""
        if self._connected:
            return
        try:
            import serial_asyncio_fast  # noqa: PLC0415  # runtime dep, may not exist at import time

            self._reader, self._writer = await serial_asyncio_fast.open_serial_connection(
                url=self._port,
                baudrate=self._baud,
                bytesize=8,
                parity="N",
                stopbits=1,
                rtscts=False,
                dsrdtr=False,
                exclusive=True,
            )
            self._connected = True
            _LOGGER.debug("Connected to %s at %d baud", self._port, self._baud)
        except Exception as exc:
            self._connected = False
            raise AutotermClientError(f"Cannot open {self._port}: {exc}") from exc

    async def disconnect(self) -> None:
        """Close the serial port cleanly."""
        self._connected = False
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            finally:
                self._writer = None
                self._reader = None
        _LOGGER.debug("Disconnected from %s", self._port)

    async def ensure_connected(self) -> bool:
        """Try to (re)connect if not currently connected.  Returns success."""
        if self._connected:
            return True
        try:
            await self.connect()
            return True
        except AutotermClientError as exc:
            _LOGGER.warning("Reconnect failed: %s", exc)
            return False

    # ── Low-level I/O ─────────────────────────────────────────────────────────

    async def _read_frame(self, timeout: float = _FRAME_TIMEOUT) -> bytes | None:
        """
        Read bytes from the serial port until a CRC-valid frame is assembled.

        Uses read_frame_from_buffer (codec) for preamble sync and CRC check.
        Returns None on timeout, EOF, or I/O error.
        """
        assert self._reader is not None
        buf = bytearray()
        try:
            async with asyncio.timeout(timeout):
                while True:
                    chunk = await self._reader.read(_READ_CHUNK)
                    if not chunk:
                        _LOGGER.warning("Serial EOF on %s", self._port)
                        return None
                    buf.extend(chunk)
                    frame, buf = read_frame_from_buffer(buf)
                    if frame is not None:
                        return frame
        except TimeoutError:
            return None
        except OSError as exc:
            _LOGGER.error("Serial read error on %s: %s", self._port, exc)
            self._connected = False
            return None

    async def _transact(self, cmd: bytes, timeout: float = _FRAME_TIMEOUT) -> bytes | None:
        """
        Send a command and return the first valid response frame.
        Must be called with self._lock already held.
        """
        assert self._writer is not None
        try:
            self._writer.write(cmd)
            await self._writer.drain()
        except OSError as exc:
            _LOGGER.error("Serial write error on %s: %s", self._port, exc)
            self._connected = False
            return None
        return await self._read_frame(timeout)

    # ── Public API ────────────────────────────────────────────────────────────

    async def transact(self, cmd: bytes, timeout: float = _FRAME_TIMEOUT) -> bytes | None:
        """Serialised command+response — safe to call from multiple coroutines."""
        async with self._lock:
            if not self._connected:
                return None
            return await self._transact(cmd, timeout)

    async def poll_status(self) -> HeaterStatus | None:
        """Send STATUS poll and return decoded HeaterStatus, or None."""
        frame = await self.transact(STATUS_REQ, timeout=_FRAME_TIMEOUT)
        if frame is None:
            return None
        st = parse_status(frame)
        if st is None:
            _LOGGER.warning("Received invalid STATUS frame: %s", frame.hex())
        return st

    async def send_stop(self) -> bool:
        """
        Send the STOP command.  Returns True if the heater acknowledged.
        The heater still needs its full purge/cooldown cycle; poll status
        until is_idle is True — do NOT close the port or cut power early.
        """
        resp = await self.transact(STOP_CMD, timeout=_FRAME_TIMEOUT)
        acked = resp is not None
        if not acked:
            _LOGGER.error("STOP command got no response — heater may be mid-cooldown")
        return acked

    async def send_start(
        self,
        level: int,
        setpoint: int = 15,
        mode: int = START_MODE_BY_POWER,
    ) -> bool:
        """
        Send the START command TWICE as the protocol requires (< 1 s apart).
        mode=START_MODE_BY_POWER (0x04) is CONFIRMED. Temperature modes are PORTED.
        Returns True if both sends were acknowledged.
        """
        cmd = build_start(level=level, setpoint=setpoint, mode=mode, ventilation=0)
        async with self._lock:
            if not self._connected:
                return False
            r1 = await self._transact(cmd)
            _LOGGER.debug("START-1 ack: %s", r1.hex() if r1 else "none")
            await asyncio.sleep(0.5)
            r2 = await self._transact(cmd)
            _LOGGER.debug("START-2 ack: %s", r2.hex() if r2 else "none")
        return r1 is not None and r2 is not None

    async def send_set_temp(self, temp_c: int) -> bool:
        """
        Send SET_TEMP (0x11) — sets the temperature setpoint or feeds the panel
        temperature to the heater (same frame for both purposes).

        CONFIRMED frame: send_set_temp(20) → AA 03 01 00 11 14 B2 51
        """
        resp = await self.transact(build_set_temp(temp_c))
        if resp is None:
            _LOGGER.debug("SET_TEMP(0x11) for %d°C got no ack", temp_c)
        return resp is not None

    async def send_write_settings(
        self,
        mode: int,
        setpoint: int,
        ventilation: int,
        power_level: int,
    ) -> bool:
        """
        Write heater settings via 0x02 frame.

        PORTED-BUT-UNVERIFIED for the write path. Read path (GET_SETTINGS_REQ) is confirmed.
        """
        cmd = build_write_settings(mode, setpoint, ventilation, power_level)
        resp = await self.transact(cmd)
        if resp is None:
            _LOGGER.warning("WRITE_SETTINGS(0x02) got no ack")
        return resp is not None

    async def get_settings(self) -> SettingsPayload | None:
        """
        Read current heater settings via 0x02 (no-payload request).
        Returns None if the heater doesn't respond or the response can't be parsed.
        """
        resp = await self.transact(GET_SETTINGS_REQ)
        if resp is None:
            _LOGGER.debug("GET_SETTINGS(0x02) got no response")
            return None
        settings = parse_settings(resp)
        if settings is None:
            _LOGGER.warning("GET_SETTINGS(0x02) response undecodable: %s", resp.hex())
        return settings

    async def send_fan_only(self, fan_level: int = 5) -> bool:
        """
        Send FAN_ONLY (0x23) command TWICE (mirrors START protocol).

        PORTED-BUT-UNVERIFIED: frame not confirmed on the 44D.
        Source: prclm (4D/44D reference). Last byte 0x0F per prclm; k3mpaxl uses 0xFF.
        """
        cmd = build_fan_only(fan_level)
        async with self._lock:
            if not self._connected:
                return False
            r1 = await self._transact(cmd)
            _LOGGER.debug("FAN_ONLY-1 ack: %s", r1.hex() if r1 else "none")
            await asyncio.sleep(0.5)
            r2 = await self._transact(cmd)
            _LOGGER.debug("FAN_ONLY-2 ack: %s", r2.hex() if r2 else "none")
        return r1 is not None and r2 is not None
