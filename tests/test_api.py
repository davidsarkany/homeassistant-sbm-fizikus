"""Tests of the SBM API client."""

import asyncio
import dataclasses
import logging
from typing import Any

import aiohttp
from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker

from custom_components.sbm_fizikus.api import (
    SbmApartment,
    SbmApartmentData,
    SbmApiError,
    SbmAuthError,
    SbmBuilding,
    SbmClient,
    SbmConnectionError,
    SbmInvalidResponseError,
    SbmThermostat,
    hash_password,
)

from .conftest import (
    APARTMENT_ID,
    APARTMENT_NAME,
    LIVING_ROOM_ID,
    PASSWORD,
    PWHASH,
    SBM_URL,
    USERNAME,
    SbmStub,
    load_fixture_json,
)

# The request bodies that the tests of the bridge verify
# (SbmClientTests.AllOperations_RequestBodies_MatchSbmContract.verified.txt).
BRIDGE_REQUEST_BODIES = [
    b'{"operation":"get_jw_token","payload":{"username_or_email":"user@example.com",'
    b'"pwhash":"2bb80d537b1da3e38bd30361aa855686bde0eacd7162fef6a25fe97bf527a25b"}}',
    b'{"operation":"get_building_access_rights","payload":{"jwt_token":"jwt"}}',
    b'{"operation":"get_apartment_access_rights","payload":{"building_id":"24",'
    b'"jwt_token":"jwt"}}',
    b'{"operation":"get_apartment_data","payload":{"apartment_id":123,'
    b'"jwt_token":"jwt"}}',
    b'{"operation":"update_thermostat_settings","payload":{"thermostat_id":1001,'
    b'"temp_setpoint_degC":21.5,"jwt_token":"jwt"}}',
]
LOGIN = "get_jw_token"
DATA = "get_apartment_data"


def token(value: str, expiration: str = "2099-10-11 12:45:26+00:00") -> dict[str, str]:
    """Return an answer to a login."""
    return {"token": value, "expiration": expiration}


@pytest.fixture
def client(hass: HomeAssistant, sbm: SbmStub) -> SbmClient:
    """Return a client of the fake SBM API."""
    return SbmClient(async_get_clientsession(hass), USERNAME, PWHASH)


def test_hash_password() -> None:
    assert hash_password(PASSWORD) == PWHASH


async def test_request_bodies_match_the_bridge(
    client: SbmClient, sbm: SbmStub, aioclient_mock: AiohttpClientMocker
) -> None:
    sbm.queue(LOGIN, json_body=token("jwt"))

    await client.async_get_all_apartments()
    await client.async_get_apartment_data(APARTMENT_ID)
    await client.async_set_target_temperature(LIVING_ROOM_ID, 21.5)

    assert sbm.bodies == BRIDGE_REQUEST_BODIES
    for method, url, _, headers in aioclient_mock.mock_calls:
        assert method.upper() == "PUT"
        assert str(url) == SBM_URL
        assert headers == {"Content-Type": "application/json; charset=utf-8"}


