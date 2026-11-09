"""Tests of the setup of the SBM Fizikus integration."""

from typing import Any

import pytest

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.sbm_fizikus import async_remove_config_entry_device
from custom_components.sbm_fizikus.const import DOMAIN

from .conftest import APARTMENT_ID, LIVING_ROOM_ID, SbmStub, setup_integration

ROOMS = ["living_room", "child_room_2", "child_room_1", "bedroom"]
# The sensors that the MQTT bridge also has, with its entity ID suffixes.
BRIDGE_SENSORS = ["humidity", "temperature", "target_temperature", "system_mode"]


async def test_setup_and_unload(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    await setup_integration(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    apartment = device_registry.async_get_device_by_identifier(
        (DOMAIN, "123"), config_entry.entry_id
    )
    assert apartment is not None
    assert apartment.name == "SBM Fizikus"
    assert apartment.manufacturer == "SBM-Verting"
    assert apartment.model == "Fizikus"
    assert apartment.sw_version == "1.2.11-legacy"
    living_room = device_registry.async_get_device_by_identifier(
        (DOMAIN, "123_1001"), config_entry.entry_id
    )
    assert living_room is not None
    assert living_room.name == "living-room"
    assert living_room.manufacturer == "SBM-Verting"
    assert living_room.model == "Fizikus thermostat"
    assert living_room.via_device_id == apartment.id
    assert (
        len(dr.async_entries_for_config_entry(device_registry, config_entry.entry_id))
        == 5
    )
    # Setup polls the apartment data, after the login, and whether the building is
    # switched off, after looking up the building of the apartment.
    assert sbm.operations == [
        "get_jw_token",
        "get_apartment_data",
        "get_building_access_rights",
        "get_apartment_access_rights",
        "get_building_data",
    ]

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_new_firmware_is_shown_without_reload(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    await setup_integration(hass, config_entry)
    coordinator = config_entry.runtime_data.coordinator

    def firmware_version() -> str | None:
        device = device_registry.async_get_device_by_identifier(
            (DOMAIN, "123"), config_entry.entry_id
        )
        assert device is not None
        return device.sw_version

    sbm.apartment_data["fwVer"] = "1.2.12"
    await coordinator.async_refresh()

    assert firmware_version() == "1.2.12"

    # An answer without a firmware version keeps the known one.
    sbm.apartment_data["fwVer"] = None
    await coordinator.async_refresh()

    assert firmware_version() == "1.2.12"


async def test_setup_logs_why_sbm_cannot_be_reached(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An outage at startup is logged once, and so is its end, for every retry."""

    def warnings(text: str) -> int:
        return sum(
            record.levelname == "WARNING" and text in record.getMessage()
            for record in caplog.records
        )

    unreachable = "SBM cannot be reached, retrying in the background"
    sbm.queue("get_apartment_data", exc=TimeoutError())
    sbm.queue("get_apartment_data", exc=TimeoutError())

    await setup_integration(hass, config_entry)
    await hass.config_entries.async_reload(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert warnings(unreachable) == 1
    assert warnings("SBM did not answer get_apartment_data in time") == 1
    assert warnings("SBM can be reached again") == 0

    await hass.config_entries.async_reload(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.LOADED
    assert warnings(unreachable) == 1
    assert warnings("SBM can be reached again") == 1

    await hass.config_entries.async_reload(config_entry.entry_id)

    assert warnings("SBM can be reached again") == 1


@pytest.mark.parametrize(
    ("operation", "answer", "state"),
    [
        ("get_apartment_data", {"status": 500}, ConfigEntryState.SETUP_RETRY),
        ("get_apartment_data", {"exc": TimeoutError()}, ConfigEntryState.SETUP_RETRY),
        ("get_apartment_data", {"text": "<html>"}, ConfigEntryState.SETUP_RETRY),
        ("get_jw_token", {"status": 500}, ConfigEntryState.SETUP_RETRY),
        ("get_jw_token", {"status": 404}, ConfigEntryState.SETUP_RETRY),
        ("get_jw_token", {"status": 401}, ConfigEntryState.SETUP_ERROR),
        (
            "get_jw_token",
            {"status": 500, "json_body": {"error": "invalid password"}},
            ConfigEntryState.SETUP_ERROR,
        ),
        (
            "get_jw_token",
            {"status": 500, "json_body": {"error": "Internal server error"}},
            ConfigEntryState.SETUP_RETRY,
        ),
    ],
)
async def test_setup_failure(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    operation: str,
    answer: dict[str, Any],
    state: ConfigEntryState,
) -> None:
    sbm.queue(operation, **answer)

    await setup_integration(hass, config_entry)

    assert config_entry.state is state
    reauth_flows = [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]
    assert len(reauth_flows) == (state is ConfigEntryState.SETUP_ERROR)


@pytest.mark.parametrize("language", ["en", "hu"])
@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_entity_ids_match_the_bridge(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    language: str,
) -> None:
    """The entity IDs of the bridge's entities do not depend on the language."""
    hass.config.language = language

    await setup_integration(hass, config_entry)

    entity_ids = {
        entry.entity_id
        for entry in er.async_entries_for_config_entry(
            entity_registry, config_entry.entry_id
        )
    }
    assert entity_ids == {
        "sensor.sbm_fizikus_outdoor_temperature",
        "sensor.sbm_fizikus_outdoor_humidity",
        "switch.sbm_fizikus_system",
        "sensor.sbm_fizikus_forward_water_temperature",
        "sensor.sbm_fizikus_return_water_temperature",
        "sensor.sbm_fizikus_flow_rate",
    } | {
        entity_id
        for room in ROOMS
        for entity_id in [
            f"climate.{room}",
            f"sensor.{room}_dew_point",
            f"sensor.{room}_condensation_risk",
            f"switch.{room}_child_lock",
            *(f"sensor.{room}_{suffix}" for suffix in BRIDGE_SENSORS),
        ]
    }


async def test_thermostat_without_name(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A thermostat without a name is named after its id, like in the bridge."""
    sbm.apartment_data["thermostats"][0]["name"] = None

    await setup_integration(hass, config_entry)

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "123_1001"), config_entry.entry_id
    )
    assert device is not None
    assert device.name == "Thermostat 1001"
    assert hass.states.get("climate.thermostat_1001") is not None
    assert hass.states.get("sensor.thermostat_1001_humidity") is not None


@pytest.mark.parametrize("initial_name", ["living-room", None])
@pytest.mark.parametrize("user_name", [None, "My room"])
async def test_room_name_updates_without_changing_customizations(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    initial_name: str | None,
    user_name: str | None,
) -> None:
    sbm.apartment_data["thermostats"][0]["name"] = initial_name
    await setup_integration(hass, config_entry)
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "123_1001"), config_entry.entry_id
    )
    assert device is not None
    device_registry.async_update_device(device.id, name_by_user=user_name)
    entities = er.async_entries_for_device(entity_registry, device.id)
    original_ids = {entity.entity_id for entity in entities}
    climate = next(entity for entity in entities if entity.domain == "climate")
    entity_registry.async_update_entity(climate.entity_id, name="My thermostat")

    sbm.apartment_data["thermostats"][0]["name"] = "New room"
    await config_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    renamed = device_registry.async_get(device.id)
    assert renamed.name == "New room"
    assert renamed.name_by_user == user_name
    assert {
        entity.entity_id
        for entity in er.async_entries_for_device(entity_registry, device.id)
    } == original_ids
    assert entity_registry.async_get(climate.entity_id).name == "My thermostat"
    assert (
        hass.states.get(climate.entity_id).attributes["friendly_name"]
        == f"{user_name or 'New room'} My thermostat"
    )

    # A missing name in one response must not clear the last known room name.
    sbm.apartment_data["thermostats"][0]["name"] = None
    await config_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert device_registry.async_get(device.id).name == "New room"


async def test_remove_stale_thermostat(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Only a thermostat that SBM no longer reports can be removed."""
    await setup_integration(hass, config_entry)
    del sbm.apartment_data["thermostats"][0]
    await config_entry.runtime_data.coordinator.async_refresh()

    def device(identifier: str) -> dr.DeviceEntry:
        entry = device_registry.async_get_device_by_identifier(
            (DOMAIN, identifier), config_entry.entry_id
        )
        assert entry is not None
        return entry

    removed = device(f"{APARTMENT_ID}_{LIVING_ROOM_ID}")
    present = device(f"{APARTMENT_ID}_1004")
    apartment = device(str(APARTMENT_ID))
    assert await async_remove_config_entry_device(hass, config_entry, removed)
    assert not await async_remove_config_entry_device(hass, config_entry, present)
    assert not await async_remove_config_entry_device(hass, config_entry, apartment)


async def test_no_device_is_removed_while_the_entry_is_not_loaded(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Without a loaded entry the devices cannot be checked, so none is removed."""
    await setup_integration(hass, config_entry)
    del sbm.apartment_data["thermostats"][0]
    await config_entry.runtime_data.coordinator.async_refresh()
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{APARTMENT_ID}_{LIVING_ROOM_ID}"), config_entry.entry_id
    )
    assert device is not None
    # SBM is down when the entry is reloaded.
    sbm.queue("get_apartment_data", status=503)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY

    assert not await async_remove_config_entry_device(hass, config_entry, device)


async def test_new_thermostat_is_added_without_reload(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_integration(hass, config_entry)
    office = {**sbm.apartment_data["thermostats"][0], "ID": 1005, "name": "office"}
    sbm.apartment_data["thermostats"].append(office)

    await config_entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("climate.office").state == "heat"
    assert hass.states.get("sensor.office_humidity").state == "60"
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{APARTMENT_ID}_1005"), config_entry.entry_id
    )
    assert device is not None
    assert device.name == "office"
    assert (
        len(dr.async_entries_for_config_entry(device_registry, config_entry.entry_id))
        == 6
    )
    # The thermostats that were there already are not added again.
    assert "already exists" not in caplog.text


async def test_deleted_thermostat_gets_entities_when_it_comes_back(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    await setup_integration(hass, config_entry)
    coordinator = config_entry.runtime_data.coordinator
    living_room = sbm.apartment_data["thermostats"].pop(0)
    await coordinator.async_refresh()
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{APARTMENT_ID}_{LIVING_ROOM_ID}"), config_entry.entry_id
    )
    assert device is not None

    # What deleting the device on its page does.
    assert await async_remove_config_entry_device(hass, config_entry, device)
    device_registry.async_remove_device(device.id)
    await hass.async_block_till_done()
    assert entity_registry.async_get("climate.living_room") is None

    sbm.apartment_data["thermostats"].insert(0, living_room)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("climate.living_room").state == "heat"
    assert hass.states.get("sensor.living_room_humidity").state == "60"
