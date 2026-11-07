"""Tests of the polling of SBM Fizikus."""

from datetime import timedelta
import logging
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.sbm_fizikus.const import DOMAIN

from .conftest import SbmStub, setup_integration

LIVING_ROOM = "climate.living_room"
BEDROOM = "climate.bedroom"
THERMOSTATS_FLAG = "communicationActiveThermostats"
DISCONNECTED = "SBM does not reach the thermostats, the entities are unavailable"
RECONNECTED = "SBM reaches the thermostats again, the entities are available"


def warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the messages that Home Assistant shows at its default log level."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]


async def refresh(config_entry: MockConfigEntry) -> None:
    """Poll SBM now."""
    await config_entry.runtime_data.coordinator.async_refresh()


async def test_polls_every_two_minutes(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_integration(hass, config_entry)
    assert sbm.operations.count("get_apartment_data") == 1

    freezer.tick(timedelta(seconds=100))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert sbm.operations.count("get_apartment_data") == 1

    freezer.tick(timedelta(seconds=21))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert sbm.operations.count("get_apartment_data") == 2
    # The token is still valid, so there is no new login.
    assert sbm.operations.count("get_jw_token") == 1


async def test_debug_log_shows_the_parsed_state(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.sbm_fizikus")

    await setup_integration(hass, config_entry)

    assert "Parsed state of apartment 123: SbmApartmentData(" in caplog.text
    assert "target_temperature=23.0" in caplog.text


async def test_failed_poll_and_recovery(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_integration(hass, config_entry)
    sbm.queue("get_apartment_data", status=500, text="Internal error")
    sbm.queue("get_apartment_data", exc=TimeoutError())

    await refresh(config_entry)
    await refresh(config_entry)

    assert hass.states.get(LIVING_ROOM).state == STATE_UNAVAILABLE
    # Logged once, with the translated message.
    assert caplog.text.count("Error fetching sbm_fizikus data") == 1
    assert (
        "Fetching the data from SBM failed: SBM request get_apartment_data failed "
        "with status 500 (Internal Server Error): Internal error"
    ) in caplog.text

    await refresh(config_entry)

    assert hass.states.get(LIVING_ROOM).state == "heat"
    assert caplog.text.count("Fetching sbm_fizikus data recovered") == 1
    assert warnings(caplog).count("SBM can be reached again") == 1

    await refresh(config_entry)

    assert warnings(caplog).count("SBM can be reached again") == 1


@pytest.mark.parametrize(
    "answer",
    [{"status": status} for status in [400, 404, 405, 408, 429, 503]]
    + [
        {"json_body": {"error": "Internal server error"}},
        {"json_body": {"token": None}},
        {"json_body": {"token": ""}},
        {"json_body": {"token": 5}},
    ],
)
async def test_failed_relogin_keeps_polling_and_recovers(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    answer: dict[str, Any],
) -> None:
    await setup_integration(hass, config_entry)
    sbm.queue("get_apartment_data", status=401)
    sbm.queue("get_jw_token", **answer)

    freezer.tick(timedelta(seconds=121))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(LIVING_ROOM).state == STATE_UNAVAILABLE
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []
    assert sbm.operations.count("get_jw_token") == 2
    assert sbm.operations.count("get_apartment_data") == 2

    # The next scheduled poll logs in again and recovers without user input.
    freezer.tick(timedelta(seconds=121))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(LIVING_ROOM).state == "heat"
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []
    assert sbm.operations.count("get_jw_token") == 3
    assert sbm.operations.count("get_apartment_data") == 3


@pytest.mark.parametrize(
    "refusal",
    [
        {"status": 401},
        # What SBM answers to a wrong password (live capture, 2026-11-07).
        {"status": 500, "json_body": {"error": "invalid password"}},
    ],
)
async def test_refused_login_starts_reauth(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    refusal: dict[str, Any],
) -> None:
    await setup_integration(hass, config_entry)
    # The password was changed: the token is rejected, and so is the new login.
    sbm.queue("get_apartment_data", status=401)
    sbm.queue("get_jw_token", **refusal)

    await refresh(config_entry)

    assert hass.states.get(LIVING_ROOM).state == STATE_UNAVAILABLE
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_disconnected_system(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Entities are unavailable while SBM does not reach the thermostats."""
    await setup_integration(hass, config_entry)

    sbm.apartment_data[THERMOSTATS_FLAG] = False
    await refresh(config_entry)
    await refresh(config_entry)

    assert hass.states.get(LIVING_ROOM).state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.living_room_humidity").state == STATE_UNAVAILABLE
    assert caplog.text.count(DISCONNECTED) == 1
    assert RECONNECTED not in caplog.text

    sbm.apartment_data[THERMOSTATS_FLAG] = True
    await refresh(config_entry)
    await refresh(config_entry)

    assert hass.states.get(LIVING_ROOM).state == "heat"
    assert caplog.text.count(DISCONNECTED) == 1
    assert caplog.text.count(RECONNECTED) == 1
    assert any(RECONNECTED in message for message in warnings(caplog))


async def test_relay_module_flag_is_ignored(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Like the app, only the flag of the thermostats counts as the connection."""
    await setup_integration(hass, config_entry)

    sbm.apartment_data["communicationActiveRelayModule"] = False
    await refresh(config_entry)

    assert hass.states.get(LIVING_ROOM).state == "heat"
    assert DISCONNECTED not in caplog.text


async def test_disconnected_at_setup(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sbm.apartment_data[THERMOSTATS_FLAG] = False

    await setup_integration(hass, config_entry)

    assert hass.states.get(LIVING_ROOM).state == STATE_UNAVAILABLE
    assert caplog.text.count(DISCONNECTED) == 1


async def test_thermostat_missing_from_the_answer(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    living_room = sbm.apartment_data["thermostats"].pop(0)

    await refresh(config_entry)

    assert hass.states.get(LIVING_ROOM).state == STATE_UNAVAILABLE
    assert hass.states.get(BEDROOM).state == "heat"

    # Back, with a new temperature.
    sbm.apartment_data["thermostats"].insert(
        0, {**living_room, "measuredTempDegC": 22.4}
    )
    await refresh(config_entry)

    state = hass.states.get(LIVING_ROOM)
    assert state.state == "heat"
    assert state.attributes["current_temperature"] == 22.4


async def test_switching_the_system_off_and_on(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Switching the system off is not a lost connection."""
    await setup_integration(hass, config_entry)

    # A switched-off system does not report its relay module.
    sbm.apartment_data["disabled"] = True
    sbm.apartment_data["communicationActiveRelayModule"] = False
    await refresh(config_entry)

    assert hass.states.get(LIVING_ROOM).state == "off"
    assert hass.states.get("sensor.living_room_humidity").state == "60"
    assert DISCONNECTED not in caplog.text

    sbm.apartment_data["disabled"] = False
    sbm.apartment_data["communicationActiveRelayModule"] = True
    await refresh(config_entry)

    assert hass.states.get(LIVING_ROOM).state == "heat"
    assert RECONNECTED not in caplog.text
