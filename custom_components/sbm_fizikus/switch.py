"""Switch of the SBM Fizikus system."""

from typing import Any, override

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import SbmAuthError, SbmError
from .const import DOMAIN, apartment_device_identifier
from .coordinator import SbmFizikusConfigEntry, SbmFizikusCoordinator, SbmFizikusData

# Switching goes to SBM one call at a time.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SbmFizikusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switch of the system."""
    async_add_entities([SbmFizikusSystemSwitch(entry.runtime_data)])


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
        try:
            await coordinator.client.async_set_system_off(
                coordinator.apartment_id, system_off=system_off
            )
        except SbmAuthError as err:
            coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except SbmError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="switch_system_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await coordinator.async_request_refresh()
