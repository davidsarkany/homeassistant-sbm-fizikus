"""Coordinators of the SBM Fizikus integration."""

from collections.abc import Awaitable, Collection
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SbmApartmentData, SbmAuthError, SbmBuilding, SbmClient, SbmError
from .const import (
    BUILDING_UPDATE_INTERVAL,
    CONF_APARTMENT_ID,
    DOMAIN,
    LOGGER,
    UPDATE_INTERVAL,
)

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
    """Poll the state of the apartment and its thermostats.

    Every BUILDING_UPDATE_INTERVAL it also asks whether the system is switched off
    for the whole building. That is extra information: when it cannot be fetched,
    the poll keeps the last known value instead of failing.
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
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )
        self.client = client
        self.apartment_id: int = config_entry.data[CONF_APARTMENT_ID]
        self._connected = True
        # The building of the apartment, looked up when needed; the config entry
        # does not store it. The outdoor coordinator uses it too.
        self._building_id: int | None = None
        self._building_off = False
        self._building_checked: datetime | None = None

    async def async_find_building_id(self, building_ids: Collection[int]) -> int:
        """Return the building, of the given ones, that the apartment is in.

        The building found is kept, and looked up again only when it is not among
        the given ones.
        """
        if (known := self._building_id) is not None and known in building_ids:
            return known
        for building_id in building_ids:
            apartments = await _async_fetch(
                self.client.async_get_apartments(building_id)
            )
            if any(
                apartment.apartment_id == self.apartment_id for apartment in apartments
            ):
                LOGGER.debug(
                    "Apartment %s is in building %s", self.apartment_id, building_id
                )
                self._building_id = building_id
                return building_id
        raise UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="building_not_found",
            translation_placeholders={"apartment_id": str(self.apartment_id)},
        )

    async def _async_get_building_off(self) -> bool:
        """Return whether the system is switched off for the whole building.

        Asked every BUILDING_UPDATE_INTERVAL; in between, and when it cannot be
        fetched, the last known value. A refused login still starts the
        reauthentication.
        """
        now = dt_util.utcnow()
        if (
            self._building_checked is not None
            and now - self._building_checked < BUILDING_UPDATE_INTERVAL
        ):
            return self._building_off
        self._building_checked = now
        try:
            building_id = self._building_id
            if building_id is None:
                buildings = await _async_fetch(self.client.async_get_buildings())
                building_id = await self.async_find_building_id(
                    [building.building_id for building in buildings]
                )
            building_off = await _async_fetch(
                self.client.async_get_building_off(building_id)
            )
        except UpdateFailed as err:
            LOGGER.debug(
                "Whether the building is switched off could not be fetched: %s", err
            )
            # The building may be gone; the next check looks it up again.
            self._building_id = None
            return self._building_off
        if building_off != self._building_off:
            LOGGER.info(
                "SBM reports the system as switched %s for the whole building",
                "off" if building_off else "on",
            )
        self._building_off = building_off
        return building_off

    async def async_run_action(
        self, action: Awaitable[None], translation_key: str
    ) -> None:
        """Send an action of an entity to SBM, then fetch the new state.

        A refused login starts the reauthentication. Every failure is raised as a
        translated HomeAssistantError: `auth_failed`, or the given key with the
        cause in its `error` placeholder. SBM reports the new value right away as
        pending, so the state is fetched at once, with no optimistic value.
        """
        try:
            await action
        except SbmAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except SbmError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=translation_key,
                translation_placeholders={"error": str(err)},
            ) from err
        await self.async_request_refresh()

    @override
    async def _async_update_data(self) -> SbmApartmentData:
        """Fetch the state of the apartment."""
        data = await _async_fetch(
            self.client.async_get_apartment_data(self.apartment_id)
        )
        data = replace(data, building_off=await self._async_get_building_off())
        LOGGER.debug("Parsed state of apartment %s: %s", self.apartment_id, data)
        if not self.last_update_success:
            # A warning, as the base class logs the recovery only as an info message,
            # which Home Assistant hides by default, unlike the failure.
            LOGGER.warning("SBM can be reached again")
        self._log_connection_change(data)
        return data

    def _log_connection_change(self, data: SbmApartmentData) -> None:
        """Log once when SBM loses the thermostats, and once when they are back."""
        if data.connected == self._connected:
            return
        self._connected = data.connected
        if data.connected:
            LOGGER.warning(
                "SBM reaches the thermostats again, the entities are available"
            )
        else:
            LOGGER.warning(
                "SBM does not reach the thermostats, "
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
        apartment_coordinator: SbmFizikusCoordinator,
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
        # It knows the building of the apartment.
        self._apartment_coordinator = apartment_coordinator

    @override
    async def _async_update_data(self) -> SbmBuilding:
        """Fetch the outdoor weather of the building of the apartment."""
        buildings = {
            building.building_id: building
            for building in await _async_fetch(self.client.async_get_buildings())
        }
        building = buildings[
            await self._apartment_coordinator.async_find_building_id(buildings)
        ]
        LOGGER.debug("Parsed outdoor weather: %s", building)
        if not self.last_update_success:
            LOGGER.warning("The outdoor weather can be fetched from SBM again")
        return building


@dataclass(slots=True)
class SbmFizikusData:
    """Runtime data of a config entry."""

    coordinator: SbmFizikusCoordinator
    outdoor_coordinator: SbmFizikusOutdoorCoordinator
    # The device registry id of the apartment, the hub of the thermostats.
    apartment_device_id: str
    # The thermostats that each platform has added entities for, by platform.
    known_thermostats: dict[str, set[int]] = field(default_factory=dict)
