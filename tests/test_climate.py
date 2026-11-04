"""Tests of the climate entities of SBM Fizikus."""

from typing import Any

import pytest

from homeassistant.components.climate import (
    ATTR_CURRENT_HUMIDITY,
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry

from custom_components.sbm_fizikus.const import DOMAIN

from .conftest import LIVING_ROOM_ID, SbmStub, setup_integration

LIVING_ROOM = "climate.living_room"
SET_SETPOINT = "update_thermostat_settings"


async def test_climate_entity(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    await setup_integration(hass, config_entry)

    state = hass.states.get(LIVING_ROOM)
    assert state.state == HVACMode.HEAT
    assert state.name == "living-room"
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 24.8
    assert state.attributes[ATTR_CURRENT_HUMIDITY] == 60.0
    assert state.attributes[ATTR_TEMPERATURE] == 23.0
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.IDLE
    assert state.attributes[ATTR_HVAC_MODES] == [HVACMode.HEAT, HVACMode.COOL]
    assert state.attributes[ATTR_MIN_TEMP] == 5
    assert state.attributes[ATTR_MAX_TEMP] == 35
    assert state.attributes[ATTR_TARGET_TEMP_STEP] == 0.5
    assert (
        state.attributes[ATTR_SUPPORTED_FEATURES]
        == ClimateEntityFeature.TARGET_TEMPERATURE
    )
    entry = entity_registry.async_get(LIVING_ROOM)
    assert entry is not None
    assert entry.unique_id == f"123_{LIVING_ROOM_ID}_climate"


@pytest.mark.parametrize(
    ("operation_mode", "active", "state", "action"),
    [
        (0, True, HVACMode.HEAT, HVACAction.HEATING),
        (0, False, HVACMode.HEAT, HVACAction.IDLE),
        (1, True, HVACMode.COOL, HVACAction.COOLING),
        (1, False, HVACMode.COOL, HVACAction.IDLE),
        # An unknown mode of SBM is unknown, never off.
        (2, True, STATE_UNKNOWN, None),
        (2, False, STATE_UNKNOWN, HVACAction.IDLE),
    ],
)
async def test_hvac_mode_and_action(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    operation_mode: int,
    active: bool,
    state: str,
    action: HVACAction | None,
) -> None:
    sbm.apartment_data["operationMode"] = operation_mode
    sbm.apartment_data["thermostats"][0]["active"] = active

    await setup_integration(hass, config_entry)

    entity = hass.states.get(LIVING_ROOM)
    assert entity.state == state
    assert entity.attributes.get(ATTR_HVAC_ACTION) == action


async def test_missing_values_are_unknown(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    sbm.apartment_data["thermostats"][0] = {"ID": LIVING_ROOM_ID, "name": "living-room"}

    await setup_integration(hass, config_entry)

    state = hass.states.get(LIVING_ROOM)
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] is None
    assert state.attributes[ATTR_TEMPERATURE] is None
    assert ATTR_CURRENT_HUMIDITY not in state.attributes
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.IDLE


async def test_set_temperature(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    # SBM reports the new target temperature as pending until the thermostat
    # confirms it, which takes one or two minutes (live capture of 2026-11-04).
    sbm.apartment_data["thermostats"][0]["temperatureSetpointCommandDegC"] = 21.9

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: LIVING_ROOM, ATTR_TEMPERATURE: 21.94},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert sbm.payloads(SET_SETPOINT) == [
        {
            "thermostat_id": LIVING_ROOM_ID,
            "temp_setpoint_degC": 21.9,
            "jwt_token": sbm.payloads("get_apartment_data")[0]["jwt_token"],
        }
    ]
    # The state is refreshed at once, with no optimistic value.
    assert sbm.operations[-2:] == [SET_SETPOINT, "get_apartment_data"]
    assert hass.states.get(LIVING_ROOM).attributes[ATTR_TEMPERATURE] == 21.9


async def test_set_temperature_with_the_current_hvac_mode(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: LIVING_ROOM, ATTR_TEMPERATURE: 22, ATTR_HVAC_MODE: "heat"},
        blocking=True,
    )

    assert [
        payload["temp_setpoint_degC"] for payload in sbm.payloads(SET_SETPOINT)
    ] == [22]


async def test_set_temperature_with_another_hvac_mode(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    """The mode is ignored, the temperature is set."""
    await setup_integration(hass, config_entry)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: LIVING_ROOM, ATTR_TEMPERATURE: 22, ATTR_HVAC_MODE: "cool"},
        blocking=True,
    )

    assert [
        payload["temp_setpoint_degC"] for payload in sbm.payloads(SET_SETPOINT)
    ] == [22]
    assert hass.states.get(LIVING_ROOM).state == HVACMode.HEAT


@pytest.mark.parametrize(
    "temperature", [4.9, 35.1, float("inf"), float("-inf"), "inf", "-inf"]
)
async def test_set_temperature_out_of_range(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    temperature: float | str,
) -> None:
    await setup_integration(hass, config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: LIVING_ROOM, ATTR_TEMPERATURE: temperature},
            blocking=True,
        )

    assert sbm.payloads(SET_SETPOINT) == []


