"""Sensors of the SBM Fizikus thermostats."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    Platform,
    UnitOfTemperature,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    OPERATION_MODE_COOLING,
    OPERATION_MODE_HEATING,
    SbmApartmentData,
    SbmBuilding,
    SbmThermostat,
)
from .const import DOMAIN, apartment_device_identifier
from .coordinator import (
    SbmFizikusConfigEntry,
    SbmFizikusCoordinator,
    SbmFizikusData,
    SbmFizikusOutdoorCoordinator,
)
from .entity import SbmFizikusEntity, async_add_thermostat_entities

# The coordinator does the polling.
PARALLEL_UPDATES = 0

_SYSTEM_MODES = {OPERATION_MODE_HEATING: "heating", OPERATION_MODE_COOLING: "cooling"}


def _system_mode(data: SbmApartmentData, thermostat: SbmThermostat) -> str | None:
    """Return off, the mode of the building while the thermostat is active, or idle."""
    if data.switched_off:
        return "off"
    if not thermostat.active:
        return "idle"
    return _SYSTEM_MODES.get(data.operation_mode)


@dataclass(frozen=True, kw_only=True)
class SbmFizikusSensorEntityDescription(SensorEntityDescription):
    """Describes a sensor of a thermostat."""

    value_fn: Callable[[SbmApartmentData, SbmThermostat], StateType]


@dataclass(frozen=True, kw_only=True)
class SbmFizikusApartmentSensorEntityDescription(SensorEntityDescription):
    """Describes a sensor of the apartment."""

    value_fn: Callable[[SbmApartmentData], StateType]


@dataclass(frozen=True, kw_only=True)
class SbmFizikusOutdoorSensorEntityDescription(SensorEntityDescription):
    """Describes an outdoor sensor of the building."""

    value_fn: Callable[[SbmBuilding], StateType]


# Home Assistant builds the entity ID of a new entity from its name, and a translated
# name would give a Hungarian entity ID on a Hungarian server. Every sensor therefore
# has a fixed English name (the ones the MQTT bridge also has keep its names), and
# the translations must not get a "name", so the entity IDs never depend on the
# language and switching from the bridge keeps them.
SENSORS: tuple[SbmFizikusSensorEntityDescription, ...] = (
    SbmFizikusSensorEntityDescription(
        key="humidity",
        name="Humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda _, thermostat: thermostat.humidity,
    ),
    SbmFizikusSensorEntityDescription(
        key="dew_point",
        name="Dew point",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
        value_fn=lambda _, thermostat: thermostat.dew_point,
    ),
    SbmFizikusSensorEntityDescription(
        key="temperature",
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
        value_fn=lambda _, thermostat: thermostat.temperature,
    ),
    SbmFizikusSensorEntityDescription(
        key="target_temperature",
        name="Target temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
        value_fn=lambda _, thermostat: thermostat.target_temperature,
    ),
    SbmFizikusSensorEntityDescription(
        key="system_mode",
        name="System mode",
        # Only for the translations of the states.
        translation_key="system_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["heating", "cooling", "idle", "off"],
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_system_mode,
    ),
)

# The values of the "Statisztika" page of the app, on the device of the apartment.
APARTMENT_SENSORS: tuple[SbmFizikusApartmentSensorEntityDescription, ...] = (
    SbmFizikusApartmentSensorEntityDescription(
        key="forward_water_temperature",
        name="Forward water temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.forward_water_temperature,
    ),
    SbmFizikusApartmentSensorEntityDescription(
        key="return_water_temperature",
        name="Return water temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.return_water_temperature,
    ),
    SbmFizikusApartmentSensorEntityDescription(
        key="flow_rate",
        name="Flow rate",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_HOUR,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.flow_rate,
    ),
)

# The bridge has these too, on the device of the apartment, so they keep its names.
OUTDOOR_SENSORS: tuple[SbmFizikusOutdoorSensorEntityDescription, ...] = (
    SbmFizikusOutdoorSensorEntityDescription(
        key="outdoor_temperature",
        name="Outdoor temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
        value_fn=lambda building: building.outdoor_temperature,
    ),
    SbmFizikusOutdoorSensorEntityDescription(
        key="outdoor_humidity",
        name="Outdoor humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_registry_enabled_default=False,
        value_fn=lambda building: building.outdoor_humidity,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SbmFizikusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of each thermostat, also of new ones, and the outdoor ones."""
    async_add_thermostat_entities(
        entry,
        async_add_entities,
        Platform.SENSOR,
        lambda data, thermostat: (
            SbmFizikusSensor(data, thermostat, description) for description in SENSORS
        ),
    )
    data = entry.runtime_data
    async_add_entities(
        SbmFizikusApartmentSensor(data, description)
        for description in APARTMENT_SENSORS
    )
    async_add_entities(
        SbmFizikusOutdoorSensor(data, description) for description in OUTDOOR_SENSORS
    )


class SbmFizikusSensor(SbmFizikusEntity, SensorEntity):
    """A sensor of a thermostat."""

    entity_description: SbmFizikusSensorEntityDescription

    def __init__(
        self,
        data: SbmFizikusData,
        thermostat: SbmThermostat,
        description: SbmFizikusSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(data, thermostat, description.key)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> StateType:
        """Return the value of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data, self.thermostat)


class SbmFizikusApartmentSensor(CoordinatorEntity[SbmFizikusCoordinator], SensorEntity):
    """A sensor of the apartment, on its device.

    The values come from the device of the apartment, not from the thermostats, so
    they do not depend on whether SBM reaches the thermostats.
    """

    _attr_has_entity_name = True
    entity_description: SbmFizikusApartmentSensorEntityDescription

    def __init__(
        self,
        data: SbmFizikusData,
        description: SbmFizikusApartmentSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(data.coordinator)
        self.entity_description = description
        apartment_id = data.coordinator.apartment_id
        self._attr_unique_id = f"{apartment_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, apartment_device_identifier(apartment_id))}
        )

    @property
    @override
    def native_value(self) -> StateType:
        """Return the value of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)


class SbmFizikusOutdoorSensor(
    CoordinatorEntity[SbmFizikusOutdoorCoordinator], SensorEntity
):
    """An outdoor sensor of the building, on the device of the apartment."""

    _attr_has_entity_name = True
    entity_description: SbmFizikusOutdoorSensorEntityDescription

    def __init__(
        self,
        data: SbmFizikusData,
        description: SbmFizikusOutdoorSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(data.outdoor_coordinator)
        self.entity_description = description
        apartment_id = data.coordinator.apartment_id
        self._attr_unique_id = f"{apartment_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, apartment_device_identifier(apartment_id))}
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Fetch the outdoor weather when the first outdoor sensor is added.

        The coordinator starts polling with its first listener, but only after the
        update interval. The fetch runs in the background, so a slow SBM does not
        hold up the setup of the entry; a second sensor added meanwhile finds the
        fetch running and does not start another one. When this fetch fails, the
        next poll tries again.
        """
        await super().async_added_to_hass()
        coordinator = self.coordinator
        if coordinator.data is None and coordinator.last_update_success:
            coordinator.config_entry.async_create_background_task(
                self.hass,
                coordinator.async_request_refresh(),
                f"{DOMAIN} first outdoor refresh",
            )

    @property
    @override
    def available(self) -> bool:
        """Return whether the outdoor weather was fetched."""
        return super().available and self.coordinator.data is not None

    @property
    @override
    def native_value(self) -> StateType:
        """Return the value of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)
