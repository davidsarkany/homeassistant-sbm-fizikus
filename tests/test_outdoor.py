"""Tests of the outdoor sensors of SBM Fizikus."""

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.sensor import ATTR_STATE_CLASS, SensorDeviceClass
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    PERCENTAGE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.sbm_fizikus.api import SbmBuilding
from custom_components.sbm_fizikus.const import DOMAIN
from custom_components.sbm_fizikus.coordinator import SbmFizikusOutdoorCoordinator

from .conftest import APARTMENT_ID, SbmStub, setup_integration

OUTDOOR_TEMPERATURE = "sensor.sbm_fizikus_outdoor_temperature"
OUTDOOR_HUMIDITY = "sensor.sbm_fizikus_outdoor_humidity"
OUTDOOR_SENSORS = [OUTDOOR_TEMPERATURE, OUTDOOR_HUMIDITY]
LIVING_ROOM = "climate.living_room"
BUILDINGS = "get_building_access_rights"
APARTMENTS = "get_apartment_access_rights"


def building(building_id: int, temperature: Any, humidity: Any) -> list[Any]:
    """Return a row of get_building_access_rights."""
    return [
        building_id,
        "Magyarország",
        1111,
        "Budapest",
        "Teszt utca 1",
        temperature,
        humidity,
        f"T{building_id}",
    ]


async def refresh(config_entry: MockConfigEntry) -> None:
    """Fetch the outdoor weather now."""
    await config_entry.runtime_data.outdoor_coordinator.async_refresh()


