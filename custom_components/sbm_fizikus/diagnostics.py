"""Diagnostics of the SBM Fizikus integration."""

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_PWHASH
from .coordinator import SbmFizikusConfigEntry

# The hash is as good as the password for SBM. The token is never stored, it is only
# listed in case it ever shows up. The names of the apartment and the thermostats
# stay: they help to match the entities.
TO_REDACT = {CONF_PWHASH, CONF_USERNAME, "jwt_token", "token"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SbmFizikusConfigEntry
) -> dict[str, Any]:
    """Return the config entry, the latest answer of SBM and the outdoor weather."""
    coordinator = entry.runtime_data.coordinator
    outdoor_coordinator = entry.runtime_data.outdoor_coordinator
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "apartment_data": async_redact_data(dict(coordinator.data.raw), TO_REDACT),
        # Only the parsed values: the answer of SBM has the address of the building.
        # The data is None while no outdoor sensor is enabled.
        "outdoor": {
            "last_update_success": outdoor_coordinator.last_update_success,
            "data": (
                None
                if outdoor_coordinator.data is None
                else asdict(outdoor_coordinator.data)
            ),
        },
    }
