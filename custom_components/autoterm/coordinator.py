"""DataUpdateCoordinator for the Autoterm USB."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import AutotermClient, AutotermClientError
from .codec import HeaterStatus
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class AutotermCoordinator(DataUpdateCoordinator[HeaterStatus | None]):
    """
    Polls the heater on a fixed interval.

    data is HeaterStatus | None.
    None means "no response received" — entities should mark themselves
    unavailable but must NOT trigger an automatic stop or restart.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: AutotermClient,
        poll_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )
        self.client = client

    async def _async_update_data(self) -> HeaterStatus | None:
        if not await self.client.ensure_connected():
            raise UpdateFailed("Cannot connect to heater serial port")
        try:
            status = await self.client.poll_status()
        except AutotermClientError as exc:
            raise UpdateFailed(str(exc)) from exc
        if status is None:
            raise UpdateFailed("No STATUS response from heater")
        return status
