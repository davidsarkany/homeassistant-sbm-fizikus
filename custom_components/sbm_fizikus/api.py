"""Client of the SBM Fizikus cloud API.

It mirrors SbmClient and SbmSession of the sbm-fizikus-to-mqtt bridge and does not
depend on Home Assistant. Error messages never contain a request payload, which holds
the token, or the answer to a login, which holds the token too. The debug log shows the
requests without the credentials and the token, and the answers except the ones in
_ANSWERS_NOT_LOGGED.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
import hashlib
from http import HTTPStatus
import json
import logging
import math
import time
from typing import Any, Final, TypeIs

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL: Final = "https://ps9et7hsp3.execute-api.eu-central-1.amazonaws.com"

OPERATION_MODE_HEATING: Final = 0
OPERATION_MODE_COOLING: Final = 1

_REQUEST_PATH: Final = "/frontend"
_REQUEST_HEADERS: Final = {"Content-Type": "application/json; charset=utf-8"}
_REQUEST_TIMEOUT: Final = aiohttp.ClientTimeout(total=30)
_TOKEN_REFRESH_MARGIN: Final = timedelta(seconds=30)
_MAX_ERROR_BODY_LENGTH: Final = 300
_REDACTED: Final = "<redacted>"
_SECRET_PAYLOAD_KEYS: Final = frozenset({"jwt_token", "pwhash", "username_or_email"})
# The answer to a login holds the token, the list of buildings holds their addresses.
_ANSWERS_NOT_LOGGED: Final = frozenset({"get_jw_token", "get_building_access_rights"})
# The `error` of SBM's answer to a refused login, which comes with status 500 (live
# captures, 2026-11-07). Any other error of a login does not prove the credentials
# are wrong, as SBM also answers its own failures with status 500.
_LOGIN_REFUSALS: Final = frozenset({"invalid password", "user not found"})
# What SBM reports for an outdoor value it does not know.
_UNKNOWN_OUTDOOR_VALUE: Final = -1.0


class SbmError(Exception):
    """An SBM API call failed or returned data the integration cannot use."""


class SbmConnectionError(SbmError):
    """SBM could not be reached or did not answer in time."""


class SbmApiError(SbmError):
    """SBM answered with a status code that is not a success."""

    def __init__(
        self, status: int, message: str, *, sbm_error: str | None = None
    ) -> None:
        """Initialize the error with the HTTP status code and SBM's `error`."""
        super().__init__(message)
        self.status = status
        # The `error` of the answer, when it is a JSON object with a string `error`.
        self.sbm_error = sbm_error

    @property
    def is_authentication_failure(self) -> bool:
        """Return whether SBM rejected the token, so a new login may help."""
        return self.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)


class SbmInvalidResponseError(SbmError):
    """SBM answered with data that is not valid or misses required values."""


class SbmAuthError(SbmError):
    """SBM refused the login: the username or the password is wrong."""