@pytest.mark.parametrize("temperature", ["nan", "NaN", float("nan")])
async def test_set_non_finite_temperature(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    temperature: float | str,
) -> None:
    await setup_integration(hass, config_entry)
    requests = len(sbm.requests)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: LIVING_ROOM, ATTR_TEMPERATURE: temperature},
            blocking=True,
        )

    assert err.value.translation_key == "invalid_temperature"
    assert err.value.translation_domain == DOMAIN
    assert str(err.value) == (
        "The target temperature must be a finite number. "
        "NaN and infinity are not allowed"
    )
    assert len(sbm.requests) == requests
    assert hass.states.get(LIVING_ROOM).attributes[ATTR_TEMPERATURE] == 23.0
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_set_temperature_failure(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    sbm.queue(SET_SETPOINT, status=503, text="Unavailable")

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: LIVING_ROOM, ATTR_TEMPERATURE: 21},
            blocking=True,
        )

    assert err.value.translation_key == "set_temperature_failed"
    assert str(err.value) == (
        "Setting the target temperature failed: SBM request "
        "update_thermostat_settings failed with status 503 (Service Unavailable): "
        "Unavailable"
    )
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_set_temperature_with_refused_login_starts_reauth(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    sbm.queue(SET_SETPOINT, status=401)
    sbm.queue("get_jw_token", status=401)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: LIVING_ROOM, ATTR_TEMPERATURE: 21},
            blocking=True,
        )
    await hass.async_block_till_done()

    assert err.value.translation_key == "auth_failed"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_set_hvac_mode(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    """The mode is set centrally: every mode is accepted and has no effect."""
    await setup_integration(hass, config_entry)
    requests = len(sbm.requests)

    for hvac_mode in [HVACMode.HEAT, HVACMode.COOL]:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: LIVING_ROOM, ATTR_HVAC_MODE: hvac_mode},
            blocking=True,
        )

    assert hass.states.get(LIVING_ROOM).state == HVACMode.HEAT
    assert len(sbm.requests) == requests


@pytest.mark.parametrize("active", [True, False])
async def test_switched_off_system(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry, active: bool
) -> None:
    """A switched-off system is off, and its thermostats still report values."""
    sbm.apartment_data["disabled"] = True
    sbm.apartment_data["communicationActiveRelayModule"] = False
    sbm.apartment_data["thermostats"][0]["active"] = active

    await setup_integration(hass, config_entry)

    state = hass.states.get(LIVING_ROOM)
    assert state.state == HVACMode.OFF
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.OFF
    assert state.attributes[ATTR_HVAC_MODES] == [
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.OFF,
    ]
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 24.8
    assert state.attributes[ATTR_TEMPERATURE] == 23.0


async def test_set_hvac_mode_of_a_switched_off_system(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    sbm.apartment_data["disabled"] = True
    sbm.apartment_data["communicationActiveRelayModule"] = False
    await setup_integration(hass, config_entry)
    requests = len(sbm.requests)

    for hvac_mode in [HVACMode.OFF, HVACMode.HEAT]:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: LIVING_ROOM, ATTR_HVAC_MODE: hvac_mode},
            blocking=True,
        )

    # The System switch switches the system, the climate entity does not.
    assert hass.states.get(LIVING_ROOM).state == HVACMode.OFF
    assert len(sbm.requests) == requests


async def test_set_temperature_of_a_switched_off_system(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    """The target temperature can still be set; SBM keeps it for later."""
    sbm.apartment_data["disabled"] = True
    sbm.apartment_data["communicationActiveRelayModule"] = False
    await setup_integration(hass, config_entry)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: LIVING_ROOM, ATTR_TEMPERATURE: 21},
        blocking=True,
    )

    assert [
        payload["temp_setpoint_degC"] for payload in sbm.payloads(SET_SETPOINT)
    ] == [21]


async def apply_scene(hass: HomeAssistant, state: str, temperature: float) -> None:
    """Apply a scene as the scene editor saves it: the mode and the temperature."""
    await hass.services.async_call(
        "scene",
        "apply",
        {"entities": {LIVING_ROOM: {"state": state, "temperature": temperature}}},
        blocking=True,
    )


@pytest.mark.parametrize(
    ("change", "scene_state"),
    [
        # Saved while the building heated.
        ({}, "heat"),
        # Saved in summer while the building cooled, applied in winter.
        ({}, "cool"),
        # Saved in winter, applied while the system is switched off.
        ({"disabled": True, "communicationActiveRelayModule": False}, "heat"),
    ],
)
async def test_scene_sets_the_temperature_in_any_mode(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    change: dict[str, Any],
    scene_state: str,
) -> None:
    sbm.apartment_data.update(change)
    await setup_integration(hass, config_entry)
    assert await async_setup_component(hass, "scene", {})
    await hass.async_block_till_done()

    await apply_scene(hass, scene_state, 20)

    assert [
        payload["temp_setpoint_degC"] for payload in sbm.payloads(SET_SETPOINT)
    ] == [20]


async def test_scene_saved_while_the_system_was_off(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    """Home Assistant refuses off while the system is on (a known limitation)."""
    await setup_integration(hass, config_entry)
    assert await async_setup_component(hass, "scene", {})
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await apply_scene(hass, "off", 20)

    assert sbm.payloads(SET_SETPOINT) == []
