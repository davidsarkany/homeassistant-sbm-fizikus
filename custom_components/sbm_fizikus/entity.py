"""Base entity of the SBM Fizikus integration."""

from collections.abc import Callable, Iterable
from typing import override

from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import SbmThermostat
from .const import DOMAIN, MANUFACTURER, THERMOSTAT_MODEL, thermostat_device_identifier
from .coordinator import SbmFizikusConfigEntry, SbmFizikusCoordinator, SbmFizikusData


@callback
def async_add_thermostat_entities(
    entry: SbmFizikusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    platform: Platform,
    create_entities: Callable[[SbmFizikusData, SbmThermostat], Iterable[Entity]],
) -> None:
    """Add the entities of the thermostats, and of new ones when SBM reports them.

    A thermostat that is missing from an answer of SBM keeps its entities, so they
    are not added again when it comes back. Deleting its device forgets it, see
    async_remove_config_entry_device.
    """
    data = entry.runtime_data
    known = data.known_thermostats.setdefault(platform, set())

    @callback
    def add_new_thermostats() -> None:
        new = [
            thermostat
            for thermostat_id, thermostat in data.coordinator.data.thermostats.items()
            if thermostat_id not in known
        ]
        if not new:
            return
        known.update(thermostat.thermostat_id for thermostat in new)
        async_add_entities(
            entity for thermostat in new for entity in create_entities(data, thermostat)
        )

    add_new_thermostats()
    entry.async_on_unload(data.coordinator.async_add_listener(add_new_thermostats))


def _thermostat_device_name(data: SbmFizikusData, thermostat: SbmThermostat) -> str:
    """Return the name of the device of a thermostat.

    The name of SBM. Without one, the name the device already has, so a name that
    is missing while the entry is set up does not replace the last known one; a
    thermostat that never had a name is named after its id, like in the bridge.
    """
    if thermostat.name is not None:
        return thermostat.name
    coordinator = data.coordinator
    device = dr.async_get(coordinator.hass).async_get_device_by_identifier(
        (
            DOMAIN,
            thermostat_device_identifier(
                coordinator.apartment_id, thermostat.thermostat_id
            ),
        ),
        coordinator.config_entry.entry_id,
    )
    return (device.name if device is not None else None) or (
        f"Thermostat {thermostat.thermostat_id}"
    )


class SbmFizikusEntity(CoordinatorEntity[SbmFizikusCoordinator]):
    """An entity of a thermostat."""

    _attr_has_entity_name = True

    def __init__(
        self, data: SbmFizikusData, thermostat: SbmThermostat, key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(data.coordinator)
        apartment_id = data.coordinator.apartment_id
        self._thermostat_id = thermostat.thermostat_id
        self._thermostat = thermostat
        self._attr_unique_id = f"{apartment_id}_{thermostat.thermostat_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    thermostat_device_identifier(apartment_id, self._thermostat_id),
                )
            },
            manufacturer=MANUFACTURER,
            model=THERMOSTAT_MODEL,
            # The same name as the device of the bridge, which the entity IDs come from.
            name=_thermostat_device_name(data, thermostat),
            via_device_id=data.apartment_device_id,
        )

    @property
    def thermostat(self) -> SbmThermostat:
        """Return the latest state of the thermostat.

        When SBM no longer reports the thermostat, this is the last known state, and
        the entity is unavailable.
        """
        return self._thermostat

    @property
    @override
    def available(self) -> bool:
        """Return whether SBM reports the thermostat and reaches the system."""
        data = self.coordinator.data
        return (
            super().available
            and data.connected
            and self._thermostat_id in data.thermostats
        )

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Take the new state of the thermostat."""
        if (
            thermostat := self.coordinator.data.thermostats.get(self._thermostat_id)
        ) is not None:
            self._thermostat = thermostat
        super()._handle_coordinator_update()
