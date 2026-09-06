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
    PANEL_TEMP_MAX,
    PANEL_TEMP_MIN,
    PRESET_BY_POWER,
    PRESET_BY_TEMP,
    SETTINGS_READ_INTERVAL,
    START_MODE_BY_POWER,
    TEMP_SOURCE_EXTERNAL,
    TEMP_SOURCE_HA_SENSOR,
    TEMP_SOURCE_INTERNAL,
)

_LOGGER = logging.getLogger(__name__)


class AutotermCoordinator(DataUpdateCoordinator[HeaterStatus | None]):
    """
    Polls the heater on a fixed interval.

    data is HeaterStatus | None.
    None means "no response received" — entities mark themselves unavailable.

    Mutable attributes are set by climate/select/number entities when the user
    changes them. The coordinator uses them to:
      - build correct START/SETTINGS frames
      - inject the selected temperature source value as panel temp at every poll
        when heating_preset == PRESET_BY_TEMP
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

        # ── Mutable heater state (updated by entities + first settings read) ─
        self.heating_preset: str = PRESET_BY_POWER  # initialised from first settings read
        self.temp_source: str = TEMP_SOURCE_HA_SENSOR  # HA-side only; never written to heater
        self.target_temp: float = 20.0
        self.power_level: int = DEFAULT_POWER_LEVEL
        self.fan_level: int = DEFAULT_FAN_LEVEL

        # ── Internal bookkeeping ──────────────────────────────────────────────
        self._settings: SettingsPayload | None = None
        self._last_settings_poll: float = 0.0  # monotonic
        # Prevents periodic settings polls from overwriting a user-selected pending preset
        self._initial_settings_applied: bool = False
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
        if not self._initial_settings_applied:
            # First read only — initialise heating_preset from heater's stored mode.
            # After this, heating_preset is user-controlled; periodic polls must not
            # overwrite a pending user selection that hasn't been confirmed by a START yet.
            self.heating_preset = (
                PRESET_BY_POWER if settings.mode == START_MODE_BY_POWER else PRESET_BY_TEMP
            )
            self._initial_settings_applied = True
        # target_temp and power_level are always safe to sync from heater
        if self.heating_preset != PRESET_BY_POWER:
            self.target_temp = max(
                CLIMATE_TEMP_MIN, min(CLIMATE_TEMP_MAX, float(settings.setpoint))
            )
        self.power_level = max(1, min(9, settings.power_level))

    # ── DataUpdateCoordinator override ───────────────────────────────────────

    _MAX_POLL_FAILURES = 3

    async def _async_update_data(self) -> HeaterStatus | None:
        if not await self.client.ensure_connected():
            raise UpdateFailed("Cannot connect to heater serial port")

        if self.heating_preset == PRESET_BY_TEMP:
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
            return self.data

        self._consecutive_poll_failures = 0

        if time.monotonic() - self._last_settings_poll > SETTINGS_READ_INTERVAL:
            with contextlib.suppress(Exception):
                await self.async_refresh_settings()

        return status

    # ── Panel temperature injection ───────────────────────────────────────────

    async def _maybe_inject_panel_temp(self) -> None:
        """
        Feed the selected temperature source to the heater as the panel temperature.

        Only sends 0x11 SET_TEMP — never 0x02 WRITE_SETTINGS (that's for user actions only).
        Falls back to the heater's own internal sensor if the configured source is unavailable.
        """
        temp = self._get_source_temp()
        if temp is None:
            st = self.data
            if st is not None:
                temp = st.heater_temp
                _LOGGER.debug(
                    "Temp source '%s' unavailable — feeding heater internal %d°C",
                    self.temp_source,
                    temp,
                )
        if temp is not None:
            await self.client.send_set_temp(max(PANEL_TEMP_MIN, min(PANEL_TEMP_MAX, temp)))

    def _get_source_temp(self) -> int | None:
        """Return the current value of the selected temperature source, or None if unavailable."""
        st = self.data
        if self.temp_source == TEMP_SOURCE_INTERNAL:
            return st.heater_temp if st else None

        if self.temp_source == TEMP_SOURCE_EXTERNAL:
            # Note: codec decodes ext_temp as uint8. Sub-zero readings will be
            # misrepresented until signed int8 decoding is added to codec.py.
            return st.ext_temp if (st and st.ext_temp is not None) else None

        if self.temp_source == TEMP_SOURCE_HA_SENSOR:
            source_entity: str | None = self._entry.options.get(CONF_TEMP_SOURCE_ENTITY)
            if not source_entity:
                return None
            state = self.hass.states.get(source_entity)
            if state is None or state.state in ("unknown", "unavailable"):
                return None
            threshold: int = int(
                self._entry.options.get(CONF_STALENESS_THRESHOLD, DEFAULT_STALENESS_THRESHOLD)
            )
            if (dt_util.utcnow() - state.last_changed).total_seconds() > threshold:
                return None
            try:
                return int(round(float(state.state)))
            except ValueError:
                return None

        return None

    def get_displayed_temp(self) -> float | None:
        """
        Return the temperature shown on the climate card and fed to the heater.

        When the configured source is unavailable, returns the heater's internal sensor
        reading as fallback — never returns None while the heater is online.
        """
        raw = self._get_source_temp()
        if raw is not None:
            return float(raw)
        st = self.data
        return float(st.heater_temp) if st else None
