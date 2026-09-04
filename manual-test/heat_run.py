#!/usr/bin/env python3
"""
AUTOTERM Air 4D — 30-minute max-power heating run.

Behaviour
---------
1. Polls current status; aborts if no response.
2. Sends START at maximum power (level 9).
3. Monitors startup:
   - Error 13 (fuel not yet primed) → waits for full cooldown, retries.
   - Any other non-zero fault code  → clean STOP + exit(1).
4. Once running: polls every 10 s for the full RUN_MINUTES duration.
5. Sends STOP and polls through the complete purge/cooldown cycle.

Requirements: pyserial  (pip install pyserial)
"""

import serial
import sys
import time
import logging

# ── Configuration ─────────────────────────────────────────────────────────────

PORT        = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_ABAKLQ9A-if00-port0"
BAUD        = 2400
POWER_LEVEL = 9          # 0-9; 9 = maximum
RUN_MINUTES = 30
MAX_RETRIES = 20
LOG_FILE    = "/tmp/autoterm_run.log"

# ── CRC + frame helpers ───────────────────────────────────────────────────────

def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return bytes([crc >> 8, crc & 0xFF])

def build(cmd: int, payload: bytes = b"") -> bytes:
    hdr  = bytes([0xAA, 0x03, len(payload), 0x00, cmd])
    body = hdr + payload
    return body + crc16(body)

STATUS_REQ = build(0x0F)
STOP_CMD   = build(0x03)

def start_frame(level: int) -> bytes:
    """Build a START (0x01) frame: mode=by-power, setpoint=15 °C, level=<level>."""
    return build(0x01, bytes([0xFF, 0xFF, 0x04, 0x0F, 0x00, level & 0xFF]))

# ── Serial I/O ────────────────────────────────────────────────────────────────

def read_frame(port: serial.Serial, timeout: float = 3.0) -> bytes | None:
    """Read one CRC-valid frame starting with 0xAA within `timeout` seconds."""
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while time.monotonic() < deadline:
        chunk = port.read(32)
        if chunk:
            buf.extend(chunk)
        # Sync to preamble
        while buf and buf[0] != 0xAA:
            buf.pop(0)
        if len(buf) < 3:
            continue
        frame_len = 5 + buf[2] + 2   # header(5) + payload + CRC(2)
        if len(buf) < frame_len:
            continue
        frame = bytes(buf[:frame_len])
        if crc16(frame[:-2]) == frame[-2:]:
            return frame
        buf.pop(0)          # bad CRC — re-sync
    return None

def transact(port: serial.Serial, cmd: bytes, timeout: float = 3.0) -> bytes | None:
    """Flush input, send `cmd`, return the first valid response frame."""
    port.reset_input_buffer()
    port.write(cmd)
    return read_frame(port, timeout)

# ── Status decoding ───────────────────────────────────────────────────────────

_STATE_NAMES = {
    (0, 1): "idle",           (1, 0): "starting",
    (2, 0): "warmup",         (2, 1): "glow_plug",
    (2, 2): "ignition_1",     (2, 3): "ignition_2",
    (2, 4): "heat_chamber",   (2, 7): "ignition_retry",
    (3, 0): "running",        (3,35): "fan_only",
    (3, 4): "cooling_down",   (4, 0): "shutdown",
}

def parse_status(frame: bytes) -> dict | None:
    """Return a status dict from a raw STATUS response frame, or None."""
    if not frame or frame[4] != 0x0F:
        return None
    p = frame[5 : 5 + frame[2]]
    if len(p) < 9:
        return None
    s1, s2 = p[0], p[1]
    return {
        "status1":    s1,
        "status2":    s2,
        "name":       _STATE_NAMES.get((s1, s2), f"{s1}.{s2}"),
        "error":      p[2],
        "heater_temp":p[3],
        "ext_temp":   None if p[4] == 0x7F else p[4],
        "voltage":    p[6] / 10.0,
        "flame_k":    p[7] * 256 + p[8],
    }

def fmt_status(st: dict) -> str:
    flame_c = st["flame_k"] - 273
    ext = f"{st['ext_temp']}°C" if st["ext_temp"] is not None else "n/a"
    return (
        f"[{st['name']}] err={st['error']:#04x} "
        f"heat={st['heater_temp']}°C ext={ext} "
        f"volt={st['voltage']:.1f}V flame={st['flame_k']}K({flame_c}°C)"
    )

# ── High-level operations ─────────────────────────────────────────────────────

def poll_status(port: serial.Serial) -> dict | None:
    frame = transact(port, STATUS_REQ, timeout=3.0)
    return parse_status(frame) if frame else None

def wait_for_idle(
    port: serial.Serial,
    max_wait: int = 300,
    resend_stop: bool = True,
) -> bool:
    """Poll until status1 == 0 (idle). Re-sends STOP every 10 s if requested."""
    log.info("Waiting for idle (cooldown)…")
    last_stop_t = time.monotonic()
    deadline    = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        time.sleep(2)
        st = poll_status(port)
        if st is None:
            log.warning("  No response during cooldown poll")
            continue
        log.info(f"  cooldown {fmt_status(st)}")
        if st["status1"] == 0:
            log.info("Heater is idle.")
            return True
        if resend_stop and time.monotonic() - last_stop_t >= 10:
            transact(port, STOP_CMD, timeout=2.0)
            log.debug("  Re-sent STOP")
            last_stop_t = time.monotonic()
    log.error("Timed out waiting for idle!")
    return False

