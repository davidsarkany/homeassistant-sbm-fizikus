"""Tests of the diagnostics of SBM Fizikus."""

import json

import pytest

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from custom_components.sbm_fizikus.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import (
    APARTMENT_ID,
    APARTMENT_NAME,
    PWHASH,
    USERNAME,
    SbmStub,
    setup_integration,
)


async def test_diagnostics(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    # The names help to match the entities, so they stay.
    assert diagnostics["entry"]["title"] == APARTMENT_NAME
    assert diagnostics["entry"]["data"] == {
        "username": REDACTED,
        "pwhash": REDACTED,
        "apartment_id": APARTMENT_ID,
    }
    assert diagnostics["last_update_success"] is True
    apartment_data = diagnostics["apartment_data"]
    assert apartment_data["name"] == APARTMENT_NAME
    assert apartment_data["operationMode"] == 0
    assert [thermostat["name"] for thermostat in apartment_data["thermostats"]] == [
        "living-room",
        "child-room-2",
        "child-room-1",
        "bedroom",
    ]
    # No outdoor sensor is enabled, so the outdoor weather was not fetched.
    assert diagnostics["outdoor"] == {"last_update_success": True, "data": None}
    dumped = json.dumps(diagnostics, default=str)
    for secret in [PWHASH, USERNAME]:
        assert secret not in dumped


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_diagnostics_with_outdoor_weather(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    assert diagnostics["outdoor"] == {
        "last_update_success": True,
        "data": {
            "building_id": 24,
            "outdoor_temperature": 19.9,
            "outdoor_humidity": 49.9,
        },
    }
    # The address of the building is left out.
    assert "Teszt utca" not in json.dumps(diagnostics, default=str)
