"""Config flow for the Autoterm USB integration."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_BAUD_RATE,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    CONF_TEMP_SOURCE_ENTITY,
    DEFAULT_BAUD,
    DEFAULT_NAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    HINT_PORT,
)

_LOGGER = logging.getLogger(__name__)

_MANUAL_ENTRY = "__manual__"
_BY_ID_DIR = Path("/dev/serial/by-id")


def _list_by_id_ports() -> list[tuple[str, str]]:
    """
    Return (path, label) pairs for all /dev/serial/by-id symlinks.
    Blocking filesystem call — run in executor.
    Never opens any device.
    """
    if not _BY_ID_DIR.exists():
        return []
    return sorted(
        (str(_BY_ID_DIR / e.name), e.name) for e in _BY_ID_DIR.iterdir() if e.is_symlink()
    )


def _extract_unique_id(port: str) -> str:
    """
    Build a stable unique_id from the FTDI serial embedded in a by-id path,
    falling back to a sanitised version of the full path.
    """
    m = re.search(r"FTDI_FT232R_USB_UART_([A-Z0-9]+)", port)
    if m:
        return f"autoterm_ftdi_{m.group(1).lower()}"
    # Fallback: use the last path component, keeping it alphanumeric
    slug = re.sub(r"[^a-z0-9]", "_", Path(port).name.lower())
    return f"autoterm_{slug}"


def _port_label(port: str) -> str:
    """Short human-readable label for a serial port path."""
    name = Path(port).name
    if "FTDI" in name:
        return f"FTDI FT232R — {name}"
    return name


class AutotermConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Config flow:
      Step 1 (user)   — choose serial port from /dev/serial/by-id dropdown or manual entry
      Step 2 (manual) — text field for manual port path (only if "manual" chosen)
      Step 3 (options_init) — baud rate, poll interval, friendly name
    """

    VERSION = 1

    def __init__(self) -> None:
        self._port: str | None = None
        self._ports: list[tuple[str, str]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 1: present port selection dropdown."""
        self._ports = await self.hass.async_add_executor_job(_list_by_id_ports)

        # Pre-select the FTDI entry if one exists
        default_port = next((p for p, _ in self._ports if "FTDI" in p), _MANUAL_ENTRY)

        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = user_input[CONF_PORT]
            if chosen == _MANUAL_ENTRY:
                return await self.async_step_manual()
            self._port = chosen
            return await self.async_step_settings()

        options: list[SelectOptionDict] = [
            SelectOptionDict(value=p, label=_port_label(p)) for p, _ in self._ports
        ]
        options.append(SelectOptionDict(value=_MANUAL_ENTRY, label="Manual entry…"))

        schema = vol.Schema(
            {
                vol.Required(CONF_PORT, default=default_port): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 2 (optional): manual port path entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            port = user_input[CONF_PORT].strip()
            if not port:
                errors[CONF_PORT] = "port_required"
            elif not (Path(port).exists() or Path(port).is_symlink()):
                errors[CONF_PORT] = "port_not_found"
            else:
                self._port = port
                return await self.async_step_settings()

        schema = vol.Schema(
            {
                vol.Required(CONF_PORT, default=HINT_PORT): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                )
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Step 3: baud rate, poll interval, friendly name."""
        assert self._port is not None

        if user_input is not None:
            unique_id = _extract_unique_id(self._port)
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            options: dict = {
                CONF_BAUD_RATE: user_input[CONF_BAUD_RATE],
                CONF_POLL_INTERVAL: user_input[CONF_POLL_INTERVAL],
            }
            if user_input.get(CONF_TEMP_SOURCE_ENTITY):
                options[CONF_TEMP_SOURCE_ENTITY] = user_input[CONF_TEMP_SOURCE_ENTITY]

            return self.async_create_entry(
                title=user_input.get("name", DEFAULT_NAME),
                data={CONF_PORT: self._port},
                options=options,
            )

        schema = vol.Schema(
            {
                vol.Optional("name", default=DEFAULT_NAME): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Required(CONF_BAUD_RATE, default=DEFAULT_BAUD): NumberSelector(
                    NumberSelectorConfig(min=1200, max=115200, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): NumberSelector(
                    NumberSelectorConfig(min=2, max=60, step=1, mode=NumberSelectorMode.SLIDER)
                ),
                vol.Optional(CONF_TEMP_SOURCE_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                ),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return AutotermOptionsFlow(config_entry)


class AutotermOptionsFlow(config_entries.OptionsFlow):
    """Allow baud rate, poll interval, and source entity to be changed."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        opts = self._config_entry.options
        if user_input is not None:
            # Strip empty string for optional entity selector
            if not user_input.get(CONF_TEMP_SOURCE_ENTITY):
                user_input.pop(CONF_TEMP_SOURCE_ENTITY, None)
            return self.async_create_entry(title="", data=user_input)

        current_source = opts.get(CONF_TEMP_SOURCE_ENTITY)

        schema_dict: dict = {
            vol.Required(
                CONF_BAUD_RATE,
                default=opts.get(CONF_BAUD_RATE, DEFAULT_BAUD),
            ): NumberSelector(
                NumberSelectorConfig(min=1200, max=115200, step=1, mode=NumberSelectorMode.BOX)
            ),
            vol.Required(
                CONF_POLL_INTERVAL,
                default=opts.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(min=2, max=60, step=1, mode=NumberSelectorMode.SLIDER)
            ),
            vol.Optional(
                CONF_TEMP_SOURCE_ENTITY,
                description={"suggested_value": current_source},
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
        }
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))