async def wait_update_interval(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Let the update interval pass."""
    freezer.tick(timedelta(seconds=121))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_disabled_by_default_and_not_polled(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, config_entry)

    for entity_id in OUTDOOR_SENSORS:
        entry = entity_registry.async_get(entity_id)
        assert entry is not None
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert hass.states.get(entity_id) is None

    await wait_update_interval(hass, freezer)

    assert sbm.operations.count("get_apartment_data") == 2
    # Only the lookup of the building of the apartment, at setup, for whether the
    # building is switched off; no poll of the outdoor weather.
    assert sbm.operations.count(BUILDINGS) == 1
    assert sbm.operations.count(APARTMENTS) == 1


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_outdoor_sensors(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    await setup_integration(hass, config_entry)

    temperature = hass.states.get(OUTDOOR_TEMPERATURE)
    assert temperature.state == "19.9"
    assert temperature.name == "SBM Fizikus Outdoor temperature"
    assert temperature.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.TEMPERATURE
    assert temperature.attributes[ATTR_STATE_CLASS] == "measurement"
    assert temperature.attributes[ATTR_UNIT_OF_MEASUREMENT] == (
        UnitOfTemperature.CELSIUS
    )
    humidity = hass.states.get(OUTDOOR_HUMIDITY)
    assert humidity.state == "49.9"
    assert humidity.name == "SBM Fizikus Outdoor humidity"
    assert humidity.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.HUMIDITY
    assert humidity.attributes[ATTR_STATE_CLASS] == "measurement"
    assert humidity.attributes[ATTR_UNIT_OF_MEASUREMENT] == PERCENTAGE

    apartment = device_registry.async_get_device_by_identifier(
        (DOMAIN, str(APARTMENT_ID)), config_entry.entry_id
    )
    assert apartment is not None
    for entity_id, key in [
        (OUTDOOR_TEMPERATURE, "outdoor_temperature"),
        (OUTDOOR_HUMIDITY, "outdoor_humidity"),
    ]:
        entry = entity_registry.async_get(entity_id)
        assert entry is not None
        assert entry.unique_id == f"{APARTMENT_ID}_{key}"
        assert entry.device_id == apartment.id

    # One fetch for both sensors; the building was looked up once, at setup.
    assert sbm.operations.count(BUILDINGS) == 2
    assert sbm.operations.count(APARTMENTS) == 1
    assert sbm.payloads(APARTMENTS)[0]["building_id"] == "24"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_slow_outdoor_fetch_does_not_hold_up_the_setup(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    answered = asyncio.Event()
    original = SbmFizikusOutdoorCoordinator._async_update_data

    async def slow_update(self: SbmFizikusOutdoorCoordinator) -> SbmBuilding:
        await answered.wait()
        return await original(self)

    config_entry.add_to_hass(hass)
    with patch.object(SbmFizikusOutdoorCoordinator, "_async_update_data", slow_update):
        assert await hass.config_entries.async_setup(config_entry.entry_id)

        assert config_entry.state is ConfigEntryState.LOADED
        for entity_id in OUTDOOR_SENSORS:
            assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

        answered.set()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(OUTDOOR_TEMPERATURE).state == "19.9"
    assert hass.states.get(OUTDOOR_HUMIDITY).state == "49.9"
    assert sbm.operations.count(BUILDINGS) == 2


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_readded_sensor_does_not_fetch_again(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Renaming the entity ID adds the sensor again, without a reload."""
    await setup_integration(hass, config_entry)

    entity_registry.async_update_entity(
        OUTDOOR_TEMPERATURE, new_entity_id="sensor.outside"
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get("sensor.outside").state == "19.9"
    assert sbm.operations.count(BUILDINGS) == 2


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_polls_with_the_thermostats(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, config_entry)
    sbm.queue(BUILDINGS, json_body=[building(24, 12.5, 80)])

    await wait_update_interval(hass, freezer)

    assert hass.states.get(OUTDOOR_TEMPERATURE).state == "12.5"
    assert hass.states.get(OUTDOOR_HUMIDITY).state == "80.0"
    assert sbm.operations.count(BUILDINGS) == 3
    # The building is looked up only once.
    assert sbm.operations.count(APARTMENTS) == 1


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_disabling_the_sensors_stops_polling(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, config_entry)

    entity_registry.async_update_entity(
        OUTDOOR_TEMPERATURE, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    await wait_update_interval(hass, freezer)

    # The humidity still listens.
    assert sbm.operations.count(BUILDINGS) == 3

    entity_registry.async_update_entity(
        OUTDOOR_HUMIDITY, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    await wait_update_interval(hass, freezer)

    assert sbm.operations.count(BUILDINGS) == 3
    assert sbm.operations.count("get_apartment_data") == 3


@pytest.mark.parametrize(
    "row", [[24], building(24, None, "49.9"), [24, "", 0, "", "", True, float("inf")]]
)
@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_missing_values_are_unknown(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry, row: list[Any]
) -> None:
    # For the lookup of the building at setup, then for the outdoor weather.
    sbm.queue(BUILDINGS, json_body=[row])
    sbm.queue(BUILDINGS, json_body=[row])

    await setup_integration(hass, config_entry)

    for entity_id in OUTDOOR_SENSORS:
        assert hass.states.get(entity_id).state == STATE_UNKNOWN


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_apartment_in_another_building(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    buildings = [building(23, 5, 90), building(24, 19.9, 49.9)]
    # For the lookup of the building at setup, then for the outdoor weather.
    sbm.queue(BUILDINGS, json_body=buildings)
    sbm.queue(BUILDINGS, json_body=buildings)
    sbm.queue(APARTMENTS, json_body=[[23, 701, "BP-TEST-001-100", True]])

    await setup_integration(hass, config_entry)

    assert hass.states.get(OUTDOOR_TEMPERATURE).state == "19.9"
    assert [payload["building_id"] for payload in sbm.payloads(APARTMENTS)] == [
        "23",
        "24",
    ]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_building_not_found(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    freezer: FrozenDateTimeFactory,
) -> None:
    # SBM does not list the apartment, for the lookup at setup and the outdoor one.
    sbm.queue(APARTMENTS, json_body=[[24, 701, "BP-TEST-001-100", True]])
    sbm.queue(APARTMENTS, json_body=[[24, 701, "BP-TEST-001-100", True]])

    await setup_integration(hass, config_entry)

    for entity_id in OUTDOOR_SENSORS:
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert hass.states.get(LIVING_ROOM).state == "heat"
    assert (
        "SBM does not list the building of apartment 123, so the outdoor "
        "temperature and humidity are not available"
    ) in caplog.text
    # The failed fetch is not repeated for the second sensor.
    assert sbm.operations.count(BUILDINGS) == 2

    # The next poll looks the building up again.
    await wait_update_interval(hass, freezer)

    assert hass.states.get(OUTDOOR_TEMPERATURE).state == "19.9"
    assert sbm.operations.count(APARTMENTS) == 3


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_building_looked_up_again_when_it_is_gone(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    sbm.queue(BUILDINGS, json_body=[building(25, 7, 70)])
    sbm.queue(APARTMENTS, json_body=[[25, APARTMENT_ID, "BP-TEST-002-101", True]])

    await refresh(config_entry)

    assert hass.states.get(OUTDOOR_TEMPERATURE).state == "7.0"
    assert [payload["building_id"] for payload in sbm.payloads(APARTMENTS)] == [
        "24",
        "25",
    ]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_failed_poll_and_recovery(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_integration(hass, config_entry)
    sbm.queue(BUILDINGS, status=500, text="Internal error")

    await refresh(config_entry)

    for entity_id in OUTDOOR_SENSORS:
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    # The thermostats do not depend on the outdoor weather.
    assert hass.states.get(LIVING_ROOM).state == "heat"
    assert (
        "Error fetching sbm_fizikus outdoor data: Fetching the data from SBM failed: "
        "SBM request get_building_access_rights failed with status 500 "
        "(Internal Server Error): Internal error"
    ) in caplog.text
    recovered = "The outdoor weather can be fetched from SBM again"
    assert recovered not in caplog.text

    await refresh(config_entry)

    assert hass.states.get(OUTDOOR_TEMPERATURE).state == "19.9"
    assert caplog.text.count(recovered) == 1


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_refused_login_starts_reauth(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    sbm.queue(BUILDINGS, status=401)
    sbm.queue("get_jw_token", status=401)

    await refresh(config_entry)

    assert hass.states.get(OUTDOOR_TEMPERATURE).state == STATE_UNAVAILABLE
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
