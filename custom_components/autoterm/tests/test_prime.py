"""
Offline unit tests for the fuel-priming logic (prime.py).

No serial port, no hardware, no real HA instance.
All HeaterStatus objects are fabricated from dataclass constructors.
asyncio.sleep is patched to a no-op so tests complete instantly.

Run with:  pytest custom_components/autoterm/tests/
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.autoterm.codec import HeaterStatus
from custom_components.autoterm.prime import (
    PRIME_FAILED_LOCKOUT,
    PRIME_FAILED_MAX_ATTEMPTS,
    PRIME_FAILED_NOT_IDLE,
    PRIME_SUCCESS,
    PrimeResult,
    prime_fuel,
)

# ── Fixture helpers ───────────────────────────────────────────────────────────

_STATE = {
    (0, 0): "idle",
    (0, 1): "idle",
    (1, 0): "starting",
    (2, 0): "warmup",
    (3, 0): "running",
    (4, 0): "shutdown",
}


def make_status(
    status1: int,
    status2: int = 0,
    error: int = 0,
    heater_temp: int = 20,
    flame_k: int = 293,
    ext_temp: int | None = None,
    voltage: float = 13.2,
) -> HeaterStatus:
    return HeaterStatus(
        status1=status1,
        status2=status2,
        state_name=_STATE.get((status1, status2), f"{status1}.{status2}"),
        error=error,
        heater_temp=heater_temp,
        ext_temp=ext_temp,
        voltage=voltage,
        flame_k=flame_k,
        raw_payload=b"",
    )


def make_mock_client(poll_responses: list) -> AsyncMock:
    client = AsyncMock()
    client.poll_status = AsyncMock(side_effect=poll_responses)
    client.send_start = AsyncMock(return_value=True)
    client.send_stop = AsyncMock(return_value=True)
    return client


def make_mock_coordinator() -> MagicMock:
    coord = MagicMock()
    coord._priming = False
    coord._prime_task = None
    coord.prime_status = "idle"
    return coord


def make_mock_hass() -> MagicMock:
    hass = MagicMock()
    hass.bus.fire = MagicMock()
    return hass


# ── Tests ─────────────────────────────────────────────────────────────────────


@patch("custom_components.autoterm.prime.asyncio.sleep", new_callable=AsyncMock)
def test_error13_retry_until_max_attempts(mock_sleep):
    """
    Scenario: ECU reports error 13 on every attempt.
    Expected: prime_fuel returns MAX_ATTEMPTS after exhausting max_attempts.
    Error 13 retries MUST wait for cooldown; no shortcuts.
    """

    # ambient_k = 20 + 273 = 293; flame_k stays at 293 (no light-off)
    # Each attempt:
    #   - Inner poll: status1=4, err=0x0D  (ECU shutdown, error 13)
    #   - Settle poll: status1=0, err=0x0D  (heater cooling, error still set)
    #   - wait_for_idle poll: status1=0, err=0  (idle)
    def attempt_sequence():
        return [
            make_status(4, error=0x0D),  # inner loop: shutdown with err 13
            make_status(0, error=0x0D),  # settle poll
            make_status(0),  # wait_for_idle → idle
        ]

    max_attempts = 3
    poll_responses = [
        make_status(0, heater_temp=20, flame_k=293),  # preflight: idle
        *[s for _ in range(max_attempts) for s in attempt_sequence()],
    ]

    client = make_mock_client(poll_responses)
    coord = make_mock_coordinator()
    hass = make_mock_hass()

    result = asyncio.run(
        prime_fuel(
            client,
            hass,
            "test-entry-id",
            coord,
            max_attempts=max_attempts,
            stabilize_seconds=0,
            _startup_window=60.0,
            _cooldown_max_wait=30.0,
        )
    )

    assert result == PrimeResult.MAX_ATTEMPTS
    assert coord.prime_status == PRIME_FAILED_MAX_ATTEMPTS

    # START was sent max_attempts times
    assert client.send_start.call_count == max_attempts

    # No STOP call at the end (already idle from error-13 cooldown).
    assert client.send_stop.call_count == 0

    # HA events: one FINISHED event fired
    finished_events = [
        c for c in hass.bus.fire.call_args_list if c.args[0] == "autoterm_prime_finished"
    ]
    assert len(finished_events) == 1
    assert finished_events[0].args[1]["result"] == PrimeResult.MAX_ATTEMPTS.value


@patch("custom_components.autoterm.prime.asyncio.sleep", new_callable=AsyncMock)
def test_light_off_success(mock_sleep):
    """
    Scenario: First attempt triggers clean light-off (status1=3, flame_k above threshold).
    Expected: prime_fuel holds stabilize, stops cleanly, returns SUCCESS.
    """
    # ambient = 20°C → ambient_k = 293K, threshold = 293 + 40 = 333K
    # flame_k = 400K > 333K → confirmed
    poll_responses = [
        make_status(0, heater_temp=20, flame_k=293),  # preflight: idle
        make_status(3, flame_k=400),  # inner loop: running, lit!
        make_status(0),  # wait_for_idle after stop
    ]

    client = make_mock_client(poll_responses)
    coord = make_mock_coordinator()
    hass = make_mock_hass()

    result = asyncio.run(
        prime_fuel(
            client,
            hass,
            "test-entry-id",
            coord,
            max_attempts=5,
            flame_confirm_delta=40,
            stabilize_seconds=0,  # no stabilise hold
            _startup_window=60.0,
            _cooldown_max_wait=30.0,
        )
    )

    assert result == PrimeResult.SUCCESS
    assert coord.prime_status == PRIME_SUCCESS

    # START sent once; STOP sent once after stabilisation.
    assert client.send_start.call_count == 1
    assert client.send_stop.call_count == 1

    # FINISHED event with result=success
    finished_events = [
        c for c in hass.bus.fire.call_args_list if c.args[0] == "autoterm_prime_finished"
    ]
    assert len(finished_events) == 1
    assert finished_events[0].args[1]["result"] == PrimeResult.SUCCESS.value


@patch("custom_components.autoterm.prime.asyncio.sleep", new_callable=AsyncMock)
def test_light_off_below_threshold_does_not_confirm(mock_sleep):
    """
    Flame_k barely above ambient but below ambient_k + delta should NOT confirm.
    First poll: running but flame cold → continue.
    Second poll: running, flame above threshold → confirm.
    """
    # ambient_k = 293, threshold = 293 + 40 = 333K
    # Poll 2: flame_k=300 (< 333) → not confirmed, continue
    # Poll 3: flame_k=400 (> 333) → confirmed
    poll_responses = [
        make_status(0, heater_temp=20, flame_k=293),  # preflight
        make_status(3, flame_k=300),  # running but cold — not confirmed
        make_status(3, flame_k=400),  # running, confirmed
        make_status(0),  # wait_for_idle
    ]

    client = make_mock_client(poll_responses)
    coord = make_mock_coordinator()
    hass = make_mock_hass()

    result = asyncio.run(
        prime_fuel(
            client,
            hass,
            "test-entry-id",
            coord,
            max_attempts=2,
            flame_confirm_delta=40,
            stabilize_seconds=0,
            _startup_window=60.0,
            _cooldown_max_wait=30.0,
        )
    )

    assert result == PrimeResult.SUCCESS


@patch("custom_components.autoterm.prime.asyncio.sleep", new_callable=AsyncMock)
def test_lockout_aborts_immediately_no_retry(mock_sleep):
    """
    Scenario: ECU shuts down, settle poll shows error 33 (LOCKOUT).
    Expected: prime_fuel returns LOCKOUT immediately — does NOT retry.
    """
    poll_responses = [
        make_status(0, heater_temp=20),  # preflight: idle
        make_status(4, error=0),  # inner loop: ECU shutdown
        make_status(0, error=33),  # settle poll: lockout code
        make_status(0),  # wait_for_idle in lockout handler
    ]

    client = make_mock_client(poll_responses)
    coord = make_mock_coordinator()
    hass = make_mock_hass()

    result = asyncio.run(
        prime_fuel(
            client,
            hass,
            "test-entry-id",
            coord,
            max_attempts=6,  # high cap — must abort on first lockout regardless
            _startup_window=60.0,
            _cooldown_max_wait=30.0,
        )
    )

    assert result == PrimeResult.LOCKOUT
    assert coord.prime_status == PRIME_FAILED_LOCKOUT

    # Only ONE start attempt — no retry after lockout.
    assert client.send_start.call_count == 1
    # STOP called once (lockout handler).
    assert client.send_stop.call_count == 1

    finished_events = [
        c for c in hass.bus.fire.call_args_list if c.args[0] == "autoterm_prime_finished"
    ]
    assert len(finished_events) == 1
    payload = finished_events[0].args[1]
    assert payload["result"] == PrimeResult.LOCKOUT.value
    assert payload["fault_code"] == 33


@patch("custom_components.autoterm.prime.asyncio.sleep", new_callable=AsyncMock)
def test_lockout_during_warmup_aborts_immediately(mock_sleep):
    """
    Scenario: error 33 appears in error field during warmup (status1=2), before shutdown.
    Expected: immediate LOCKOUT abort — does NOT retry.
    """
    poll_responses = [
        make_status(0, heater_temp=20),  # preflight: idle
        make_status(2, error=33),  # warmup: lockout flag in error field
        make_status(0),  # wait_for_idle in lockout handler
    ]

    client = make_mock_client(poll_responses)
    coord = make_mock_coordinator()
    hass = make_mock_hass()

    result = asyncio.run(
        prime_fuel(
            client,
            hass,
            "test-entry-id",
            coord,
            max_attempts=6,
            _startup_window=60.0,
            _cooldown_max_wait=30.0,
        )
    )

    assert result == PrimeResult.LOCKOUT
    assert client.send_start.call_count == 1  # only one attempt


@patch("custom_components.autoterm.prime.asyncio.sleep", new_callable=AsyncMock)
def test_other_fault_stops_and_aborts(mock_sleep):
    """
    Scenario: ECU shuts down with error 3 (flame failure during operation).
    Expected: prime_fuel sends STOP, waits for cooldown, returns FAULT.  No retry.
    """
    poll_responses = [
        make_status(0, heater_temp=20),  # preflight
        make_status(4, error=0),  # ECU shutdown
        make_status(0, error=3),  # settle poll: fault 3
        make_status(0),  # wait_for_idle
    ]

    client = make_mock_client(poll_responses)
    coord = make_mock_coordinator()
    hass = make_mock_hass()

    result = asyncio.run(
        prime_fuel(
            client,
            hass,
            "test-entry-id",
            coord,
            max_attempts=6,
            _startup_window=60.0,
            _cooldown_max_wait=30.0,
        )
    )

    assert result == PrimeResult.FAULT

    assert client.send_start.call_count == 1  # no retry on non-retryable fault
    assert client.send_stop.call_count == 1  # clean stop

    finished_events = [
        c for c in hass.bus.fire.call_args_list if c.args[0] == "autoterm_prime_finished"
    ]
    assert len(finished_events) == 1
    assert finished_events[0].args[1]["result"] == PrimeResult.FAULT.value
    assert finished_events[0].args[1]["fault_code"] == 3


@patch("custom_components.autoterm.prime.asyncio.sleep", new_callable=AsyncMock)
def test_fault_during_warmup_stops_and_aborts(mock_sleep):
    """
    Scenario: Non-lockout fault appears in error field during warmup (before ECU shuts down).
    Expected: FAULT, no retry.
    """
    poll_responses = [
        make_status(0, heater_temp=20),  # preflight
        make_status(2, error=9),  # warmup: glow plug fault
        make_status(0),  # wait_for_idle
    ]

    client = make_mock_client(poll_responses)
    coord = make_mock_coordinator()
    hass = make_mock_hass()

    result = asyncio.run(
        prime_fuel(
            client,
            hass,
            "test-entry-id",
            coord,
            max_attempts=6,
            _startup_window=60.0,
            _cooldown_max_wait=30.0,
        )
    )

    assert result == PrimeResult.FAULT
    assert client.send_start.call_count == 1


@patch("custom_components.autoterm.prime.asyncio.sleep", new_callable=AsyncMock)
def test_heater_not_idle_rejected(mock_sleep):
    """
    Scenario: Heater is already running when prime_fuel is called.
    Expected: NOT_IDLE returned immediately; no START sent.
    """
    poll_responses = [
        make_status(3, flame_k=600),  # already running
    ]

    client = make_mock_client(poll_responses)
    coord = make_mock_coordinator()
    hass = make_mock_hass()

    result = asyncio.run(
        prime_fuel(
            client,
            hass,
            "test-entry-id",
            coord,
            _startup_window=60.0,
            _cooldown_max_wait=30.0,
        )
    )

    assert result == PrimeResult.NOT_IDLE
    assert coord.prime_status == PRIME_FAILED_NOT_IDLE
    assert client.send_start.call_count == 0


@patch("custom_components.autoterm.prime.asyncio.sleep", new_callable=AsyncMock)
def test_error13_then_success_on_second_attempt(mock_sleep):
    """
    Scenario: First attempt ends in error 13; second attempt lights off.
    Expected: SUCCESS, exactly 2 START calls.
    """
    # ambient_k = 293, threshold = 333K
    poll_responses = [
        make_status(0, heater_temp=20, flame_k=293),  # preflight
        # Attempt 1: error 13
        make_status(4, error=0x0D),  # shutdown + err13
        make_status(0, error=0x0D),  # settle
        make_status(0),  # wait_for_idle
        # Attempt 2: light-off
        make_status(3, flame_k=500),  # running + flame 500K > 333K → lit
        make_status(0),  # wait_for_idle after stop
    ]

    client = make_mock_client(poll_responses)
    coord = make_mock_coordinator()
    hass = make_mock_hass()

    result = asyncio.run(
        prime_fuel(
            client,
            hass,
            "test-entry-id",
            coord,
            max_attempts=3,
            flame_confirm_delta=40,
            stabilize_seconds=0,
            _startup_window=60.0,
            _cooldown_max_wait=30.0,
        )
    )

    assert result == PrimeResult.SUCCESS
    assert client.send_start.call_count == 2
    assert client.send_stop.call_count == 1


@patch("custom_components.autoterm.prime.asyncio.sleep", new_callable=AsyncMock)
def test_priming_flag_cleared_on_success(mock_sleep):
    """The _priming flag on the coordinator must be cleared in the finally block."""
    poll_responses = [
        make_status(0, heater_temp=20, flame_k=293),
        make_status(3, flame_k=400),
        make_status(0),
    ]

    client = make_mock_client(poll_responses)
    coord = make_mock_coordinator()
    hass = make_mock_hass()

    coord._priming = True  # simulate service handler having set this
    asyncio.run(
        prime_fuel(
            client,
            hass,
            "test-entry-id",
            coord,
            stabilize_seconds=0,
            _startup_window=60.0,
            _cooldown_max_wait=30.0,
        )
    )

    assert coord._priming is False  # finally block must clear it


@patch("custom_components.autoterm.prime.asyncio.sleep", new_callable=AsyncMock)
def test_priming_flag_cleared_on_fault(mock_sleep):
    """The _priming flag must be cleared even on non-recoverable fault."""
    poll_responses = [
        make_status(0, heater_temp=20),
        make_status(4, error=0),
        make_status(0, error=3),
        make_status(0),
    ]

    client = make_mock_client(poll_responses)
    coord = make_mock_coordinator()
    hass = make_mock_hass()

    coord._priming = True
    asyncio.run(
        prime_fuel(
            client,
            hass,
            "test-entry-id",
            coord,
            max_attempts=2,
            _startup_window=60.0,
            _cooldown_max_wait=30.0,
        )
    )

    assert coord._priming is False


@patch("custom_components.autoterm.prime.asyncio.sleep", new_callable=AsyncMock)
def test_cancellation_sends_stop(mock_sleep):
    """
    If the task is cancelled mid-run, prime_fuel must send STOP and re-raise.
    """
    call_count = 0

    async def poll_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return make_status(0, heater_temp=20)  # preflight: idle
        if call_count == 2:
            raise asyncio.CancelledError()  # cancel on first inner poll
        return make_status(0)  # subsequent polls in cleanup handler

    client = AsyncMock()
    client.poll_status = AsyncMock(side_effect=poll_side_effect)
    client.send_start = AsyncMock(return_value=True)
    client.send_stop = AsyncMock(return_value=True)

    coord = make_mock_coordinator()
    hass = make_mock_hass()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            prime_fuel(
                client,
                hass,
                "test-entry-id",
                coord,
                _startup_window=60.0,
                _cooldown_max_wait=30.0,
            )
        )

    # STOP must have been sent during cleanup.
    assert client.send_stop.call_count >= 1
    assert coord._priming is False
