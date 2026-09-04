"""Autoterm USB custom integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import AutotermClient, AutotermClientError
from .const import CONF_BAUD_RATE, CONF_POLL_INTERVAL, CONF_PORT, DEFAULT_BAUD, DEFAULT_POLL_INTERVAL, DOMAIN
from .coordinator import AutotermCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    port          = entry.data[CONF_PORT]
    baud          = entry.options.get(CONF_BAUD_RATE, DEFAULT_BAUD)
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
    )

    # Initial poll — raises ConfigEntryNotReady if the heater doesn't respond
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: AutotermCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.disconnect()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)
