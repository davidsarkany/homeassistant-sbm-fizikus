"""Tests of the config flow of SBM Fizikus."""

from typing import Any
from unittest.mock import patch

import aiohttp
import pytest

from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntryState,
    ConfigFlowResult,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.sbm_fizikus.api import hash_password
from custom_components.sbm_fizikus.const import CONF_APARTMENT_ID, CONF_PWHASH, DOMAIN

from .conftest import (
    APARTMENT_ID,
    APARTMENT_NAME,
    PASSWORD,
    PWHASH,
    USERNAME,
    SbmStub,
)

USER_INPUT = {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
# The apartment of the fixtures, and a second one without a name.
SEVERAL_APARTMENTS = [[24, APARTMENT_ID, APARTMENT_NAME, True], [24, 703, None, True]]


def apartment_options(result: ConfigFlowResult) -> list[dict[str, str]]:
    """Return the apartments that the apartment step offers."""
    (selector,) = result["data_schema"].schema.values()
    return selector.config["options"]


async def start_user_flow(hass: HomeAssistant) -> ConfigFlowResult:
    """Start the user flow and enter the credentials."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)


async def test_user_flow(hass: HomeAssistant, sbm: SbmStub) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: f" {USERNAME} ", CONF_PASSWORD: PASSWORD}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == APARTMENT_NAME
    # The password itself is not stored, only its hash.
    assert result["data"] == {
        CONF_USERNAME: USERNAME,
        CONF_PWHASH: PWHASH,
        CONF_APARTMENT_ID: APARTMENT_ID,
    }
    assert result["result"].unique_id == str(APARTMENT_ID)
    assert result["result"].state is ConfigEntryState.LOADED
    assert sbm.payloads("get_jw_token")[0] == {
        "username_or_email": USERNAME,
        "pwhash": PWHASH,
    }


@pytest.mark.parametrize(
    ("operation", "answer", "error"),
    [
        ("get_jw_token", {"status": 401}, "invalid_auth"),
        ("get_jw_token", {"json_body": {"message": "Wrong password"}}, "invalid_auth"),
        ("get_jw_token", {"status": 500}, "cannot_connect"),
        ("get_jw_token", {"status": 404}, "cannot_connect"),
        ("get_jw_token", {"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        ("get_jw_token", {"exc": TimeoutError()}, "cannot_connect"),
        ("get_building_access_rights", {"text": "<html>"}, "cannot_connect"),
    ],
)
async def test_user_flow_error_and_recovery(
    hass: HomeAssistant,
    sbm: SbmStub,
    operation: str,
    answer: dict[str, Any],
    error: str,
) -> None:
    sbm.queue(operation, **answer)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    # The form keeps the username, not the password.
    suggested = {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key.description
    }
    assert suggested == {CONF_USERNAME: USERNAME}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_unexpected_error_and_recovery(
    hass: HomeAssistant, sbm: SbmStub
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(
        "custom_components.sbm_fizikus.config_flow.SbmClient.async_get_all_apartments",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    "operation", ["get_building_access_rights", "get_apartment_access_rights"]
)
async def test_user_flow_without_apartments(
    hass: HomeAssistant, sbm: SbmStub, operation: str
) -> None:
    sbm.queue(operation, json_body=[])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_apartments"


async def test_user_flow_apartment_without_name(
    hass: HomeAssistant, sbm: SbmStub
) -> None:
    sbm.queue("get_apartment_access_rights", json_body=[[24, APARTMENT_ID]])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SBM Fizikus"


async def test_user_flow_already_configured(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)

    result = await start_user_flow(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "all_apartments_configured"


async def test_user_flow_with_several_apartments(
    hass: HomeAssistant, sbm: SbmStub
) -> None:
    sbm.queue("get_apartment_access_rights", json_body=SEVERAL_APARTMENTS)

    result = await start_user_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "apartment"
    assert apartment_options(result) == [
        {"value": str(APARTMENT_ID), "label": APARTMENT_NAME},
        # Without a name, the apartment is shown with its id.
        {"value": "703", "label": "703"},
    ]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_APARTMENT_ID: "703"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SBM Fizikus"
    assert result["data"] == {
        CONF_USERNAME: USERNAME,
        CONF_PWHASH: PWHASH,
        CONF_APARTMENT_ID: 703,
    }
    assert result["result"].unique_id == "703"
    assert result["result"].state is ConfigEntryState.LOADED


async def test_user_flow_offers_only_apartments_that_are_not_set_up(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    """The only apartment that is not set up yet is set up without asking."""
    config_entry.add_to_hass(hass)
    sbm.queue("get_apartment_access_rights", json_body=SEVERAL_APARTMENTS)

    result = await start_user_flow(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_APARTMENT_ID] == 703


async def test_user_flow_apartment_set_up_in_the_meantime(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    sbm.queue("get_apartment_access_rights", json_body=SEVERAL_APARTMENTS)
    result = await start_user_flow(hass)
    # Another flow sets up the apartment while this one waits for the choice.
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_APARTMENT_ID: str(APARTMENT_ID)}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        data={**config_entry.data, CONF_PWHASH: hash_password("old password")},
    )

    result = await config_entry.start_reauth_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"][CONF_USERNAME] == USERNAME

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data == {
        CONF_USERNAME: USERNAME,
        CONF_PWHASH: PWHASH,
        CONF_APARTMENT_ID: APARTMENT_ID,
    }
    assert config_entry.state is ConfigEntryState.LOADED
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    # The flow checked the new password with the apartment of the entry.
    assert sbm.payloads("get_jw_token")[0]["pwhash"] == PWHASH
    assert sbm.payloads("get_apartment_data")[0]["apartment_id"] == APARTMENT_ID


@pytest.mark.parametrize(
    ("operation", "answer", "error"),
    [
        ("get_jw_token", {"status": 403}, "invalid_auth"),
        ("get_jw_token", {"status": 404}, "cannot_connect"),
        ("get_jw_token", {"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        ("get_apartment_data", {"status": 502}, "cannot_connect"),
    ],
)
async def test_reauth_flow_error_and_recovery(
    hass: HomeAssistant,
    sbm: SbmStub,
    config_entry: MockConfigEntry,
    operation: str,
    answer: dict[str, Any],
    error: str,
) -> None:
    config_entry.add_to_hass(hass)
    sbm.queue(operation, **answer)
    result = await config_entry.start_reauth_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_reauth_updates_the_other_apartments_of_the_account(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    old_pwhash = hash_password("old password")
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, data={**config_entry.data, CONF_PWHASH: old_pwhash}
    )
    other_apartment = MockConfigEntry(
        domain=DOMAIN,
        title="BP-TEST-001-102",
        unique_id="703",
        data={CONF_USERNAME: USERNAME, CONF_PWHASH: old_pwhash, CONF_APARTMENT_ID: 703},
    )
    other_account = MockConfigEntry(
        domain=DOMAIN,
        title="BP-TEST-002-201",
        unique_id="801",
        data={
            CONF_USERNAME: "other@example.com",
            CONF_PWHASH: old_pwhash,
            CONF_APARTMENT_ID: 801,
        },
    )
    other_apartment.add_to_hass(hass)
    other_account.add_to_hass(hass)
    # SBM refused the old password for the other apartment too.
    await other_apartment.start_reauth_flow(hass)

    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PWHASH] == PWHASH
    assert other_apartment.data[CONF_PWHASH] == PWHASH
    assert other_apartment.state is ConfigEntryState.LOADED
    # Another account keeps its own password.
    assert other_account.data[CONF_PWHASH] == old_pwhash
    # The reload closed the reauthentication of the other apartment.
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_reauth_flow_unexpected_error_and_recovery(
    hass: HomeAssistant, sbm: SbmStub, config_entry: MockConfigEntry
) -> None:
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    with patch(
        "custom_components.sbm_fizikus.config_flow.SbmClient.async_get_apartment_data",
        side_effect=RuntimeError("boom"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: PASSWORD}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
