"""
Fuel-priming routine for the Autoterm USB heater.

Runs inside the integration — reuses the shared AutotermClient and its
asyncio.Lock.  Never opens a second serial connection.

Calling convention
------------------
Call prime_fuel() as an asyncio.Task so HA can cancel it on entry unload.
The coordinator's normal 1 Hz poll continues concurrently; both paths
serialise through client._lock so they never overlap on the wire.
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import TYPE_CHECKING

from .client import AutotermClient
from .codec import HeaterStatus
from .const import (
    DOMAIN,
    FAULT_CODES,
    FAULT_LOCKOUT,
    STOP_RESEND_INTERVAL,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import AutotermCoordinator

_LOGGER = logging.getLogger(__name__)

# ── Prime status strings (written to coordinator.prime_status) ────────────────

PRIME_IDLE = "idle"
PRIME_PRIMING = "priming"
PRIME_LIT = "lit"
PRIME_COOLING = "cooling"
PRIME_SUCCESS = "success"
PRIME_FAILED_MAX_ATTEMPTS = "failed:max_attempts"
PRIME_FAILED_LOCKOUT = "failed:err33"
PRIME_FAILED_FAULT = "failed:fault"
PRIME_FAILED_NOT_IDLE = "failed:not_idle"
PRIME_FAILED_CANCELLED = "failed:cancelled"

# ── Result enum ───────────────────────────────────────────────────────────────


class PrimeResult(str, Enum):
    SUCCESS = "success"
    MAX_ATTEMPTS = "failed:max_attempts"
    LOCKOUT = "failed:err33"
    FAULT = "failed:fault"
    NOT_IDLE = "failed:not_idle"
    CANCELLED = "failed:cancelled"


# ── Event names ───────────────────────────────────────────────────────────────

EVENT_PRIME_PROGRESS = "autoterm_prime_progress"
EVENT_PRIME_FINISHED = "autoterm_prime_finished"

# ── Internal timing constants ─────────────────────────────────────────────────

_POLL_INTERVAL = 1.5     # seconds between polls in the inner monitoring loop
_STARTUP_WINDOW = 600    # maximum seconds to wait for ignition per attempt
_COOLDOWN_MAX_WAIT = 300 # maximum seconds to wait for idle after stop


# ── Event helpers ─────────────────────────────────────────────────────────────


def _fire_progress(
    hass: HomeAssistant,
    entry_id: str,
    attempt: int,
    max_attempts: int,
    status: HeaterStatus,
) -> None:
    hass.bus.fire(
        EVENT_PRIME_PROGRESS,
        {
            "entry_id": entry_id,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "state_name": status.state_name,
            "flame_k": status.flame_k,
            "error": status.error,
        },
    )


def _fire_finished(
    hass: HomeAssistant,
    entry_id: str,
    result: PrimeResult,
    attempt: int,
    status: HeaterStatus | None = None,
    fault_code: int | None = None,
) -> None:
    data: dict = {"entry_id": entry_id, "result": result.value, "attempt": attempt}
    if status is not None:
        data.update(
            {
                "state_name": status.state_name,
                "flame_k": status.flame_k,
                "error": status.error,
            }
        )
    if fault_code is not None:
        title, desc = FAULT_CODES.get(fault_code, (f"Error {fault_code:#04x}", "Unknown"))
        data["fault_code"] = fault_code
        data["fault_title"] = title
        data["fault_description"] = desc
    hass.bus.fire(EVENT_PRIME_FINISHED, data)


# ── Cooldown wait ─────────────────────────────────────────────────────────────


async def _wait_for_idle(
    client: AutotermClient,
    *,
    max_wait: float = _COOLDOWN_MAX_WAIT,
    resend_stop: bool = True,
) -> bool:
    """Poll until is_idle or timeout.  Re-sends STOP every STOP_RESEND_INTERVAL s."""
    _LOGGER.info("Prime: waiting for idle (cooldown)…")
    loop = asyncio.get_event_loop()
    last_stop_t = loop.time()
    deadline = loop.time() + max_wait
    while loop.time() < deadline:
        await asyncio.sleep(2)
        st = await client.poll_status()
        if st is None:
            _LOGGER.warning("Prime: no response during cooldown poll")
            continue
        _LOGGER.info(
            "Prime cooldown: state=%s err=%#04x flame=%dK",
            st.state_name,
            st.error,
            st.flame_k,
        )
        if st.is_idle:
            _LOGGER.info("Prime: heater is idle.")
            return True
        if resend_stop and loop.time() - last_stop_t >= STOP_RESEND_INTERVAL:
            await client.send_stop()
            _LOGGER.debug("Prime: re-sent STOP during cooldown")
            last_stop_t = loop.time()
    _LOGGER.error("Prime: timed out waiting for idle!")
    return False


# ── Main priming coroutine ────────────────────────────────────────────────────


async def prime_fuel(
    client: AutotermClient,
    hass: HomeAssistant,
    entry_id: str,
    coordinator: AutotermCoordinator,
    *,
    power_level: int = 2,
    max_attempts: int = 6,
    flame_confirm_delta: int = 40,
    stabilize_seconds: int = 60,
    _startup_window: float = _STARTUP_WINDOW,
    _cooldown_max_wait: float = _COOLDOWN_MAX_WAIT,
) -> PrimeResult:
    """
    Prime the fuel line using the shared serial client.

    Shares client._lock with the coordinator's poll — does NOT open a second
    serial connection.  Must run as an asyncio.Task so CancelledError is caught.

    Parameters
    ----------
    power_level        : 1–9.  Default 2 — low power is enough for priming.
    max_attempts       : hard cap on ignition retries. Default 6.
    flame_confirm_delta: °C rise on flame_k above ambient to confirm light-off.
    stabilize_seconds  : hold time after confirmed light-off before STOP.
    _startup_window    : (test hook) seconds per attempt before timeout.
    _cooldown_max_wait : (test hook) seconds to wait for idle after stop.
    """
    coordinator.prime_status = PRIME_PRIMING

    # Last known status — updated throughout so we always have something to report.
    last_st: HeaterStatus | None = None

    try:
        # ── Preflight: require idle ───────────────────────────────────────────
        last_st = await client.poll_status()
        if last_st is None:
            _LOGGER.error("Prime: no response from heater — aborting")
            coordinator.prime_status = PRIME_FAILED_NOT_IDLE
            _fire_finished(hass, entry_id, PrimeResult.NOT_IDLE, 0)
            return PrimeResult.NOT_IDLE

        if not last_st.is_idle:
            _LOGGER.warning(
                "Prime: heater is not idle (%s) — refusing to start",
                last_st.state_name,
            )
            coordinator.prime_status = PRIME_FAILED_NOT_IDLE
            _fire_finished(hass, entry_id, PrimeResult.NOT_IDLE, 0, last_st)
            return PrimeResult.NOT_IDLE

        # Capture ambient in Kelvin now — flame_k is Kelvin, delta is in K (== °C for diffs).
        ambient_k = last_st.heater_temp + 273

        _LOGGER.info(
            "Prime: starting — power=%d max_attempts=%d "
            "flame_threshold=%dK stabilize=%ds ambient=%d°C",
            power_level,
            max_attempts,
            ambient_k + flame_confirm_delta,
            stabilize_seconds,
            last_st.heater_temp,
        )

        lit = False
        attempt = 0

        # ── Attempt loop ──────────────────────────────────────────────────────
        for attempt in range(1, max_attempts + 1):
            coordinator.prime_status = f"attempt {attempt}/{max_attempts}"
            _LOGGER.info("Prime: === attempt %d/%d ===", attempt, max_attempts)

            started = await client.send_start(level=power_level)
            if not started:
                _LOGGER.warning(
                    "Prime: START not acknowledged on attempt %d — continuing anyway",
                    attempt,
                )

            fault_code: int | None = None
            loop = asyncio.get_event_loop()
            startup_deadline = loop.time() + _startup_window

            # ── Inner monitoring loop ─────────────────────────────────────────
            while loop.time() < startup_deadline:
                await asyncio.sleep(_POLL_INTERVAL)
                st = await client.poll_status()
                if st is None:
                    _LOGGER.warning("Prime: no status response — retrying poll")
                    continue
                last_st = st

                _fire_progress(hass, entry_id, attempt, max_attempts, st)
                _LOGGER.info(
                    "Prime: attempt %d/%d state=%s err=%#04x flame=%dK",
                    attempt,
                    max_attempts,
                    st.state_name,
                    st.error,
                    st.flame_k,
                )

                # ── Light-off confirmed ───────────────────────────────────────
                if st.is_running and st.flame_k > ambient_k + flame_confirm_delta:
                    _LOGGER.info(
                        "Prime: *** FLAME CONFIRMED *** flame=%dK threshold=%dK",
                        st.flame_k,
                        ambient_k + flame_confirm_delta,
                    )
                    lit = True
                    break

                # ── ECU shutdown — read final fault code ──────────────────────
                if st.status1 == 4:
                    await asyncio.sleep(3)  # let ECU settle the error register
                    final = await client.poll_status()
                    # Prefer the post-settle read; fall back to the shutdown frame.
                    fault_code = (final.error if final is not None else st.error)
                    if fault_code == 0 and st.error != 0:
                        fault_code = st.error
                    if final is not None:
                        last_st = final
                    _LOGGER.info("Prime: ECU shutdown — fault %#04x", fault_code)
                    break

                # ── Lockout during warmup (before ECU shuts down) ─────────────
                if st.error == FAULT_LOCKOUT:
                    fault_code = FAULT_LOCKOUT
                    _LOGGER.error("Prime: LOCKOUT (err 33) during warmup — aborting")
                    break

                # ── Other fault during warmup ─────────────────────────────────
                if st.error not in (0x00, 0x0D):
                    fault_code = st.error
                    _LOGGER.error(
                        "Prime: unexpected fault %#04x during warmup",
                        st.error,
                    )
                    break

            if lit:
                break

            # ── Evaluate the fault after this attempt ─────────────────────────

            if fault_code == FAULT_LOCKOUT:
                _LOGGER.error(
                    "Prime: LOCKOUT — manual unlock required; NOT retrying"
                )
                coordinator.prime_status = PRIME_FAILED_LOCKOUT
                await client.send_stop()
                await _wait_for_idle(client, max_wait=_cooldown_max_wait, resend_stop=True)
                _fire_finished(
                    hass, entry_id, PrimeResult.LOCKOUT, attempt, last_st, FAULT_LOCKOUT
                )
                return PrimeResult.LOCKOUT

            if fault_code == 0x0D:
                # Error 13 = ECU tried twice, fuel not at burner yet — normal on dry line.
                _LOGGER.info(
                    "Prime: error 13 on attempt %d/%d "
                    "(fuel not yet at burner) — cooling down, then retrying",
                    attempt,
                    max_attempts,
                )
                # Full fan purge before next attempt — do NOT skip this.
                await _wait_for_idle(
                    client, max_wait=_cooldown_max_wait, resend_stop=False
                )
                await asyncio.sleep(5)
                continue  # next attempt

            if fault_code is not None and fault_code != 0x00:
                title, _ = FAULT_CODES.get(fault_code, (f"Error {fault_code:#04x}", ""))
                _LOGGER.error(
                    "Prime: non-recoverable fault %#04x (%s) — aborting",
                    fault_code,
                    title,
                )
                coordinator.prime_status = PRIME_FAILED_FAULT
                await client.send_stop()
                await _wait_for_idle(client, max_wait=_cooldown_max_wait, resend_stop=True)
                _fire_finished(
                    hass, entry_id, PrimeResult.FAULT, attempt, last_st, fault_code
                )
                return PrimeResult.FAULT

            # fault_code is None or 0 → startup window expired without result.
            _LOGGER.error(
                "Prime: startup window expired on attempt %d/%d — aborting",
                attempt,
                max_attempts,
            )
            coordinator.prime_status = PRIME_FAILED_FAULT
            await client.send_stop()
            await _wait_for_idle(client, max_wait=_cooldown_max_wait, resend_stop=True)
            _fire_finished(hass, entry_id, PrimeResult.FAULT, attempt, last_st)
            return PrimeResult.FAULT

        # ── for loop exhausted (all attempts ended in error 13) ───────────────
        if not lit:
            _LOGGER.error(
                "Prime: heater did not light after %d attempt(s) — giving up",
                max_attempts,
            )
            coordinator.prime_status = PRIME_FAILED_MAX_ATTEMPTS
            # Heater is already idle — we waited for cooldown after the last error-13.
            _fire_finished(hass, entry_id, PrimeResult.MAX_ATTEMPTS, attempt, last_st)
            return PrimeResult.MAX_ATTEMPTS

        # ── Flame confirmed — stabilise then stop ─────────────────────────────
        coordinator.prime_status = PRIME_LIT
        _LOGGER.info(
            "Prime: flame confirmed on attempt %d — holding %ds for stabilisation",
            attempt,
            stabilize_seconds,
        )
        loop = asyncio.get_event_loop()
        stabilize_end = loop.time() + stabilize_seconds
        while loop.time() < stabilize_end:
            await asyncio.sleep(_POLL_INTERVAL)
            st = await client.poll_status()
            if st is not None:
                last_st = st
                _fire_progress(hass, entry_id, attempt, max_attempts, st)
                _LOGGER.info(
                    "Prime: stabilising state=%s flame=%dK [%ds left]",
                    st.state_name,
                    st.flame_k,
                    int(stabilize_end - loop.time()),
                )

        coordinator.prime_status = PRIME_COOLING
        _LOGGER.info("Prime: sending STOP and waiting for cooldown…")
        await client.send_stop()
        await _wait_for_idle(client, max_wait=_cooldown_max_wait, resend_stop=True)

        coordinator.prime_status = PRIME_SUCCESS
        _LOGGER.info(
            "Prime: SUCCESS — fuel line primed after %d attempt(s)", attempt
        )
        _fire_finished(hass, entry_id, PrimeResult.SUCCESS, attempt, last_st)
        return PrimeResult.SUCCESS

    except asyncio.CancelledError:
        _LOGGER.warning("Prime: cancelled — sending STOP and cleaning up")
        coordinator.prime_status = PRIME_FAILED_CANCELLED
        try:
            await client.send_stop()
            await _wait_for_idle(client, max_wait=_cooldown_max_wait, resend_stop=True)
        except Exception:  # noqa: BLE001
            pass
        _fire_finished(hass, entry_id, PrimeResult.CANCELLED, attempt)
        raise

    finally:
        coordinator._priming = False
