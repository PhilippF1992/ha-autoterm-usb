"""DataUpdateCoordinator for the Autoterm USB."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import AutotermClient, AutotermClientError
from .codec import HeaterStatus, SettingsPayload
from .const import (
    CLIMATE_TEMP_MAX,
    CLIMATE_TEMP_MIN,
    CONF_STALENESS_THRESHOLD,
    CONF_TEMP_SOURCE_ENTITY,
    DEFAULT_FAN_LEVEL,
    DEFAULT_POWER_LEVEL,
    DEFAULT_STALENESS_THRESHOLD,
    DOMAIN,
    MODE_TO_REG_SOURCE,
    PANEL_TEMP_MAX,
    PANEL_TEMP_MIN,
    REG_SOURCE_PANEL,
    REG_SOURCE_POWER,
    REG_SOURCE_TO_MODE,
    SETTINGS_READ_INTERVAL,
    START_MODE_BY_HEATER_TEMP,
)

_LOGGER = logging.getLogger(__name__)


class AutotermCoordinator(DataUpdateCoordinator[HeaterStatus | None]):
    """
    Polls the heater on a fixed interval.

    data is HeaterStatus | None.
    None means "no response received" — entities mark themselves unavailable.

    Mutable attributes (reg_source, target_temp, power_level, fan_level) are set
    by the select/number/climate entities when the user changes them. The coordinator
    uses them to:
      - build correct START/SETTINGS frames
      - inject panel temperature in the 1 Hz loop when reg_source == "panel"
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: AutotermClient,
        poll_interval: int,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )
        self.client = client
        self._entry = entry

        # ── Mutable heater state (updated by entities + settings reads) ───────
        self.reg_source: str = REG_SOURCE_POWER          # default until settings read
        self.target_temp: float = 20.0
        self.power_level: int = DEFAULT_POWER_LEVEL
        self.fan_level: int = DEFAULT_FAN_LEVEL

        # ── Internal bookkeeping ──────────────────────────────────────────────
        self._settings: SettingsPayload | None = None
        self._last_settings_poll: float = 0.0          # monotonic
        self._panel_fallback_active: bool = False
        self._consecutive_poll_failures: int = 0

        # ── Fuel-prime state ──────────────────────────────────────────────────
        self._priming: bool = False
        self._prime_task: asyncio.Task | None = None
        self.prime_status: str = "idle"

    # ── Settings sync ─────────────────────────────────────────────────────────

    async def async_refresh_settings(self) -> None:
        """Read current settings from the heater and sync local state."""
        settings = await self.client.get_settings()
        if settings is None:
            _LOGGER.warning("Could not read heater settings (0x02 no response)")
            return
        self._apply_settings(settings)
        self._last_settings_poll = time.monotonic()

    def _apply_settings(self, settings: SettingsPayload) -> None:
        self._settings = settings
        self.reg_source = MODE_TO_REG_SOURCE.get(settings.mode, REG_SOURCE_POWER)
        # In power mode the heater doesn't regulate by temperature, so don't let
        # the settings read overwrite a user-configured target_temp.
        if self.reg_source != REG_SOURCE_POWER:
            self.target_temp = max(CLIMATE_TEMP_MIN, min(CLIMATE_TEMP_MAX, float(settings.setpoint)))
        self.power_level = max(1, min(9, settings.power_level))

    # ── DataUpdateCoordinator override ───────────────────────────────────────

    # How many consecutive missed STATUS responses before declaring unavailable.
    # A command (START/STOP) holds the lock for ~1 s and the heater may not reply
    # to the immediate post-command refresh poll — tolerate a few misses so that
    # entities don't flicker unavailable on every command.
    _MAX_POLL_FAILURES = 3

    async def _async_update_data(self) -> HeaterStatus | None:
        if not await self.client.ensure_connected():
            raise UpdateFailed("Cannot connect to heater serial port")

        # Inject panel temperature before polling status (panel is the master)
        if self.reg_source == REG_SOURCE_PANEL:
            await self._maybe_inject_panel_temp()

        try:
            status = await self.client.poll_status()
        except AutotermClientError as exc:
            raise UpdateFailed(str(exc)) from exc

        if status is None:
            self._consecutive_poll_failures += 1
            if self._consecutive_poll_failures >= self._MAX_POLL_FAILURES:
                raise UpdateFailed(
                    f"No STATUS response from heater "
                    f"({self._consecutive_poll_failures} consecutive misses)"
                )
            _LOGGER.debug(
                "No STATUS response (miss %d/%d) — keeping last state",
                self._consecutive_poll_failures,
                self._MAX_POLL_FAILURES,
            )
            return self.data  # return last known data; entities stay available

        self._consecutive_poll_failures = 0

        # Periodic settings re-read to stay in sync with heater-side changes
        if time.monotonic() - self._last_settings_poll > SETTINGS_READ_INTERVAL:
            with contextlib.suppress(Exception):
                await self.async_refresh_settings()

        return status

    # ── Panel temperature injection ───────────────────────────────────────────

    async def _maybe_inject_panel_temp(self) -> None:
        """
        Feed the current HA sensor reading to the heater as the panel temperature.

        If the source sensor is unavailable or stale, fall back to internal sensor
        (mode=0x01) and log a warning. Restores panel mode when sensor recovers.
        """
        source_entity: str | None = self._entry.options.get(CONF_TEMP_SOURCE_ENTITY)
        if not source_entity:
            return

        state = self.hass.states.get(source_entity)
        if state is None or state.state in ("unknown", "unavailable"):
            await self._handle_panel_sensor_unavailable(source_entity, "state is unavailable")
            return

        threshold: int = int(
            self._entry.options.get(CONF_STALENESS_THRESHOLD, DEFAULT_STALENESS_THRESHOLD)
        )
        age = (dt_util.utcnow() - state.last_changed).total_seconds()
        if age > threshold:
            await self._handle_panel_sensor_unavailable(
                source_entity, f"stale ({age:.0f} s > threshold {threshold} s)"
            )
            return

        try:
            raw = float(state.state)
        except ValueError:
            await self._handle_panel_sensor_unavailable(
                source_entity, f"non-numeric state '{state.state}'"
            )
            return

        temp = int(round(max(PANEL_TEMP_MIN, min(PANEL_TEMP_MAX, raw))))

        if self._panel_fallback_active:
            _LOGGER.info(
                "Panel temp source %s recovered (%.1f°C), resuming panel mode",
                source_entity,
                raw,
            )
            self._panel_fallback_active = False
            # Restore panel mode in heater settings
            await self.client.send_write_settings(
                mode=REG_SOURCE_TO_MODE[REG_SOURCE_PANEL],
                setpoint=int(round(self.target_temp)),
                ventilation=0,
                power_level=self.power_level,
            )

        await self.client.send_set_temp(temp)

    async def _handle_panel_sensor_unavailable(self, entity_id: str, reason: str) -> None:
        if not self._panel_fallback_active:
            _LOGGER.warning(
                "Panel temp source %s %s — falling back to internal sensor",
                entity_id,
                reason,
            )
            self._panel_fallback_active = True
            await self.client.send_write_settings(
                mode=START_MODE_BY_HEATER_TEMP,
                setpoint=int(round(self.target_temp)),
                ventilation=0,
                power_level=self.power_level,
            )