def hash_password(password: str) -> str:
    """Return what SBM expects: the lowercase hex SHA-256 of the UTF-8 password."""
    return hashlib.sha256(password.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SbmApartment:
    """The apartment of an account, from its access rights."""

    apartment_id: int
    name: str | None


@dataclass(frozen=True, slots=True)
class SbmBuilding:
    """A building of an account, from its access rights.

    SBM reports the outdoor weather per building. Values that it does not report are
    None.
    """

    building_id: int
    outdoor_temperature: float | None
    outdoor_humidity: float | None


@dataclass(frozen=True, slots=True)
class SbmThermostat:
    """The state of a thermostat. Values that SBM does not report are None."""

    thermostat_id: int
    name: str | None
    # The thermostat calls for heating or cooling.
    active: bool
    temperature: float | None
    # The thermostats measure whole percents, and SBM sends them as integers.
    humidity: int | None
    dew_point: float | None
    target_temperature: float | None


@dataclass(frozen=True, slots=True)
class SbmApartmentData:
    """The state of an apartment and its thermostats."""

    operation_mode: int
    # SBM reaches the thermostats, the connection indicator of the app.
    connected: bool
    # The system is switched off in SBM, or a switching off that the device has not
    # confirmed yet is pending (`disabledCommand`, else `disabled`), like the app
    # shows it.
    system_off: bool
    thermostats: Mapping[int, SbmThermostat]
    # The firmware of the SBM Fizikus device of the apartment, None when not reported.
    firmware_version: str | None
    # The decoded answer of SBM, for diagnostics.
    raw: Mapping[str, Any] = field(compare=False, repr=False)


class SbmClient:
    """Call the SBM API with a cached token.

    Before a call the client logs in when the token expires within 30 seconds. When
    SBM rejects the token of a call (401 or 403), it logs in once more and retries
    the call once.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        pwhash: str,
        *,
        base_url: str = BASE_URL,
    ) -> None:
        """Initialize the client with the hash of the password."""
        self._session = session
        self._username = username
        self._pwhash = pwhash
        self._url = f"{base_url}{_REQUEST_PATH}"
        self._login_lock = asyncio.Lock()
        self._token: str | None = None
        self._token_expiration = datetime.min.replace(tzinfo=UTC)

    async def async_get_all_apartments(self) -> list[SbmApartment]:
        """Return the apartments of the account, from all of its buildings."""
        return await self._async_call(self._async_get_all_apartments)

    async def async_get_buildings(self) -> list[SbmBuilding]:
        """Return the buildings of the account, with their outdoor weather."""
        return await self._async_call(self._async_get_buildings)

    async def async_get_apartments(self, building_id: int) -> list[SbmApartment]:
        """Return the apartments of the account in a building."""
        return await self._async_call(
            lambda token: self._async_get_apartments(building_id, token)
        )

    async def async_get_apartment_data(self, apartment_id: int) -> SbmApartmentData:
        """Return the state of an apartment and its thermostats."""
        return await self._async_call(
            lambda token: self._async_get_apartment_data(apartment_id, token)
        )

    async def async_set_target_temperature(
        self, thermostat_id: int, temperature: float
    ) -> None:
        """Set the target temperature of a thermostat, rounded to 0.1 °C."""
        setpoint = _round_setpoint(temperature)
        await self._async_call(
            lambda token: self._async_update_thermostat_settings(
                thermostat_id, setpoint, token
            )
        )

    async def async_set_system_off(
        self, apartment_id: int, *, system_off: bool
    ) -> None:
        """Switch the system of an apartment off or on, like the app."""
        await self._async_call(
            lambda token: self._async_update_apartment_settings(
                apartment_id, token, system_off=system_off
            )
        )

    async def _async_call[T](self, operation: Callable[[str], Awaitable[T]]) -> T:
        """Run an operation, and log why it failed.

        The coordinators log a failing poll only once, and the config flow shows only
        the kind of the error, so this is where the debug log gets every cause.
        """
        try:
            return await self._async_call_with_token(operation)
        except SbmError as err:
            _LOGGER.debug("SBM call failed: %s", err)
            raise

    async def _async_call_with_token[T](
        self, operation: Callable[[str], Awaitable[T]]
    ) -> T:
        """Run an operation with a token, and once more after a rejected token."""
        token = await self._async_get_token()
        try:
            return await operation(token)
        except SbmApiError as err:
            if not err.is_authentication_failure:
                raise
            _LOGGER.debug("SBM rejected the token (%s), logging in again", err.status)
            # Another call may already have replaced the rejected token.
            if self._token == token:
                self._token = None
            return await operation(await self._async_get_token())

    async def _async_get_token(self) -> str:
        """Return the cached token, after logging in when it is about to expire."""
        async with self._login_lock:
            token = self._token
            if (
                token is None
                or self._token_expiration <= datetime.now(UTC) + _TOKEN_REFRESH_MARGIN
            ):
                token, self._token_expiration = await self._async_login()
                self._token = token
            return token

    async def _async_login(self) -> tuple[str, datetime]:
        """Log in and return the token and when it expires."""
        payload = {"username_or_email": self._username, "pwhash": self._pwhash}
        try:
            response = await self._async_request(
                "get_jw_token", payload, redact_response=True
            )
        except SbmApiError as err:
            # A route, request or server error does not prove the credentials are
            # wrong, only a rejection or one of SBM's refusals does.
            if err.sbm_error in _LOGIN_REFUSALS:
                raise SbmAuthError(
                    f"SBM refused the login with status {err.status}: {err.sbm_error}"
                ) from err
            if err.is_authentication_failure:
                raise SbmAuthError(
                    f"SBM refused the login with status {err.status}"
                ) from err
            raise
        if not isinstance(response, dict):
            raise SbmInvalidResponseError(
                "SBM returned an invalid response to get_jw_token"
            )
        token = response.get("token")
        if not isinstance(token, str) or not token:
            # SBM answered, but did not let the account in.
            raise SbmAuthError("SBM did not return a token for the login")
        expiration = _parse_timestamp(response.get("expiration"))
        if expiration is None:
            raise SbmInvalidResponseError("SBM returned no valid token expiration")
        _LOGGER.debug("Logged in to SBM, the token is valid until %s", expiration)
        return token, expiration

    async def _async_get_all_apartments(self, token: str) -> list[SbmApartment]:
        """Return the apartments of the account, in the order of SBM."""
        apartments: dict[int, SbmApartment] = {}
        for building in await self._async_get_buildings(token):
            for apartment in await self._async_get_apartments(
                building.building_id, token
            ):
                # An apartment listed twice is offered once.
                apartments.setdefault(apartment.apartment_id, apartment)
        return list(apartments.values())

    async def _async_get_buildings(self, token: str) -> list[SbmBuilding]:
        """Return the buildings of the account, with their outdoor weather."""
        # Rows of [buildingId, country, postalCode, city, street, outdoorTemp,
        # outdoorHum, name].
        rows = _parse_rows(
            await self._async_request(
                "get_building_access_rights", {"jwt_token": token}
            ),
            "get_building_access_rights",
            id_index=0,
        )
        return [_parse_building(row) for row in rows]

    async def _async_get_apartments(
        self, building_id: int, token: str
    ) -> list[SbmApartment]:
        """Return the apartments of the account in a building."""
        # Rows of [buildingId, apartmentId, name, isOnline].
        rows = _parse_rows(
            await self._async_request(
                "get_apartment_access_rights",
                # SBM expects the building id as a string here.
                {"building_id": str(building_id), "jwt_token": token},
            ),
            "get_apartment_access_rights",
            id_index=1,
        )
        return [
            SbmApartment(apartment_id=row[1], name=_optional_str(row, 2))
            for row in rows
        ]

    async def _async_get_apartment_data(
        self, apartment_id: int, token: str
    ) -> SbmApartmentData:
        """Return the state of an apartment and its thermostats."""
        return _parse_apartment_data(
            await self._async_request(
                "get_apartment_data", {"apartment_id": apartment_id, "jwt_token": token}
            )
        )

    async def _async_update_thermostat_settings(
        self, thermostat_id: int, setpoint: float, token: str
    ) -> None:
        """Send a new target temperature."""
        response = await self._async_request(
            "update_thermostat_settings",
            {
                "thermostat_id": thermostat_id,
                "temp_setpoint_degC": setpoint,
                "jwt_token": token,
            },
        )
        if not isinstance(response, dict) or not _is_int(response.get("thermostat_id")):
            raise SbmInvalidResponseError(
                "SBM returned an invalid response to update_thermostat_settings"
            )

    async def _async_update_apartment_settings(
        self, apartment_id: int, token: str, *, system_off: bool
    ) -> None:
        """Send whether the system of the apartment is switched off."""
        response = await self._async_request(
            "update_apartment_settings",
            # SBM expects the apartment id as a string here, like the app sends it.
            {
                "apartment_id": str(apartment_id),
                "disabled": system_off,
                "jwt_token": token,
            },
        )
        # The answer has the id as a string too.
        if not isinstance(response, dict) or str(response.get("apartment_id")) != str(
            apartment_id
        ):
            raise SbmInvalidResponseError(
                "SBM returned an invalid response to update_apartment_settings"
            )

    async def _async_request(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        redact_response: bool = False,
    ) -> Any:
        """Send an operation and return the decoded JSON answer."""
        # Compact JSON in the key order of the bridge, so the bodies are the same.
        body = json.dumps(
            {"operation": operation, "payload": payload},
            separators=(",", ":"),
            allow_nan=False,
        )
        _LOGGER.debug("SBM request %s: %s", operation, _loggable_payload(payload))
        started = time.monotonic()
        try:
            async with self._session.put(
                self._url,
                data=body.encode(),
                headers=_REQUEST_HEADERS,
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                status = response.status
                text = await response.text()
        except TimeoutError as err:
            raise SbmConnectionError(f"SBM did not answer {operation} in time") from err
        except aiohttp.ClientError as err:
            raise SbmConnectionError(f"SBM request {operation} failed: {err}") from err
        except UnicodeDecodeError as err:
            raise SbmInvalidResponseError(
                f"SBM returned an undecodable response to {operation}"
            ) from err

        _LOGGER.debug(
            "SBM answer to %s, status %s in %.3f s: %s",
            operation,
            status,
            time.monotonic() - started,
            _REDACTED if operation in _ANSWERS_NOT_LOGGED else text,
        )
        shown = _REDACTED if redact_response else _truncate(text)
        if not HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES:
            raise SbmApiError(
                status,
                f"SBM request {operation} failed with status "
                f"{_describe_status(status)}: {shown}",
                sbm_error=_error_field(text),
            )
        try:
            return json.loads(text)
        except ValueError as err:
            raise SbmInvalidResponseError(
                f"SBM returned an invalid response to {operation}: {shown}"
            ) from err


def _error_field(text: str) -> str | None:
    """Return the `error` of an answer such as {"error": "invalid password"}."""
    try:
        answer = json.loads(text)
    except ValueError:
        return None
    if isinstance(answer, dict) and isinstance(error := answer.get("error"), str):
        return error
    return None


def _parse_rows(response: Any, operation: str, *, id_index: int) -> list[list[Any]]:
    """Validate an answer of positional rows, each with an integer id."""
    if not isinstance(response, list) or not all(
        isinstance(row, list) and len(row) > id_index and _is_int(row[id_index])
        for row in response
    ):
        raise SbmInvalidResponseError(
            f"SBM returned an invalid response to {operation}"
        )
    return response


def _parse_building(row: list[Any]) -> SbmBuilding:
    """Convert a row of get_building_access_rights.

    SBM reports an outdoor value it does not know as -1. A humidity of -1 is always
    unknown. A temperature of -1 is a real winter value too, so it is unknown only
    when the humidity is -1 as well.
    """
    temperature = _row_float(row, 5)
    humidity = _row_float(row, 6)
    if humidity == _UNKNOWN_OUTDOOR_VALUE:
        humidity = None
        if temperature == _UNKNOWN_OUTDOOR_VALUE:
            temperature = None
    return SbmBuilding(
        building_id=row[0],
        outdoor_temperature=temperature,
        outdoor_humidity=humidity,
    )


def _parse_apartment_data(response: Any) -> SbmApartmentData:
    """Validate and convert the answer of get_apartment_data."""
    if not isinstance(response, dict):
        raise SbmInvalidResponseError(
            "SBM returned an invalid response to get_apartment_data"
        )
    operation_mode = response.get("operationMode")
    connected = response.get("communicationActiveThermostats")
    thermostats = response.get("thermostats")
    if not (
        _is_int(operation_mode)
        and isinstance(connected, bool)
        and isinstance(thermostats, list)
        and all(
            isinstance(thermostat, dict) and _is_int(thermostat.get("ID"))
            for thermostat in thermostats
        )
    ):
        raise SbmInvalidResponseError(
            "SBM returned an invalid response to get_apartment_data"
        )
    # A switching that the device has not confirmed yet.
    pending = response.get("disabledCommand")
    return SbmApartmentData(
        operation_mode=operation_mode,
        connected=connected,
        system_off=(
            pending if isinstance(pending, bool) else response.get("disabled") is True
        ),
        thermostats={
            thermostat["ID"]: _parse_thermostat(thermostat)
            for thermostat in thermostats
        },
        firmware_version=_optional_text(response.get("fwVer")),
        raw=response,
    )


def _parse_thermostat(data: Mapping[str, Any]) -> SbmThermostat:
    """Convert a thermostat.

    Only the id is required: a thermostat that has not reported yet, for example one
    that was just paired, must not fail the state of the others.
    """
    return SbmThermostat(
        thermostat_id=data["ID"],
        name=_optional_text(data.get("name")),
        active=data.get("active") is True,
        temperature=_optional_float(data.get("measuredTempDegC")),
        humidity=_optional_whole_number(data.get("measuredHumPerc")),
        dew_point=_optional_float(data.get("dewPointDegC")),
        target_temperature=_target_temperature(data),
    )


def _target_temperature(data: Mapping[str, Any]) -> float | None:
    """Return the target temperature, like the app shows it.

    A new target temperature stays in temperatureSetpointCommandDegC until the
    thermostat confirms it, which takes one or two minutes; only then does it move to
    temperatureSetpointDegC.
    """
    pending = _optional_float(data.get("temperatureSetpointCommandDegC"))
    if pending is not None:
        return pending
    return _optional_float(data.get("temperatureSetpointDegC"))


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse a timestamp such as 2026-10-11 12:45:26.411281+00:00.

    A timestamp without an offset is in UTC.
    """
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)


