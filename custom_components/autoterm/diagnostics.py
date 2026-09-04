"""Diagnostics support for the Autoterm USB."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import AutotermCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: AutotermCoordinator = hass.data[DOMAIN][entry.entry_id]
    st = coordinator.data

    redacted_port = "**REDACTED**"  # don't expose full device path

    return {
        "config": {
            "port":    redacted_port,
            "options": dict(entry.options),
        },
        "last_update_success": coordinator.last_update_success,
        "status": (
            {
                "state_name":   st.state_name,
                "status1":      st.status1,
                "status2":      st.status2,
                "error_code":   st.error,
                "heater_temp":  st.heater_temp,
                "ext_temp":     st.ext_temp,
                "voltage":      st.voltage,
                "flame_k":      st.flame_k,
                "flame_c":      round(st.flame_c, 1),
                "raw_payload":  st.raw_payload.hex(),
            }
            if st is not None
            else None
        ),
    }
