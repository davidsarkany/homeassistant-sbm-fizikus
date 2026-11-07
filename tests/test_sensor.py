"""Tests of the sensors of SBM Fizikus."""

import pytest

from homeassistant.components.sensor import (
    ATTR_OPTIONS,
    ATTR_STATE_CLASS,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    PERCENTAGE,
    STATE_UNKNOWN,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from .conftest import LIVING_ROOM_ID, SbmStub, setup_integration


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_sensors(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    await setup_integration(hass, config_entry)

    humidity = hass.states.get("sensor.living_room_humidity")
    assert humidity.state == "60"
    assert humidity.name == "living-room Humidity"
    assert humidity.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.HUMIDITY
    assert humidity.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT
    assert humidity.attributes[ATTR_UNIT_OF_MEASUREMENT] == PERCENTAGE

    for entity_id, value in [
        ("sensor.living_room_temperature", 24.8),
        ("sensor.living_room_target_temperature", 23.0),
        ("sensor.living_room_dew_point", 15.9),
    ]:
        state = hass.states.get(entity_id)
        assert float(state.state) == pytest.approx(value)
        assert state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.TEMPERATURE
        assert state.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT
        assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfTemperature.CELSIUS
    assert hass.states.get("sensor.living_room_dew_point").name == (
        "living-room Dew point"
    )

    system_mode = hass.states.get("sensor.living_room_system_mode")
    assert system_mode.state == "idle"
    assert system_mode.name == "living-room System mode"
    assert system_mode.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.ENUM
    assert system_mode.attributes[ATTR_OPTIONS] == [
        "heating",
        "cooling",
        "idle",
        "off",
    ]
    entry = entity_registry.async_get("sensor.living_room_system_mode")
    assert entry is not None
    assert entry.entity_category is EntityCategory.DIAGNOSTIC
    assert entry.unique_id == f"123_{LIVING_ROOM_ID}_system_mode"


@pytest.mark.parametrize(
    ("operation_mode", "active", "expected"),
    [
        (0, True, "heating"),
        (1, True, "cooling"),
        (0, False, "idle"),
        (1, False, "idle"),
        (2, True, STATE_UNKNOWN),
        (2, False, "idle"),
    ],
)
@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_system_mode(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    operation_mode: int,
    active: bool,
    expected: str,
) -> None:
    sbm.apartment_data["operationMode"] = operation_mode
    sbm.apartment_data["thermostats"][0]["active"] = active

    await setup_integration(hass, config_entry)

    assert hass.states.get("sensor.living_room_system_mode").state == expected


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_missing_values_are_unknown(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    sbm.apartment_data["thermostats"][0] = {"ID": LIVING_ROOM_ID, "name": "living-room"}

    await setup_integration(hass, config_entry)

    for suffix in ["humidity", "dew_point", "temperature", "target_temperature"]:
        assert hass.states.get(f"sensor.living_room_{suffix}").state == STATE_UNKNOWN


async def test_enabled_by_default(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """The climate entity, the humidity and the system switch are enabled."""
    await setup_integration(hass, config_entry)

    enabled = {
        entry.entity_id
        for entry in er.async_entries_for_config_entry(
            entity_registry, config_entry.entry_id
        )
        if not entry.disabled
    }
    rooms = ["living_room", "child_room_2", "child_room_1", "bedroom"]
    assert enabled == {"switch.sbm_fizikus_system"} | {
        f"climate.{room}" for room in rooms
    } | {f"sensor.{room}_humidity" for room in rooms}


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_system_mode_of_a_switched_off_system(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry, active: bool
) -> None:
    sbm.apartment_data["disabled"] = True
    sbm.apartment_data["communicationActiveRelayModule"] = False
    sbm.apartment_data["thermostats"][0]["active"] = active

    await setup_integration(hass, config_entry)

    assert hass.states.get("sensor.living_room_system_mode").state == "off"
