"""Tests of the system switched off for the whole building."""

from datetime import timedelta
import logging

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.climate import ATTR_HVAC_ACTION, ATTR_HVAC_MODES
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.sbm_fizikus.const import DOMAIN
from custom_components.sbm_fizikus.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import LOGIN_REFUSED, SbmStub, setup_integration

LIVING_ROOM = "climate.living_room"
SYSTEM = "switch.sbm_fizikus_system"
BUILDING = "get_building_data"
BUILDINGS = "get_building_access_rights"
APARTMENTS = "get_apartment_access_rights"
BUILDING_OFF = {"data": {"disabled": True}}
BUILDING_ON = {"data": {"disabled": False}}


async def wait(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: int
) -> None:
    """Let some polls of the apartment pass."""
    for _ in range(minutes // 2):
        freezer.tick(timedelta(seconds=120))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)


async def test_building_switched_off(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    """Every apartment is off then, like the app shows it."""
    sbm.queue(BUILDING, json_body=BUILDING_OFF)

    await setup_integration(hass, config_entry)

    state = hass.states.get(LIVING_ROOM)
    assert state.state == "off"
    assert state.attributes[ATTR_HVAC_ACTION] == "off"
    assert state.attributes[ATTR_HVAC_MODES] == ["heat", "cool", "off"]
    # The apartment cannot be switched while the building is off, so the app greys
    # its switch out.
    assert hass.states.get(SYSTEM).state == STATE_UNAVAILABLE
    # The thermostats still report their values.
    assert state.attributes["current_temperature"] == 24.8
    assert sbm.payloads(BUILDING)[0]["building_id"] == 24
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diagnostics["building_off"] is True


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_system_mode_while_the_building_is_off(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    sbm.queue(BUILDING, json_body=BUILDING_OFF)

    await setup_integration(hass, config_entry)

    assert hass.states.get("sensor.living_room_system_mode").state == "off"


async def test_building_is_checked_every_10_minutes(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_integration(hass, config_entry)
    assert hass.states.get(LIVING_ROOM).state == "heat"
    assert sbm.operations.count(BUILDING) == 1

    sbm.queue(BUILDING, json_body=BUILDING_OFF)
    await wait(hass, freezer, 8)

    # The polls in between keep the last known value.
    assert sbm.operations.count("get_apartment_data") == 5
    assert sbm.operations.count(BUILDING) == 1
    assert hass.states.get(LIVING_ROOM).state == "heat"

    await wait(hass, freezer, 2)

    assert sbm.operations.count(BUILDING) == 2
    assert hass.states.get(LIVING_ROOM).state == "off"
    assert hass.states.get(SYSTEM).state == STATE_UNAVAILABLE
    assert (
        "SBM reports the system as switched off for the whole building" in caplog.text
    )

    await wait(hass, freezer, 10)

    assert hass.states.get(LIVING_ROOM).state == "heat"
    assert hass.states.get(SYSTEM).state == STATE_ON
    assert "SBM reports the system as switched on for the whole building" in (
        caplog.text
    )
    # The building is looked up only once.
    assert sbm.operations.count(APARTMENTS) == 1


async def test_building_that_cannot_be_fetched(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The thermostats stay available, with the last known value of the building."""
    caplog.set_level(logging.DEBUG, logger="custom_components.sbm_fizikus")
    sbm.queue(BUILDING, json_body=BUILDING_OFF)
    await setup_integration(hass, config_entry)
    sbm.queue(BUILDING, status=503, text="Unavailable")

    await wait(hass, freezer, 10)

    assert hass.states.get(LIVING_ROOM).state == "off"
    assert "Whether the building is switched off could not be fetched" in caplog.text
    assert "Error fetching" not in caplog.text

    # The next poll looks the building up again, as it may be gone, and asks again.
    await wait(hass, freezer, 2)

    assert hass.states.get(LIVING_ROOM).state == "heat"
    assert sbm.operations.count(APARTMENTS) == 2
    assert sbm.operations.count(BUILDING) == 3

    # After an answer, the building is asked every 10 minutes again.
    await wait(hass, freezer, 8)

    assert sbm.operations.count(BUILDING) == 3

    await wait(hass, freezer, 2)

    assert sbm.operations.count(BUILDING) == 4


@pytest.mark.parametrize(
    ("operation", "answer"),
    [
        # SBM does not list the apartment in any building.
        (APARTMENTS, {"json_body": [[24, 701, "BP-TEST-001-100", True]]}),
        (BUILDINGS, {"status": 500}),
        (BUILDING, {"json_body": {"data": {"disabled": None}}}),
    ],
)
async def test_building_unknown_at_setup(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    operation: str,
    answer: dict[str, object],
) -> None:
    """Without the building, the system is not switched off for it."""
    sbm.queue(operation, **answer)

    await setup_integration(hass, config_entry)

    assert hass.states.get(LIVING_ROOM).state == "heat"
    assert hass.states.get(SYSTEM).state == STATE_ON


async def test_building_that_cannot_be_fetched_at_setup(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The next poll asks again, so a switched-off building shows without a wait."""
    sbm.queue(BUILDING, status=503, text="Unavailable")
    sbm.queue(BUILDING, json_body=BUILDING_OFF)

    await setup_integration(hass, config_entry)

    assert hass.states.get(LIVING_ROOM).state == "heat"

    await wait(hass, freezer, 2)

    assert hass.states.get(LIVING_ROOM).state == "off"
    assert hass.states.get(SYSTEM).state == STATE_UNAVAILABLE


async def test_refused_login_while_checking_the_building(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, config_entry)
    sbm.queue(BUILDING, status=401)
    sbm.queue("get_jw_token", **LOGIN_REFUSED)

    await wait(hass, freezer, 10)

    assert hass.states.get(LIVING_ROOM).state == STATE_UNAVAILABLE
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_debug_log_leaves_out_the_address_of_the_building(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.sbm_fizikus")

    await setup_integration(hass, config_entry)

    assert "SBM answer to get_building_data, status 200" in caplog.text
    assert "Teszt utca" not in caplog.text
    assert "building_off=False" in caplog.text