async def test_debug_log_shows_the_calls_without_secrets(
    client: SbmClient, sbm: SbmStub, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.sbm_fizikus")
    sbm.queue(LOGIN, json_body=token("secret-jwt"))

    await client.async_get_all_apartments()
    await client.async_get_apartment_data(APARTMENT_ID)
    await client.async_set_target_temperature(LIVING_ROOM_ID, 21.5)
    await client.async_set_system_off(APARTMENT_ID, system_off=True)

    for secret in (USERNAME, PWHASH, "secret-jwt", "Teszt utca"):
        assert secret not in caplog.text
    assert f"SBM request get_apartment_data: {{'apartment_id': {APARTMENT_ID}" in (
        caplog.text
    )
    assert "SBM answer to get_apartment_data, status 200" in caplog.text
    assert "living-room" in caplog.text
    assert "'temp_setpoint_degC': 21.5" in caplog.text
    assert "'disabled': True" in caplog.text


async def test_debug_log_shows_why_a_call_failed(
    client: SbmClient, sbm: SbmStub, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.sbm_fizikus")
    sbm.queue(LOGIN, json_body=token("jwt"))
    sbm.queue(DATA, status=503, text="try later")

    with pytest.raises(SbmApiError):
        await client.async_get_apartment_data(APARTMENT_ID)

    assert "SBM call failed: SBM request get_apartment_data failed" in caplog.text
    assert "503 (Service Unavailable): try later" in caplog.text


async def test_debug_log_hides_the_answer_to_a_failed_login(
    client: SbmClient, sbm: SbmStub, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.sbm_fizikus")
    sbm.queue(LOGIN, status=500, text="secret-answer")

    with pytest.raises(SbmApiError):
        await client.async_get_apartment_data(APARTMENT_ID)

    assert "SBM call failed: SBM request get_jw_token failed" in caplog.text
    assert "secret-answer" not in caplog.text


@pytest.mark.parametrize(
    "operation", ["get_building_access_rights", "get_building_data"]
)
@pytest.mark.parametrize(
    ("status", "answer", "error_type"),
    [
        pytest.param(
            503,
            '{"error": "Failure at Teszt utca"}',
            SbmApiError,
            id="http-error",
        ),
        pytest.param(
            200,
            '{"address": "Teszt utca"',
            SbmInvalidResponseError,
            id="invalid-json",
        ),
    ],
)
async def test_building_errors_hide_the_address(
    client: SbmClient,
    sbm: SbmStub,
    caplog: pytest.LogCaptureFixture,
    operation: str,
    status: int,
    answer: str,
    error_type: type[SbmApiError | SbmInvalidResponseError],
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.sbm_fizikus")
    sbm.queue(operation, status=status, text=answer)
    request = {
        "get_building_access_rights": client.async_get_buildings,
        "get_building_data": lambda: client.async_get_building_off(24),
    }[operation]

    with pytest.raises(error_type) as err:
        await request()

    assert operation in str(err.value)
    assert str(err.value).endswith(": <redacted>")
    assert "Teszt utca" not in str(err.value)
    assert "Teszt utca" not in caplog.text
    assert f"SBM answer to {operation}, status {status}" in caplog.text
    assert "SBM call failed:" in caplog.text


@pytest.mark.parametrize("system_off", [True, False])
async def test_set_system_off_request_body(
    client: SbmClient, sbm: SbmStub, system_off: bool
) -> None:
    """The body is the one the SBM Fizikus app sends, with the id as a string."""
    sbm.queue(LOGIN, json_body=token("jwt"))

    await client.async_set_system_off(APARTMENT_ID, system_off=system_off)

    disabled = "true" if system_off else "false"
    assert sbm.bodies[-1] == (
        b'{"operation":"update_apartment_settings","payload":{"apartment_id":"123",'
        b'"disabled":' + disabled.encode() + b',"jwt_token":"jwt"}}'
    )


@pytest.mark.parametrize(
    "answer",
    [[], {"message": "ok"}, {"apartment_id": "703"}, {"apartment_id": None}],
)
async def test_invalid_answer_to_set_system_off(
    client: SbmClient, sbm: SbmStub, answer: Any
) -> None:
    sbm.queue("update_apartment_settings", json_body=answer)

    with pytest.raises(SbmInvalidResponseError):
        await client.async_set_system_off(APARTMENT_ID, system_off=True)


@pytest.mark.parametrize(
    "expiration",
    [
        "2026-11-03 13:00:00+00:00",
        "2026-11-03 14:00:00+01:00",
        # Without an offset, the time is in UTC.
        "2026-11-03 13:00:00",
    ],
)
async def test_token_is_reused_until_30_seconds_before_it_expires(
    client: SbmClient, sbm: SbmStub, freezer: FrozenDateTimeFactory, expiration: str
) -> None:
    freezer.move_to("2026-11-03 12:00:00+00:00")
    sbm.queue(LOGIN, json_body=token("first", expiration))
    sbm.queue(LOGIN, json_body=token("second", expiration))

    await client.async_get_apartment_data(APARTMENT_ID)
    freezer.move_to("2026-11-03 12:59:29+00:00")
    await client.async_get_apartment_data(APARTMENT_ID)
    freezer.move_to("2026-11-03 12:59:30+00:00")
    await client.async_get_apartment_data(APARTMENT_ID)

    assert sbm.operations == [LOGIN, DATA, DATA, LOGIN, DATA]
    assert [payload["jwt_token"] for payload in sbm.payloads(DATA)] == [
        "first",
        "first",
        "second",
    ]


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_token_logs_in_again(
    client: SbmClient, sbm: SbmStub, status: int
) -> None:
    sbm.queue(LOGIN, json_body=token("revoked"))
    sbm.queue(DATA, status=status)

    data = await client.async_get_apartment_data(APARTMENT_ID)

    assert sbm.operations == [LOGIN, DATA, LOGIN, DATA]
    assert sbm.payloads(DATA)[1]["jwt_token"] != "revoked"
    assert len(data.thermostats) == 4


async def test_token_rejected_after_a_new_login(
    client: SbmClient, sbm: SbmStub
) -> None:
    sbm.queue(DATA, status=403, text="Forbidden")
    sbm.queue(DATA, status=403, text="Forbidden")

    with pytest.raises(SbmApiError) as err:
        await client.async_get_apartment_data(APARTMENT_ID)

    assert err.value.status == 403
    assert err.value.is_authentication_failure
    assert sbm.operations == [LOGIN, DATA, LOGIN, DATA]


async def test_concurrent_calls_share_the_new_login(
    client: SbmClient, sbm: SbmStub
) -> None:
    """Two calls rejected with the same token log in again only once."""
    sbm.queue(LOGIN, json_body=token("revoked"))
    sbm.queue(LOGIN, json_body=token("new"))
    sbm.queue(DATA, status=401)
    sbm.queue(DATA, status=401)

    await asyncio.gather(
        client.async_get_apartment_data(APARTMENT_ID),
        client.async_get_apartment_data(APARTMENT_ID),
    )

    assert sbm.operations.count(LOGIN) == 2
    assert [payload["jwt_token"] for payload in sbm.payloads(DATA)] == [
        "revoked",
        "revoked",
        "new",
        "new",
    ]


async def test_server_error_is_not_retried(client: SbmClient, sbm: SbmStub) -> None:
    sbm.queue(DATA, status=500, text="Internal error")

    with pytest.raises(SbmApiError) as err:
        await client.async_get_apartment_data(APARTMENT_ID)

    assert err.value.status == 500
    assert not err.value.is_authentication_failure
    assert str(err.value) == (
        "SBM request get_apartment_data failed with status "
        "500 (Internal Server Error): Internal error"
    )
    assert sbm.operations == [LOGIN, DATA]


async def test_error_message_shortens_the_answer(
    client: SbmClient, sbm: SbmStub
) -> None:
    sbm.queue(DATA, status=599, text="x" * 500)

    with pytest.raises(SbmApiError) as err:
        await client.async_get_apartment_data(APARTMENT_ID)

    assert str(err.value) == (
        f"SBM request get_apartment_data failed with status 599: {'x' * 300}…"
    )


@pytest.mark.parametrize("error", ["invalid password", "user not found"])
async def test_login_refused_with_status_500(
    client: SbmClient, sbm: SbmStub, error: str
) -> None:
    """SBM refuses a login with status 500 and an error (live captures, 2026-11-07)."""
    sbm.queue(LOGIN, status=500, json_body={"error": error})

    with pytest.raises(SbmAuthError) as err:
        await client.async_get_apartment_data(APARTMENT_ID)

    assert str(err.value) == f"SBM refused the login with status 500: {error}"
    assert sbm.operations == [LOGIN]


@pytest.mark.parametrize(
    "json_body",
    [
        {"error": "Internal server error"},
        {"error": 5},
        {"message": "invalid password"},
        ["invalid password"],
    ],
)
async def test_other_login_error_is_not_a_refusal(
    client: SbmClient, sbm: SbmStub, json_body: Any
) -> None:
    """Only SBM's refusals mean wrong credentials, not its other failures."""
    sbm.queue(LOGIN, status=500, json_body=json_body)

    with pytest.raises(SbmApiError) as err:
        await client.async_get_apartment_data(APARTMENT_ID)

    assert not isinstance(err.value, SbmAuthError)
    assert err.value.status == 500


async def test_refusal_error_of_another_operation_is_not_a_refusal(
    client: SbmClient, sbm: SbmStub
) -> None:
    """Only the login can be refused; the call keeps its status."""
    sbm.queue(DATA, status=500, json_body={"error": "invalid password"})

    with pytest.raises(SbmApiError) as err:
        await client.async_get_apartment_data(APARTMENT_ID)

    assert not isinstance(err.value, SbmAuthError)
    assert err.value.sbm_error == "invalid password"
    assert sbm.operations == [LOGIN, DATA]


@pytest.mark.parametrize(
    "status", [400, 401, 403, 404, 405, 408, 409, 410, 415, 422, 429, 500, 503]
)
async def test_login_failure_that_is_not_a_refusal(
    client: SbmClient, sbm: SbmStub, status: int
) -> None:
    """SBM refuses a login only with status 500; a 401 or 403 may be the gateway's."""
    sbm.queue(LOGIN, status=status, text="details with a token")

    with pytest.raises(SbmApiError) as err:
        await client.async_get_apartment_data(APARTMENT_ID)

    assert not isinstance(err.value, SbmAuthError)
    assert err.value.status == status
    assert str(err.value).endswith(": <redacted>")
    assert sbm.operations == [LOGIN]


@pytest.mark.parametrize(
    "answer",
    [
        {"message": "Wrong username or password"},
        {"token": None, "expiration": "2099-10-11 12:45:26+00:00"},
        {"token": "", "expiration": "2099-10-11 12:45:26+00:00"},
        {"token": 5, "expiration": "2099-10-11 12:45:26+00:00"},
    ],
)
async def test_login_without_token_is_invalid(
    client: SbmClient, sbm: SbmStub, answer: dict[str, Any]
) -> None:
    sbm.queue(LOGIN, json_body=answer)

    with pytest.raises(SbmInvalidResponseError):
        await client.async_get_apartment_data(APARTMENT_ID)


@pytest.mark.parametrize(
    ("json_body", "text"),
    [
        (None, "token-like garbage"),
        ([], None),
        ({"token": "jwt"}, None),
        ({"token": "jwt", "expiration": "soon"}, None),
        ({"token": "jwt", "expiration": 5}, None),
    ],
)
async def test_invalid_login_answer(
    client: SbmClient, sbm: SbmStub, json_body: Any, text: str | None
) -> None:
    sbm.queue(LOGIN, json_body=json_body, text=text)

    with pytest.raises(SbmInvalidResponseError) as err:
        await client.async_get_apartment_data(APARTMENT_ID)

    # The answer to a login holds the token, so it is never shown.
    assert "garbage" not in str(err.value)
    assert "jwt" not in str(err.value)


@pytest.mark.parametrize(
    "exc", [aiohttp.ClientConnectionError("Connection refused"), TimeoutError()]
)
async def test_connection_error(
    client: SbmClient, sbm: SbmStub, exc: Exception
) -> None:
    sbm.queue(DATA, exc=exc)

    with pytest.raises(SbmConnectionError):
        await client.async_get_apartment_data(APARTMENT_ID)


async def test_undecodable_answer(client: SbmClient, sbm: SbmStub) -> None:
    sbm.queue(DATA, content=b"\xff\xfe\xfa")

    with pytest.raises(SbmInvalidResponseError):
        await client.async_get_apartment_data(APARTMENT_ID)


async def test_all_apartments(client: SbmClient, sbm: SbmStub) -> None:
    apartments = await client.async_get_all_apartments()

    assert apartments == [SbmApartment(apartment_id=APARTMENT_ID, name=APARTMENT_NAME)]
    assert sbm.payloads("get_apartment_access_rights")[0]["building_id"] == "24"


async def test_apartments_of_several_buildings(client: SbmClient, sbm: SbmStub) -> None:
    sbm.queue("get_building_access_rights", json_body=[[24], [25, "Magyarország"]])
    sbm.queue(
        "get_apartment_access_rights",
        json_body=[[24, 123, "BP-1", True], [24, 703, " ", True]],
    )
    # An apartment listed twice is returned once, with its first name.
    sbm.queue("get_apartment_access_rights", json_body=[[25, 801], [25, 123, "BP-X"]])

    apartments = await client.async_get_all_apartments()

    assert apartments == [
        SbmApartment(apartment_id=123, name="BP-1"),
        SbmApartment(apartment_id=703, name=None),
        SbmApartment(apartment_id=801, name=None),
    ]
    assert [
        payload["building_id"]
        for payload in sbm.payloads("get_apartment_access_rights")
    ] == ["24", "25"]


@pytest.mark.parametrize(
    ("operation", "answer"),
    [("get_building_access_rights", []), ("get_apartment_access_rights", [])],
)
async def test_no_apartments(
    client: SbmClient, sbm: SbmStub, operation: str, answer: list[Any]
) -> None:
    sbm.queue(operation, json_body=answer)

    assert await client.async_get_all_apartments() == []


@pytest.mark.parametrize(
    ("operation", "answer"),
    [
        ("get_building_access_rights", {"buildings": []}),
        ("get_building_access_rights", [24]),
        ("get_building_access_rights", [[]]),
        ("get_building_access_rights", [["24"]]),
        ("get_building_access_rights", [[True]]),
        ("get_building_access_rights", [[24.0]]),
        ("get_apartment_access_rights", [[24]]),
        ("get_apartment_access_rights", [[24, "123"]]),
        ("get_apartment_access_rights", [[24, 123], [24, None]]),
    ],
)
async def test_invalid_access_rights(
    client: SbmClient, sbm: SbmStub, operation: str, answer: Any
) -> None:
    sbm.queue(operation, json_body=answer)

    with pytest.raises(SbmInvalidResponseError):
        await client.async_get_all_apartments()


async def test_buildings(client: SbmClient, sbm: SbmStub) -> None:
    sbm.queue("get_building_access_rights", json_body=[[24, "", 0, "", "", 1, 2], [25]])

    buildings = await client.async_get_buildings()

    assert buildings == [
        SbmBuilding(building_id=24, outdoor_temperature=1.0, outdoor_humidity=2.0),
        SbmBuilding(building_id=25, outdoor_temperature=None, outdoor_humidity=None),
    ]
    assert await client.async_get_buildings() == [
        SbmBuilding(building_id=24, outdoor_temperature=19.9, outdoor_humidity=49.9)
    ]


@pytest.mark.parametrize("value", [None, "19.9", True, float("nan")])
async def test_building_values_that_are_invalid(
    client: SbmClient, sbm: SbmStub, value: Any
) -> None:
    sbm.queue(
        "get_building_access_rights", json_body=[[24, "", 0, "", "", value, value]]
    )

    assert await client.async_get_buildings() == [
        SbmBuilding(building_id=24, outdoor_temperature=None, outdoor_humidity=None)
    ]


@pytest.mark.parametrize(
    ("temperature", "humidity", "expected_temperature", "expected_humidity"),
    [
        # SBM does not know the outdoor weather.
        (-1, -1, None, None),
        (-1.0, -1.0, None, None),
        # -1 °C is a real temperature while the humidity is known.
        (-1, 80, -1.0, 80.0),
        # A humidity of -1 is never real.
        (5, -1, 5.0, None),
    ],
)
async def test_building_values_that_sbm_does_not_know(
    client: SbmClient,
    sbm: SbmStub,
    temperature: float,
    humidity: float,
    expected_temperature: float | None,
    expected_humidity: float | None,
) -> None:
    sbm.queue(
        "get_building_access_rights",
        json_body=[[24, "", 0, "", "", temperature, humidity]],
    )

    assert await client.async_get_buildings() == [
        SbmBuilding(
            building_id=24,
            outdoor_temperature=expected_temperature,
            outdoor_humidity=expected_humidity,
        )
    ]


async def test_apartments(client: SbmClient, sbm: SbmStub) -> None:
    apartments = await client.async_get_apartments(24)

    assert apartments == [SbmApartment(apartment_id=APARTMENT_ID, name=APARTMENT_NAME)]
    assert sbm.payloads("get_apartment_access_rights")[0]["building_id"] == "24"


@pytest.mark.parametrize("answer", [{"rows": []}, [["24"]]])
async def test_invalid_buildings_and_apartments(
    client: SbmClient, sbm: SbmStub, answer: Any
) -> None:
    sbm.queue("get_building_access_rights", json_body=answer)
    sbm.queue("get_apartment_access_rights", json_body=answer)

    with pytest.raises(SbmInvalidResponseError):
        await client.async_get_buildings()
    with pytest.raises(SbmInvalidResponseError):
        await client.async_get_apartments(24)


async def test_apartment_data(client: SbmClient) -> None:
    data = await client.async_get_apartment_data(APARTMENT_ID)

    assert data.operation_mode == 0
    assert data.connected
    assert list(data.thermostats) == [1001, 1002, 1003, 1004]
    assert data.thermostats[LIVING_ROOM_ID] == SbmThermostat(
        thermostat_id=LIVING_ROOM_ID,
        name="living-room",
        active=False,
        temperature=24.8,
        humidity=60,
        dew_point=15.9,
        target_temperature=23.0,
        child_lock=False,
        condensation_risk_level=0,
    )
    assert data.firmware_version == "1.2.11-legacy"
    assert not data.system_off
    assert data.raw == load_fixture_json("get_apartment_data.json")


@pytest.mark.parametrize("firmware", [None, "", " ", 5])
async def test_firmware_version_that_is_missing_or_invalid(
    client: SbmClient, sbm: SbmStub, firmware: Any
) -> None:
    sbm.apartment_data["fwVer"] = firmware

    data = await client.async_get_apartment_data(APARTMENT_ID)

    assert data.firmware_version is None


@pytest.mark.parametrize(
    ("disabled", "thermostats", "system_off"),
    [
        (True, True, True),
        (True, False, True),
        (False, True, False),
        (None, True, False),
        ("yes", True, False),
    ],
)
async def test_switched_off_system(
    client: SbmClient,
    sbm: SbmStub,
    disabled: Any,
    thermostats: bool,
    system_off: bool,
) -> None:
    sbm.apartment_data["disabled"] = disabled
    # A switched-off system does not report its relay module.
    sbm.apartment_data["communicationActiveRelayModule"] = False
    sbm.apartment_data["communicationActiveThermostats"] = thermostats

    data = await client.async_get_apartment_data(APARTMENT_ID)

    assert data.system_off is system_off
    assert data.connected is thermostats


@pytest.mark.parametrize(
    ("disabled", "command", "system_off"),
    [
        # Switching off, not confirmed yet.
        (False, True, True),
        # Switching on, not confirmed yet.
        (True, False, False),
        (False, False, False),
        # No pending switching, or a value that is not one.
        (True, None, True),
        (False, "yes", False),
    ],
)
async def test_pending_switching(
    client: SbmClient,
    sbm: SbmStub,
    disabled: bool,
    command: Any,
    system_off: bool,
) -> None:
    """A pending switching is shown, like the app."""
    sbm.apartment_data["disabled"] = disabled
    sbm.apartment_data["disabledCommand"] = command

    data = await client.async_get_apartment_data(APARTMENT_ID)

    assert data.system_off is system_off


@pytest.mark.parametrize(
    ("setpoint", "command", "expected"),
    [
        # Live capture of 2026-11-04: the new value is pending for 1 to 2 minutes.
        (19.0, 19.1, 19.1),
        (19.1, None, 19.1),
        (None, 21, 21.0),
        (22.0, "21", 22.0),
        (22.0, float("nan"), 22.0),
    ],
)
async def test_pending_target_temperature(
    client: SbmClient,
    sbm: SbmStub,
    setpoint: Any,
    command: Any,
    expected: float,
) -> None:
    thermostat = sbm.apartment_data["thermostats"][0]
    thermostat["temperatureSetpointDegC"] = setpoint
    thermostat["temperatureSetpointCommandDegC"] = command

    data = await client.async_get_apartment_data(APARTMENT_ID)

    assert data.thermostats[LIVING_ROOM_ID].target_temperature == expected


@pytest.mark.parametrize(
    ("humidity", "expected"),
    [
        # Live capture of 2026-11-04: SBM sends integers.
        (62, 62),
        (0, 0),
        (100, 100),
        # A float, should SBM ever send one, is rounded half up.
        (60.0, 60),
        (59.4, 59),
        (59.5, 60),
    ],
)
async def test_humidity_is_a_whole_number(
    client: SbmClient, sbm: SbmStub, humidity: float, expected: int
) -> None:
    """The thermostats measure whole percents, so the humidity stays an integer."""
    sbm.apartment_data["thermostats"][0]["measuredHumPerc"] = humidity

    data = await client.async_get_apartment_data(APARTMENT_ID)

    value = data.thermostats[LIVING_ROOM_ID].humidity
    assert value == expected
    assert type(value) is int


@pytest.mark.parametrize(
    ("dew_point", "expected"),
    [
        # Live capture of 2026-11-07: SBM sends the dew point with full precision.
        (13.04477696992492, 13.0),
        (15.934023536720074, 15.9),
        (12.25, 12.3),
        (-0.05, -0.1),
        (14, 14.0),
    ],
)
async def test_dew_point_is_rounded_to_tenths(
    client: SbmClient, sbm: SbmStub, dew_point: float, expected: float
) -> None:
    sbm.apartment_data["thermostats"][0]["dewPointDegC"] = dew_point

    data = await client.async_get_apartment_data(APARTMENT_ID)

    value = data.thermostats[LIVING_ROOM_ID].dew_point
    assert value == expected
    assert type(value) is float


@pytest.mark.parametrize("relay_module", [True, False, None])
async def test_apartment_data_connection_ignores_the_relay_module(
    client: SbmClient, sbm: SbmStub, relay_module: Any
) -> None:
    """Like the app, only the flag of the thermostats counts as the connection."""
    sbm.apartment_data["communicationActiveRelayModule"] = relay_module

    data = await client.async_get_apartment_data(APARTMENT_ID)
    assert data.connected

    sbm.apartment_data["communicationActiveThermostats"] = False

    data = await client.async_get_apartment_data(APARTMENT_ID)
    assert not data.connected


async def test_thermostat_values_that_are_missing_or_invalid(
    client: SbmClient, sbm: SbmStub
) -> None:
    """Missing or invalid values are unknown, and do not fail the other values."""
    sbm.queue(
        DATA,
        text=(
            '{"operationMode": 1,'
            ' "communicationActiveThermostats": true, "thermostats": ['
            '{"ID": 1},'
            '{"ID": 2, "name": " ", "active": null, "measuredTempDegC": NaN,'
            ' "measuredHumPerc": "55", "dewPointDegC": true,'
            ' "temperatureSetpointDegC": 22},'
            '{"ID": 3, "name": 5, "active": "yes", "measuredTempDegC": Infinity}'
            "]}"
        ),
    )

    data = await client.async_get_apartment_data(APARTMENT_ID)

    unknown = {
        "name": None,
        "active": False,
        "temperature": None,
        "humidity": None,
        "dew_point": None,
        "target_temperature": None,
        "child_lock": False,
        "condensation_risk_level": None,
    }
    assert data.thermostats == {
        1: SbmThermostat(thermostat_id=1, **unknown),
        2: SbmThermostat(thermostat_id=2, **{**unknown, "target_temperature": 22.0}),
        3: SbmThermostat(thermostat_id=3, **unknown),
    }


@pytest.mark.parametrize(
    "change",
    [
        {"operationMode": None},
        {"operationMode": "0"},
        {"operationMode": False},
        {"communicationActiveThermostats": None},
        {"thermostats": None},
        {"thermostats": {"1001": {}}},
        {"thermostats": [5]},
        {"thermostats": [{"name": "living-room"}]},
        {"thermostats": [{"ID": "1001"}]},
    ],
)
async def test_invalid_apartment_data(
    client: SbmClient, sbm: SbmStub, change: dict[str, Any]
) -> None:
    sbm.apartment_data.update(change)

    with pytest.raises(SbmInvalidResponseError):
        await client.async_get_apartment_data(APARTMENT_ID)


async def test_apartment_data_that_is_not_an_object(
    client: SbmClient, sbm: SbmStub
) -> None:
    sbm.queue(DATA, json_body=[])

    with pytest.raises(SbmInvalidResponseError) as err:
        await client.async_get_apartment_data(APARTMENT_ID)

    assert str(err.value) == "SBM returned an invalid response to get_apartment_data"


@pytest.mark.parametrize(
    ("temperature", "sent"),
    [
        (21.5, "21.5"),
        (21.94, "21.9"),
        (21.96, "22"),
        (22.25, "22.3"),
        (22.0, "22"),
        (23, "23"),
    ],
)
async def test_set_target_temperature_rounds_to_tenths(
    client: SbmClient, sbm: SbmStub, temperature: float, sent: str
) -> None:
    await client.async_set_target_temperature(LIVING_ROOM_ID, temperature)

    assert f'"temp_setpoint_degC":{sent},'.encode() in sbm.bodies[-1]


@pytest.mark.parametrize("temperature", [float("nan"), float("inf"), float("-inf")])
async def test_non_finite_target_temperature_is_rejected_before_login(
    client: SbmClient, sbm: SbmStub, temperature: float
) -> None:
    with pytest.raises(ValueError, match="Target temperature must be a finite number"):
        await client.async_set_target_temperature(LIVING_ROOM_ID, temperature)

    assert sbm.operations == []


@pytest.mark.parametrize("temperature", [float("nan"), float("inf"), float("-inf")])
async def test_request_rejects_non_finite_json_values(
    client: SbmClient, sbm: SbmStub, temperature: float
) -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        await client._async_request(
            "update_thermostat_settings", {"temp_setpoint_degC": temperature}
        )

    assert sbm.operations == []


@pytest.mark.parametrize(
    "answer",
    [
        [],
        {},
        {"thermostat_id": "1001"},
        {"thermostat_id": 1002},
        {"thermostat_id": True},
    ],
)
async def test_invalid_answer_to_set_target_temperature(
    client: SbmClient, sbm: SbmStub, answer: Any
) -> None:
    sbm.queue("update_thermostat_settings", json_body=answer)

    with pytest.raises(SbmInvalidResponseError):
        await client.async_set_target_temperature(LIVING_ROOM_ID, 21.5)


def test_apartment_data_equality_ignores_the_raw_answer() -> None:
    """The coordinator only notifies entities when the parsed data changed."""
    first = SbmApartmentData(
        operation_mode=0,
        connected=True,
        system_off=False,
        thermostats={},
        firmware_version=None,
        forward_water_temperature=None,
        return_water_temperature=None,
        flow_rate=None,
        raw={"lastStateUpdate": "2026-10-10 12:45:11.578277"},
    )
    second = SbmApartmentData(
        operation_mode=0,
        connected=True,
        system_off=False,
        thermostats={},
        firmware_version=None,
        forward_water_temperature=None,
        return_water_temperature=None,
        flow_rate=None,
        raw={"lastStateUpdate": "2026-10-10 12:47:11.578277"},
    )

    assert first == second


@pytest.mark.parametrize(
    ("lock", "command", "expected"),
    [
        # Live capture of 2026-11-07: a lock that was never set is null.
        (None, None, False),
        (True, None, True),
        (False, None, False),
        # A change that the thermostat has not confirmed yet.
        (False, True, True),
        (True, False, False),
        (None, "yes", False),
    ],
)
async def test_child_lock(
    client: SbmClient, sbm: SbmStub, lock: Any, command: Any, expected: bool
) -> None:
    thermostat = sbm.apartment_data["thermostats"][0]
    thermostat["childLock"] = lock
    thermostat["childLockCommand"] = command

    data = await client.async_get_apartment_data(APARTMENT_ID)

    assert data.thermostats[LIVING_ROOM_ID].child_lock is expected


@pytest.mark.parametrize("locked", [True, False])
async def test_set_child_lock_request_body(
    client: SbmClient, sbm: SbmStub, locked: bool
) -> None:
    """The body is the one the room card of the app sends."""
    sbm.queue(LOGIN, json_body=token("jwt"))

    await client.async_set_child_lock(LIVING_ROOM_ID, locked=locked)

    value = "true" if locked else "false"
    assert sbm.bodies[-1] == (
        b'{"operation":"update_thermostat_settings","payload":{"thermostat_id":1001,'
        b'"child_lock":' + value.encode() + b',"jwt_token":"jwt"}}'
    )


@pytest.mark.parametrize("answer", [{}, {"thermostat_id": 1002}])
async def test_invalid_answer_to_set_child_lock(
    client: SbmClient, sbm: SbmStub, answer: Any
) -> None:
    sbm.queue("update_thermostat_settings", json_body=answer)

    with pytest.raises(SbmInvalidResponseError):
        await client.async_set_child_lock(LIVING_ROOM_ID, locked=True)


async def test_building_off(client: SbmClient, sbm: SbmStub) -> None:
    sbm.queue(LOGIN, json_body=token("jwt"))
    sbm.queue("get_building_data", json_body={"data": {"disabled": True}})

    assert await client.async_get_building_off(24)
    assert not await client.async_get_building_off(24)
    # The building id is an integer here, like the app sends it.
    assert sbm.bodies[-1] == (
        b'{"operation":"get_building_data","payload":{"building_id":24,'
        b'"jwt_token":"jwt"}}'
    )


@pytest.mark.parametrize(
    "answer",
    [[], {}, {"data": None}, {"data": {}}, {"data": {"disabled": "true"}}],
)
async def test_invalid_building_data(
    client: SbmClient, sbm: SbmStub, answer: Any
) -> None:
    sbm.queue("get_building_data", json_body=answer)

    with pytest.raises(SbmInvalidResponseError):
        await client.async_get_building_off(24)


def test_switched_off_by_the_apartment_or_the_building() -> None:
    data = SbmApartmentData(
        operation_mode=0,
        connected=True,
        system_off=False,
        thermostats={},
        firmware_version=None,
        forward_water_temperature=None,
        return_water_temperature=None,
        flow_rate=None,
        raw={},
    )

    assert not data.switched_off
    assert dataclasses.replace(data, system_off=True).switched_off
    assert dataclasses.replace(data, building_off=True).switched_off
