"""Sensor entities for the Autoterm USB."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .codec import HeaterStatus
from .const import DOMAIN, FAULT_CODES
from .coordinator import AutotermCoordinator


@dataclass(frozen=True)
class AutotermSensorDescription(SensorEntityDescription):
    value_fn: Callable[[HeaterStatus], Any] = lambda _: None
    available_fn: Callable[[Optional[HeaterStatus]], bool] = lambda st: st is not None


SENSOR_DESCRIPTIONS: tuple[AutotermSensorDescription, ...] = (
    # ── CONFIRMED fields ──────────────────────────────────────────────────────
    AutotermSensorDescription(
        key="heater_temp",
        name="Internal Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda st: float(st.heater_temp),
    ),
    AutotermSensorDescription(
        key="ext_temp",
        name="External Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda st: float(st.ext_temp) if st.ext_temp is not None else None,
        available_fn=lambda st: st is not None and st.ext_temp is not None,
    ),
    AutotermSensorDescription(
        key="voltage",
        name="Supply Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=lambda st: round(st.voltage, 1),
    ),
    AutotermSensorDescription(
        key="flame_temp",
        name="Heat Exchanger Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda st: round(st.flame_c, 1),
    ),
    AutotermSensorDescription(
        key="state",
        name="State",
        device_class=None,
        state_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda st: st.state_name,
    ),
    AutotermSensorDescription(
        key="error_code",
        name="Fault Code",
        device_class=None,
        state_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda st: st.error,
    ),
    AutotermSensorDescription(
        key="fault_description",
        name="Fault Description",
        device_class=None,
        state_class=None,
        native_unit_of_measurement=None,
        value_fn=lambda st: FAULT_CODES.get(st.error, (None, None))[0] if st.error else "OK",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AutotermCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AutotermSensor(coordinator, entry, desc)
        for desc in SENSOR_DESCRIPTIONS
    )


class AutotermSensor(CoordinatorEntity[AutotermCoordinator], SensorEntity):
    entity_description: AutotermSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: AutotermCoordinator,
        entry: ConfigEntry,
        description: AutotermSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._entry              = entry
        self.entity_description  = description
        self._attr_unique_id     = f"{entry.unique_id}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.unique_id or self._entry.entry_id)},
        )

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.entity_description.available_fn(self.coordinator.data)
        )

    @property
    def native_value(self) -> Any:
        st = self.coordinator.data
        if st is None:
            return None
        try:
            return self.entity_description.value_fn(st)
        except Exception:
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Extra attributes for fault_code sensor: add human-readable description."""
        if self.entity_description.key != "error_code":
            return {}
        st = self.coordinator.data
        if st is None or st.error == 0:
            return {}
        title, detail = FAULT_CODES.get(st.error, ("Unknown fault", ""))
        return {
            "fault_title":  title,
            "fault_detail": detail,
            "is_lockout":   st.is_lockout,
        }
