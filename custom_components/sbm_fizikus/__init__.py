"""The SBM Fizikus integration."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.hass_dict import HassKey

from .api import SbmClient
from .const import (
    APARTMENT_DEVICE_NAME,
    APARTMENT_MODEL,
    CONF_PWHASH,
    DOMAIN,
    LOGGER,
    MANUFACTURER,
    PLATFORMS,
    apartment_device_identifier,
    thermostat_device_identifier,
)
from .coordinator import (
    SbmFizikusConfigEntry,
    SbmFizikusCoordinator,
    SbmFizikusData,
    SbmFizikusOutdoorCoordinator,
)

_UNREACHABLE_AT_SETUP: HassKey[set[str]] = HassKey(f"{DOMAIN}_unreachable_at_setup")


async def async_setup_entry(hass: HomeAssistant, entry: SbmFizikusConfigEntry) -> None:
    """Set up SBM Fizikus from a config entry."""
    client = SbmClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PWHASH],
    )
    coordinator = SbmFizikusCoordinator(hass, entry, client)
    # Home Assistant logs a failed setup only as an info message, which it hides by
    # default. The setup is retried with a new coordinator, so the entries that have
    # been logged are kept here, to log an outage only once.
    unreachable = hass.data.setdefault(_UNREACHABLE_AT_SETUP, set())
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady as err:
        if entry.entry_id not in unreachable:
            unreachable.add(entry.entry_id)
            LOGGER.warning("SBM cannot be reached, retrying in the background: %s", err)
        raise
    if entry.entry_id in unreachable:
        unreachable.remove(entry.entry_id)
        LOGGER.warning("SBM can be reached again")

    # The thermostats link to the apartment with its device id, so it must exist
    # before their entities are added.
    device_registry = dr.async_get(hass)
    apartment_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, apartment_device_identifier(coordinator.apartment_id))},
        manufacturer=MANUFACTURER,
        model=APARTMENT_MODEL,
        name=APARTMENT_DEVICE_NAME,
        sw_version=coordinator.data.firmware_version,
    )

    shown_firmware_version = coordinator.data.firmware_version

    @callback
    def async_update_firmware_version() -> None:
        """Show a new firmware of the device at once, without a reload.

        A poll without a firmware version keeps the known one, so a value missing
        from one answer does not clear it.
        """
        nonlocal shown_firmware_version
        firmware_version = coordinator.data.firmware_version
        if firmware_version is None or firmware_version == shown_firmware_version:
            return
        shown_firmware_version = firmware_version
        device_registry.async_update_device(
            apartment_device.id, sw_version=firmware_version
        )

    entry.async_on_unload(coordinator.async_add_listener(async_update_firmware_version))

    @callback
    def async_update_thermostat_names() -> None:
        """Update reported room names, preserving user overrides and entity IDs."""
        for thermostat in coordinator.data.thermostats.values():
            if thermostat.name is None:
                continue
            device = device_registry.async_get_device_by_identifier(
                (
                    DOMAIN,
                    thermostat_device_identifier(
                        coordinator.apartment_id, thermostat.thermostat_id
                    ),
                ),
                entry.entry_id,
            )
            if device is not None and device.name != thermostat.name:
                device_registry.async_update_device(device.id, name=thermostat.name)

    entry.async_on_unload(coordinator.async_add_listener(async_update_thermostat_names))
    entry.runtime_data = SbmFizikusData(
        coordinator,
        # Polls only for enabled outdoor sensors, so it has no first refresh here.
        SbmFizikusOutdoorCoordinator(hass, entry, client, coordinator),
        apartment_device.id,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(hass: HomeAssistant, entry: SbmFizikusConfigEntry) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: SbmFizikusConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a thermostat that SBM no longer reports.

    Without a loaded entry there is no answer of SBM to check the device against,
    so nothing can be removed then.
    """
    if entry.state is not ConfigEntryState.LOADED:
        return False
    data = entry.runtime_data
    apartment_id = data.coordinator.apartment_id
    identifiers = {
        identifier
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN
    }
    present = {apartment_device_identifier(apartment_id)} | {
        thermostat_device_identifier(apartment_id, thermostat_id)
        for thermostat_id in data.coordinator.data.thermostats
    }
    if identifiers & present:
        return False
    # Forget the thermostat, so it gets entities again if SBM reports it later.
    for known in data.known_thermostats.values():
        known -= {
            thermostat_id
            for thermostat_id in known
            if thermostat_device_identifier(apartment_id, thermostat_id) in identifiers
        }
    return True