def _round_setpoint(temperature: float) -> float:
    """Round to the 0.1 °C resolution of SBM.

    Whole values become integers, so 22.0 is sent as 22, like the bridge sends it.
    """
    if not math.isfinite(temperature):
        raise ValueError("Target temperature must be a finite number")
    value = Decimal(str(temperature)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return int(value) if value == value.to_integral_value() else float(value)


def _is_int(value: Any) -> TypeIs[int]:
    """Return whether a JSON value is an integer (booleans are not)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _optional_float(value: Any) -> float | None:
    """Return a finite JSON number as a float, anything else as None."""
    if _is_int(value) or isinstance(value, float):
        number = float(value)
        if math.isfinite(number):
            return number
    return None


def _optional_whole_number(value: Any) -> int | None:
    """Return a finite JSON number rounded half up to an int, anything else as None."""
    number = _optional_float(value)
    if number is None:
        return None
    return int(Decimal(str(number)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _row_float(row: list[Any], index: int) -> float | None:
    """Return a finite number at a position of a row, otherwise None."""
    return _optional_float(row[index] if index < len(row) else None)


def _optional_text(value: Any) -> str | None:
    """Return a string that is not blank as it is, anything else as None."""
    return value if isinstance(value, str) and value.strip() else None


def _optional_str(row: list[Any], index: int) -> str | None:
    """Return a string that is not blank at a position of a row, otherwise None."""
    return _optional_text(row[index] if index < len(row) else None)


def _describe_status(status: int) -> str:
    """Return a status code with its reason phrase, like 503 (Service Unavailable)."""
    try:
        return f"{status} ({HTTPStatus(status).phrase})"
    except ValueError:
        return str(status)


def _loggable_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a request payload without the credentials and the token."""
    return {
        key: _REDACTED if key in _SECRET_PAYLOAD_KEYS else value
        for key, value in payload.items()
    }


def _truncate(text: str) -> str:
    """Shorten an answer for an error message."""
    if len(text) <= _MAX_ERROR_BODY_LENGTH:
        return text
    return f"{text[:_MAX_ERROR_BODY_LENGTH]}…"