def do_stop(port: serial.Serial) -> None:
    """Send STOP once, then wait through the full cooldown cycle."""
    log.info("Sending STOP…")
    resp = transact(port, STOP_CMD, timeout=3.0)
    log.info(f"  STOP ack: {resp.hex() if resp else 'none'}")
    wait_for_idle(port, max_wait=300, resend_stop=True)

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    log.info(f"Opening {PORT} at {BAUD} baud")
    port = serial.Serial(
        PORT, BAUD,
        bytesize=8, parity="N", stopbits=1,
        timeout=0.5, write_timeout=2,
        rtscts=False, dsrdtr=False,
    )

    try:
        # ── Preflight status check ────────────────────────────────────────────
        st = poll_status(port)
        if st is None:
            log.error("No response — check connection and baud rate")
            return 1
        log.info(f"Initial status: {fmt_status(st)}")

        cmd = start_frame(POWER_LEVEL)
        log.info(f"START frame (level={POWER_LEVEL}): {cmd.hex(' ')}")

        # ── Start + retry loop ────────────────────────────────────────────────
        reached_running = False

        for attempt in range(1, MAX_RETRIES + 1):
            log.info(f"=== Start attempt {attempt}/{MAX_RETRIES} ===")

            # Protocol requires two identical START frames < 1 s apart
            r1 = transact(port, cmd, timeout=3.0)
            log.info(f"  START-1 ack: {r1.hex() if r1 else 'none'}")
            time.sleep(0.5)
            r2 = transact(port, cmd, timeout=3.0)
            log.info(f"  START-2 ack: {r2.hex() if r2 else 'none'}")

            fault_code  = None
            startup_end = time.monotonic() + 600   # 10-min startup window

            while time.monotonic() < startup_end:
                time.sleep(1.5)
                st = poll_status(port)
                if st is None:
                    log.warning("  No status response — retrying poll")
                    continue

                log.info(f"  {fmt_status(st)}")
                s1  = st["status1"]
                err = st["error"]

                # ── Running ──────────────────────────────────────────────────
                if s1 == 3:
                    log.info("*** HEATER IS RUNNING ***")
                    reached_running = True
                    break

                # ── Auto-shutdown ────────────────────────────────────────────
                if s1 == 4:
                    time.sleep(3)               # let error code settle
                    final = poll_status(port)
                    fault_code = (final or st)["error"]
                    log.info(f"  Shutdown detected — fault code {fault_code:#04x}")
                    break

                # ── Unexpected fault during warmup/ignition ──────────────────
                if err not in (0x00, 0x0D):
                    log.error(f"  Unexpected error {err:#04x} during startup")
                    fault_code = err
                    break

            if reached_running:
                break

            # ── Evaluate fault ────────────────────────────────────────────────
            if fault_code == 0x0D:
                log.info("Error 13: fuel not yet primed — waiting for cooldown, then retrying")
                wait_for_idle(port, max_wait=300, resend_stop=False)
                log.info("Retrying in 5 s…")
                time.sleep(5)
                continue

            if fault_code is not None and fault_code != 0x00:
                log.error(f"Non-recoverable fault {fault_code:#04x} — aborting")
                do_stop(port)
                return 1

            # Startup window expired without result
            log.error("Startup timed out (10 min) without reaching running state — aborting")
            do_stop(port)
            return 1

        if not reached_running:
            log.error(f"Heater did not start after {MAX_RETRIES} attempts")
            do_stop(port)
            return 1

        # ── Running phase: poll every 10 s for RUN_MINUTES ───────────────────
        run_end = time.monotonic() + RUN_MINUTES * 60
        log.info(f"Running for {RUN_MINUTES} min at power level {POWER_LEVEL}…")

        while time.monotonic() < run_end:
            time.sleep(10)
            st = poll_status(port)

            if st is None:
                log.error("Lost contact during run — emergency stop")
                do_stop(port)
                return 1

            remaining = int(run_end - time.monotonic())
            log.info(f"  [{remaining:4d}s left] {fmt_status(st)}")

            # Unexpected state transition during run
            if st["status1"] not in (2, 3):
                log.error(f"Unexpected state during run: {fmt_status(st)} — stopping")
                do_stop(port)
                return 1

            # Any non-zero error that isn't historical idle noise
            if st["error"] not in (0x00, 0x0D):
                log.error(f"Fault {st['error']:#04x} during run — stopping")
                do_stop(port)
                return 1

        # ── Planned shutdown ──────────────────────────────────────────────────
        log.info(f"{RUN_MINUTES}-minute run complete — clean shutdown")
        do_stop(port)
        return 0

    finally:
        if port.is_open:
            port.close()
        log.info("Port closed.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE),
        ],
    )
    log = logging.getLogger()
    sys.exit(main())
