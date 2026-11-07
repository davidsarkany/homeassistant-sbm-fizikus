"""Tests of the switch of the SBM Fizikus system."""

import pytest

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.sbm_fizikus.const import DOMAIN

from .conftest import APARTMENT_ID, SbmStub, setup_integration

SYSTEM = "switch.sbm_fizikus_system"
UPDATE = "update_apartment_settings"


async def test_system_switch(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    await setup_integration(hass, config_entry)

    state = hass.states.get(SYSTEM)
    assert state.state == STATE_ON
    assert state.name == "SBM Fizikus System"
    entry = entity_registry.async_get(SYSTEM)
    assert entry is not None
    assert entry.unique_id == f"{APARTMENT_ID}_system"
    apartment = device_registry.async_get_device_by_identifier(
        (DOMAIN, str(APARTMENT_ID)), config_entry.entry_id
    )
    assert apartment is not None
    assert entry.device_id == apartment.id


@pytest.mark.parametrize(
    ("service", "disabled", "state", "climate"),
    [
        (SERVICE_TURN_OFF, True, STATE_OFF, "off"),
        (SERVICE_TURN_ON, False, STATE_ON, "heat"),
    ],
)
async def test_switch_the_system(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    service: str,
    disabled: bool,
    state: str,
    climate: str,
) -> None:
    sbm.apartment_data["disabled"] = not disabled
    await setup_integration(hass, config_entry)
    # SBM reports the new state as pending until the device confirms it, which takes
    # about half a minute (live capture of 2026-11-04).
    sbm.apartment_data["disabledCommand"] = disabled

    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: SYSTEM}, blocking=True
    )
    await hass.async_block_till_done()

    assert sbm.payloads(UPDATE) == [
        {
            "apartment_id": str(APARTMENT_ID),
            "disabled": disabled,
            "jwt_token": sbm.payloads("get_apartment_data")[0]["jwt_token"],
        }
    ]
    assert sbm.operations[-2:] == [UPDATE, "get_apartment_data"]
    assert hass.states.get(SYSTEM).state == state
    assert hass.states.get("climate.living_room").state == climate


async def test_switch_failure(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    sbm.queue(UPDATE, status=503, text="Unavailable")

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SYSTEM}, blocking=True
        )

    assert err.value.translation_key == "switch_system_failed"
    assert str(err.value) == (
        "Switching the SBM Fizikus system on or off failed: SBM request "
        "update_apartment_settings failed with status 503 (Service Unavailable): "
        "Unavailable"
    )
    assert hass.states.get(SYSTEM).state == STATE_ON
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_switch_with_refused_login_starts_reauth(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    sbm.queue(UPDATE, status=401)
    sbm.queue("get_jw_token", status=401)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SYSTEM}, blocking=True
        )
    await hass.async_block_till_done()

    assert err.value.translation_key == "auth_failed"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


CHILD_LOCK = "switch.living_room_child_lock"
SETTINGS = "update_thermostat_settings"


async def test_child_lock_is_disabled_by_default(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    await setup_integration(hass, config_entry)

    entry = entity_registry.async_get(CHILD_LOCK)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.entity_category is EntityCategory.CONFIG
    assert entry.unique_id == f"{APARTMENT_ID}_1001_child_lock"


@pytest.mark.parametrize(
    ("service", "locked", "state"),
    [(SERVICE_TURN_ON, True, STATE_ON), (SERVICE_TURN_OFF, False, STATE_OFF)],
)
@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_lock_a_thermostat(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    service: str,
    locked: bool,
    state: str,
) -> None:
    sbm.apartment_data["thermostats"][0]["childLock"] = not locked
    await setup_integration(hass, config_entry)
    assert hass.states.get(CHILD_LOCK).state != state
    assert hass.states.get(CHILD_LOCK).name == "living-room Child lock"
    # SBM reports the change as pending until the thermostat confirms it.
    sbm.apartment_data["thermostats"][0]["childLockCommand"] = locked

    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: CHILD_LOCK}, blocking=True
    )
    await hass.async_block_till_done()

    assert sbm.payloads(SETTINGS) == [
        {
            "thermostat_id": 1001,
            "child_lock": locked,
            "jwt_token": sbm.payloads("get_apartment_data")[0]["jwt_token"],
        }
    ]
    assert sbm.operations[-2:] == [SETTINGS, "get_apartment_data"]
    assert hass.states.get(CHILD_LOCK).state == state


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_child_lock_failure(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, config_entry)
    sbm.queue(SETTINGS, status=503, text="Unavailable")

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: CHILD_LOCK}, blocking=True
        )

    assert err.value.translation_key == "switch_child_lock_failed"
    assert hass.states.get(CHILD_LOCK).state == STATE_OFF


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_child_lock_of_a_disconnected_thermostat(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    """Like the other entities of a thermostat, it needs SBM to reach it."""
    sbm.apartment_data["communicationActiveThermostats"] = False

    await setup_integration(hass, config_entry)

    assert hass.states.get(CHILD_LOCK).state == STATE_UNAVAILABLE
    assert hass.states.get(SYSTEM).state == STATE_ON
