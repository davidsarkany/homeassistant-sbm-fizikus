"""Climate entities of the SBM Fizikus thermostats."""

from typing import Any, override

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    PRECISION_TENTHS,
    Platform,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import (
    OPERATION_MODE_COOLING,
    OPERATION_MODE_HEATING,
    SbmAuthError,
    SbmError,
    SbmThermostat,
)
from .const import DOMAIN, LOGGER, MAX_TEMPERATURE, MIN_TEMPERATURE, TEMPERATURE_STEP
from .coordinator import SbmFizikusConfigEntry, SbmFizikusData
from .entity import SbmFizikusEntity, async_add_thermostat_entities

# Set temperature calls go to SBM one at a time.
PARALLEL_UPDATES = 1

_HVAC_MODES = {
    OPERATION_MODE_HEATING: HVACMode.HEAT,
    OPERATION_MODE_COOLING: HVACMode.COOL,
}
_HVAC_ACTIONS = {HVACMode.HEAT: HVACAction.HEATING, HVACMode.COOL: HVACAction.COOLING}
_HVAC_MODES_ON = [HVACMode.HEAT, HVACMode.COOL]
_HVAC_MODES_OFF = [HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SbmFizikusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a climate entity for each thermostat, also for new ones."""
    async_add_thermostat_entities(
        entry,
        async_add_entities,
        Platform.CLIMATE,
        lambda data, thermostat: [SbmFizikusClimate(data, thermostat)],
    )


class SbmFizikusClimate(SbmFizikusEntity, ClimateEntity):
    """A thermostat.

    The building heats or cools centrally, so the HVAC mode can only be read. It is
    off only while SBM reports the system as switched off; an unknown mode of SBM is
    an unknown state, never off.
    """

    # The main feature of the device, so it takes the device name, like the bridge.
    _attr_name = None
    _attr_max_temp = MAX_TEMPERATURE
    _attr_min_temp = MIN_TEMPERATURE
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_target_temperature_step = TEMPERATURE_STEP

    def __init__(self, data: SbmFizikusData, thermostat: SbmThermostat) -> None:
        """Initialize the climate entity."""
        super().__init__(data, thermostat, "climate")

    @property
    @override
    def hvac_modes(self) -> list[HVACMode]:
        """Return heat and cool, and off while the system is switched off."""
        return _HVAC_MODES_OFF if self.coordinator.data.system_off else _HVAC_MODES_ON

    @property
    @override
    def hvac_mode(self) -> HVACMode | None:
        """Return off, or the mode of the building: heat or cool, None when unknown."""
        data = self.coordinator.data
        if data.system_off:
            return HVACMode.OFF
        return _HVAC_MODES.get(data.operation_mode)

    @property
    @override
    def hvac_action(self) -> HVACAction | None:
        """Return whether the thermostat heats, cools, is idle, or is off."""
        if self.coordinator.data.system_off:
            return HVACAction.OFF
        if not self.thermostat.active:
            return HVACAction.IDLE
        if (hvac_mode := self.hvac_mode) is None:
            return None
        return _HVAC_ACTIONS[hvac_mode]

    @property
    @override
    def native_current_temperature(self) -> float | None:
        """Return the measured temperature."""
        return self.thermostat.temperature

    @property
    @override
    def current_humidity(self) -> float | None:
        """Return the measured humidity."""
        return self.thermostat.humidity

    @property
    @override
    def native_target_temperature(self) -> float | None:
        """Return the target temperature."""
        return self.thermostat.target_temperature

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Ignore a change of the mode, which is set centrally for the building.

        Like the bridge, another mode has no effect instead of an error, so a scene
        saved in the other season, or while the system was switched off, still
        sets its target temperature: a scene sets the mode first.
        """
        if hvac_mode != self.hvac_mode:
            LOGGER.debug(
                "Ignoring HVAC mode %s of %s: it is set centrally for the building",
                hvac_mode,
                self.entity_id,
            )

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature; SBM rounds it to 0.1 °C."""
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(hvac_mode)
        try:
            await self.coordinator.client.async_set_target_temperature(
                self._thermostat_id, kwargs[ATTR_TEMPERATURE]
            )
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_temperature"
            ) from err
        except SbmAuthError as err:
            self.coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except SbmError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="set_temperature_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        # SBM reports the new target temperature right away as pending, in
        # temperatureSetpointCommandDegC, until the thermostat confirms it.
        await self.coordinator.async_request_refresh()
