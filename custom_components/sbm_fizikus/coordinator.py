"""Coordinators of the SBM Fizikus integration."""

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SbmApartmentData, SbmAuthError, SbmBuilding, SbmClient, SbmError
from .const import CONF_APARTMENT_ID, DOMAIN, LOGGER, UPDATE_INTERVAL

type SbmFizikusConfigEntry = ConfigEntry[SbmFizikusData]


async def _async_fetch[T](request: Awaitable[T]) -> T:
    """Wait for a request to SBM, and raise its errors as errors of an update."""
    try:
        return await request
    except SbmAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except SbmError as err:
        raise UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="update_failed",
            translation_placeholders={"error": str(err)},
        ) from err


class SbmFizikusCoordinator(DataUpdateCoordinator[SbmApartmentData]):
    """Poll the state of the apartment and its thermostats."""

    config_entry: SbmFizikusConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SbmFizikusConfigEntry,
        client: SbmClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )
        self.client = client
        self.apartment_id: int = config_entry.data[CONF_APARTMENT_ID]
        self._connected = True

    @override
    async def _async_update_data(self) -> SbmApartmentData:
        """Fetch the state of the apartment."""
        data = await _async_fetch(
            self.client.async_get_apartment_data(self.apartment_id)
        )
        self._log_connection_change(data)
        return data

    def _log_connection_change(self, data: SbmApartmentData) -> None:
        """Log once when SBM loses the relay module or thermostats, once when back."""
        if data.connected == self._connected:
            return
        self._connected = data.connected
        if data.connected:
            LOGGER.info(
                "SBM reaches the relay module and the thermostats again, "
                "the entities are available"
            )
        else:
            LOGGER.warning(
                "SBM does not reach the relay module or the thermostats, "
                "the entities are unavailable until it does again"
            )


class SbmFizikusOutdoorCoordinator(DataUpdateCoordinator[SbmBuilding]):
    """Poll the outdoor weather of the building of the apartment.

    It has no first refresh at setup, and like every coordinator it polls only while
    entities listen to it. The outdoor sensors are disabled by default, and a disabled
    entity is not added, so SBM is asked for the outdoor weather only while one of
    them is enabled. Enabling one reloads the config entry, which adds it.
    """

    config_entry: SbmFizikusConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: SbmFizikusConfigEntry,
        client: SbmClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} outdoor",
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )
        self.client = client
        self.apartment_id: int = config_entry.data[CONF_APARTMENT_ID]
        # Looked up at the first poll; the config entry does not store it.
        self._building_id: int | None = None

    @override
    async def _async_update_data(self) -> SbmBuilding:
        """Fetch the outdoor weather of the building of the apartment."""
        buildings = {
            building.building_id: building
            for building in await _async_fetch(self.client.async_get_buildings())
        }
        if self._building_id not in buildings:
            self._building_id = await self._async_find_building_id(buildings)
        return buildings[self._building_id]

    async def _async_find_building_id(
        self, buildings: Mapping[int, SbmBuilding]
    ) -> int:
        """Return the building that the apartment is in."""
        for building_id in buildings:
            apartments = await _async_fetch(
                self.client.async_get_apartments(building_id)
            )
            if any(
                apartment.apartment_id == self.apartment_id for apartment in apartments
            ):
                LOGGER.debug(
                    "Apartment %s is in building %s", self.apartment_id, building_id
                )
                return building_id
        raise UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="building_not_found",
            translation_placeholders={"apartment_id": str(self.apartment_id)},
        )


@dataclass(slots=True)
class SbmFizikusData:
    """Runtime data of a config entry."""

    coordinator: SbmFizikusCoordinator
    outdoor_coordinator: SbmFizikusOutdoorCoordinator
    # The device registry id of the apartment, the hub of the thermostats.
    apartment_device_id: str
    # The thermostats that each platform has added entities for, by platform.
    known_thermostats: dict[str, set[int]] = field(default_factory=dict)
