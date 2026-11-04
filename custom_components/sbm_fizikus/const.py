"""Constants of the SBM Fizikus integration."""

from datetime import timedelta
import logging
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "sbm_fizikus"
LOGGER: Final = logging.getLogger(__package__)

PLATFORMS: Final = [Platform.CLIMATE, Platform.SENSOR, Platform.SWITCH]

# The polling interval of the MQTT bridge.
UPDATE_INTERVAL: Final = timedelta(seconds=120)

CONF_APARTMENT_ID: Final = "apartment_id"
CONF_PWHASH: Final = "pwhash"

# Device names and models match the MQTT bridge, so switching keeps the entity IDs.
MANUFACTURER: Final = "SBM-Verting"
APARTMENT_DEVICE_NAME: Final = "SBM Fizikus"
APARTMENT_MODEL: Final = "Fizikus"
THERMOSTAT_MODEL: Final = "Fizikus thermostat"

MIN_TEMPERATURE: Final = 5.0
MAX_TEMPERATURE: Final = 35.0
TEMPERATURE_STEP: Final = 0.5


def apartment_device_identifier(apartment_id: int) -> str:
    """Return the device registry identifier of the apartment."""
    return str(apartment_id)


def thermostat_device_identifier(apartment_id: int, thermostat_id: int) -> str:
    """Return the device registry identifier of a thermostat."""
    return f"{apartment_id}_{thermostat_id}"
