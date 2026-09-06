"""Autoterm USB custom integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from .client import AutotermClient, AutotermClientError
from .const import (
    CONF_BAUD_RATE,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    DEFAULT_BAUD,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .coordinator import AutotermCoordinator
from .prime import prime_fuel

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.NUMBER,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    port = entry.data[CONF_PORT]
    baud = entry.options.get(CONF_BAUD_RATE, DEFAULT_BAUD)
    poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    client = AutotermClient(port=port, baud=int(baud))
    try:
        await client.connect()
    except AutotermClientError as exc:
        raise ConfigEntryNotReady(f"Cannot open {port}: {exc}") from exc

    coordinator = AutotermCoordinator(
        hass=hass,
        client=client,
        poll_interval=int(poll_interval),
        entry=entry,
    )

    # Initial poll
    await coordinator.async_config_entry_first_refresh()

    # Read current heater settings to initialise reg_source / target_temp / power_level
    await coordinator.async_refresh_settings()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Register the prime_fuel service once (shared across all config entries).
    if not hass.services.has_service(DOMAIN, "prime_fuel"):
        _async_register_prime_service(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: AutotermCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    # Cancel any in-flight prime task for this entry before unloading.
    if coordinator is not None:
        prime_task = coordinator._prime_task
        if prime_task is not None and not prime_task.done():
            prime_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await prime_task

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.disconnect()
        # Remove the service when no more config entries remain.
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "prime_fuel")
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


# ── prime_fuel service ────────────────────────────────────────────────────────


def _async_register_prime_service(hass: HomeAssistant) -> None:
    """Register the autoterm.prime_fuel service (called once per domain lifetime)."""
    import voluptuous as vol  # noqa: PLC0415  # lazy: only needed when HA is present

    schema = vol.Schema(
        {
            vol.Optional("power_level", default=2): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=9)
            ),
            vol.Optional("max_attempts", default=6): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=10)
            ),
            vol.Optional("flame_confirm_delta", default=40): vol.All(
                vol.Coerce(int), vol.Range(min=10, max=200)
            ),
            vol.Optional("stabilize_seconds", default=60): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=600)
            ),
        }
    )

    async def handle_prime_fuel(call: ServiceCall) -> None:
        domain_data: dict = hass.data.get(DOMAIN, {})

        # Identify which coordinator to target.
        if len(domain_data) == 0:
            raise HomeAssistantError("No Autoterm heater is configured")
        if len(domain_data) == 1:
            coordinator: AutotermCoordinator = next(iter(domain_data.values()))
        else:
            raise HomeAssistantError(
                "Multiple Autoterm heaters configured — "
                "autoterm.prime_fuel does not yet support multi-entry targeting"
            )

        if coordinator._priming:
            raise HomeAssistantError("A prime run is already in progress; wait for it to finish")

        coordinator._priming = True
        task = hass.async_create_task(
            prime_fuel(
                coordinator.client,
                hass,
                coordinator._entry.entry_id,
                coordinator,
                power_level=call.data["power_level"],
                max_attempts=call.data["max_attempts"],
                flame_confirm_delta=call.data["flame_confirm_delta"],
                stabilize_seconds=call.data["stabilize_seconds"],
            ),
            name="autoterm_prime_fuel",
        )
        coordinator._prime_task = task
        _LOGGER.debug("Prime task created: %s", task)

    hass.services.async_register(
        DOMAIN,
        "prime_fuel",
        handle_prime_fuel,
        schema=schema,
    )
