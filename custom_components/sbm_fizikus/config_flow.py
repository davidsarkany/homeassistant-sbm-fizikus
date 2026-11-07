"""Config flow of the SBM Fizikus integration."""

from collections.abc import Mapping
import logging
from typing import Any, override

import probatio

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import SbmApartment, SbmAuthError, SbmClient, SbmError, hash_password
from .const import APARTMENT_DEVICE_NAME, CONF_APARTMENT_ID, CONF_PWHASH, DOMAIN

_LOGGER = logging.getLogger(__name__)

_PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
# The reauthentication asks for the username too, which may have changed.
USER_SCHEMA = REAUTH_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")
        ),
        probatio.Required(probatio.Secret(CONF_PASSWORD)): _PASSWORD_SELECTOR,
    }
)


class SbmFizikusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up an apartment of an SBM account, with its thermostats.

    Every apartment is a config entry of its own. When the account has more than
    one apartment that is not set up yet, the flow asks which one to set up.
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._username = ""
        self._pwhash = ""
        # The apartments of the account that are not set up yet.
        self._apartments: dict[int, SbmApartment] = {}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the SBM username and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            # Only the hash is stored; SBM accepts it in place of the password.
            pwhash = hash_password(user_input[CONF_PASSWORD])
            client = SbmClient(async_get_clientsession(self.hass), username, pwhash)
            try:
                apartments = await client.async_get_all_apartments()
            except SbmAuthError:
                errors["base"] = "invalid_auth"
            except SbmError as err:
                _LOGGER.warning("Setting up SBM Fizikus failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error while setting up SBM Fizikus")
                errors["base"] = "unknown"
            else:
                if not apartments:
                    return self.async_abort(reason="no_apartments")
                configured = self._async_current_ids()
                self._apartments = {
                    apartment.apartment_id: apartment
                    for apartment in apartments
                    if str(apartment.apartment_id) not in configured
                }
                if not self._apartments:
                    return self.async_abort(reason="all_apartments_configured")
                self._username = username
                self._pwhash = pwhash
                if len(self._apartments) == 1:
                    return await self._async_create_apartment_entry(
                        next(iter(self._apartments.values()))
                    )
                return await self.async_step_apartment()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA,
                {CONF_USERNAME: user_input[CONF_USERNAME]} if user_input else None,
            ),
            errors=errors,
        )

    async def async_step_apartment(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which apartment of the account to set up."""
        if user_input is not None:
            return await self._async_create_apartment_entry(
                self._apartments[int(user_input[CONF_APARTMENT_ID])]
            )
        options = [
            SelectOptionDict(
                value=str(apartment_id), label=apartment.name or str(apartment_id)
            )
            for apartment_id, apartment in self._apartments.items()
        ]
        return self.async_show_form(
            step_id="apartment",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_APARTMENT_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )

    async def _async_create_apartment_entry(
        self, apartment: SbmApartment
    ) -> ConfigFlowResult:
        """Create the config entry of an apartment."""
        await self.async_set_unique_id(str(apartment.apartment_id))
        # Another flow may have set up the apartment in the meantime.
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=apartment.name or APARTMENT_DEVICE_NAME,
            data={
                CONF_USERNAME: self._username,
                CONF_PWHASH: self._pwhash,
                CONF_APARTMENT_ID: apartment.apartment_id,
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start the reauthentication after SBM refused the login."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password of the account again, and the username if it changed.

        The account must still have the apartment of the entry, so a changed
        username cannot move the entry to another account without it.
        """
        entry = self._get_reauth_entry()
        old_username = entry.data[CONF_USERNAME]
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            pwhash = hash_password(user_input[CONF_PASSWORD])
            client = SbmClient(async_get_clientsession(self.hass), username, pwhash)
            try:
                apartments = await client.async_get_all_apartments()
            except SbmAuthError:
                errors["base"] = "invalid_auth"
            except SbmError as err:
                _LOGGER.warning("Reauthenticating SBM Fizikus failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error while reauthenticating SBM Fizikus")
                errors["base"] = "unknown"
            else:
                apartment_ids = {apartment.apartment_id for apartment in apartments}
                if entry.data[CONF_APARTMENT_ID] not in apartment_ids:
                    errors["base"] = "wrong_account"
                else:
                    self._async_update_other_apartments(
                        entry, old_username, apartment_ids, username, pwhash
                    )
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={CONF_USERNAME: username, CONF_PWHASH: pwhash},
                    )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                REAUTH_SCHEMA,
                {
                    CONF_USERNAME: user_input[CONF_USERNAME]
                    if user_input
                    else old_username
                },
            ),
            description_placeholders={CONF_USERNAME: old_username},
            errors=errors,
        )

    @callback
    def _async_update_other_apartments(
        self,
        entry: ConfigEntry,
        old_username: str,
        apartment_ids: set[int],
        username: str,
        pwhash: str,
    ) -> None:
        """Give the new login to the other apartments of the account.

        They share the login, so they need the new password, and the new username,
        too. Only the apartments that the account still has get them. Reloading
        them also closes their own reauthentication flows.
        """
        for other in self._async_current_entries(include_ignore=False):
            if (
                other.entry_id != entry.entry_id
                and other.data.get(CONF_USERNAME) == old_username
                and other.data.get(CONF_APARTMENT_ID) in apartment_ids
                and (
                    other.data.get(CONF_USERNAME) != username
                    or other.data.get(CONF_PWHASH) != pwhash
                )
            ):
                self.hass.config_entries.async_update_entry(
                    other,
                    data={**other.data, CONF_USERNAME: username, CONF_PWHASH: pwhash},
                )
                self.hass.config_entries.async_schedule_reload(other.entry_id)
