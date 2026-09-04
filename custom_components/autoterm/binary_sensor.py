"""Binary sensor entities for the Autoterm USB."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .codec import HeaterStatus
from .const import DOMAIN
from .coordinator import AutotermCoordinator


@dataclass(frozen=True)
class AutotermBinarySensorDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[HeaterStatus], bool] = lambda _: False
    available_fn: Callable[[HeaterStatus | None], bool] = lambda st: st is not None


BINARY_SENSOR_DESCRIPTIONS: tuple[AutotermBinarySensorDescription, ...] = (
    AutotermBinarySensorDescription(
        key="running",
        name="Running",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda st: st.is_running,
    ),
    AutotermBinarySensorDescription(
        key="fault",
        name="Fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda st: st.is_fault,
    ),
    AutotermBinarySensorDescription(
        key="ext_sensor_present",
        name="External Temperature Sensor",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        is_on_fn=lambda st: st.ext_temp_present,
    ),
    AutotermBinarySensorDescription(
        key="lockout",
        name="Lockout",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda st: st.is_lockout,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AutotermCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AutotermBinarySensor(coordinator, entry, desc) for desc in BINARY_SENSOR_DESCRIPTIONS
    )


class AutotermBinarySensor(CoordinatorEntity[AutotermCoordinator], BinarySensorEntity):
    entity_description: AutotermBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AutotermCoordinator,
        entry: ConfigEntry,
        description: AutotermBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.unique_id or self._entry.entry_id)},
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.entity_description.available_fn(
            self.coordinator.data
        )

    @property
    def is_on(self) -> bool | None:
        st = self.coordinator.data
        if st is None:
            return None
        return self.entity_description.is_on_fn(st)
