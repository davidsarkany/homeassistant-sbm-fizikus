"""Switches of the SBM Fizikus system and its thermostats."""

from typing import Any, override

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import SbmThermostat
from .const import DOMAIN, apartment_device_identifier
from .coordinator import SbmFizikusConfigEntry, SbmFizikusCoordinator, SbmFizikusData
from .entity import SbmFizikusEntity, async_add_thermostat_entities

# Switching goes to SBM one call at a time.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SbmFizikusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switch of the system, and the child lock of each thermostat."""
    async_add_entities([SbmFizikusSystemSwitch(entry.runtime_data)])
    async_add_thermostat_entities(
        entry,
        async_add_entities,
        Platform.SWITCH,
        lambda data, thermostat: [SbmFizikusChildLockSwitch(data, thermostat)],
    )


class SbmFizikusSystemSwitch(CoordinatorEntity[SbmFizikusCoordinator], SwitchEntity):
    """Switches the SBM Fizikus system of the apartment on and off, like the app.

    The climate entities show off while the system is off, but they do not switch
    it: off for one room, for example from an automation for an open window, would
    switch off every room.
    """

    _attr_has_entity_name = True
    # A fixed English name like the other entities, so the entity ID does not depend
    # on the language of the server.
    _attr_name = "System"

    def __init__(self, data: SbmFizikusData) -> None:
        """Initialize the switch."""
        super().__init__(data.coordinator)
        apartment_id = data.coordinator.apartment_id
        self._attr_unique_id = f"{apartment_id}_system"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, apartment_device_identifier(apartment_id))}
        )

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the system is switched on."""
        return not self.coordinator.data.system_off

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the system on."""
        await self._async_switch(system_off=False)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the system off."""
        await self._async_switch(system_off=True)

    async def _async_switch(self, *, system_off: bool) -> None:
        """Switch the system, then fetch its new state."""
        coordinator = self.coordinator
        await coordinator.async_run_action(
            coordinator.client.async_set_system_off(
                coordinator.apartment_id, system_off=system_off
            ),
            "switch_system_failed",
        )


class SbmFizikusChildLockSwitch(SbmFizikusEntity, SwitchEntity):
    """Locks the buttons of a thermostat, like the room card of the app."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    # A fixed English name like the other entities, so the entity ID does not depend
    # on the language of the server.
    _attr_name = "Child lock"

    def __init__(self, data: SbmFizikusData, thermostat: SbmThermostat) -> None:
        """Initialize the switch."""
        super().__init__(data, thermostat, "child_lock")

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the buttons of the thermostat are locked."""
        return self.thermostat.child_lock

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Lock the buttons of the thermostat."""
        await self._async_lock(locked=True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unlock the buttons of the thermostat."""
        await self._async_lock(locked=False)

    async def _async_lock(self, *, locked: bool) -> None:
        """Lock or unlock, then fetch the new state.

        SBM reports the change right away as pending, in childLockCommand, until the
        thermostat confirms it.
        """
        coordinator = self.coordinator
        await coordinator.async_run_action(
            coordinator.client.async_set_child_lock(
                self.thermostat.thermostat_id, locked=locked
            ),
            "switch_child_lock_failed",
        )
