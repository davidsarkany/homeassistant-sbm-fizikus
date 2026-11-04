"""Fixtures of the SBM Fizikus tests."""

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pytest
from yarl import URL

from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.sbm_fizikus.api import BASE_URL
from custom_components.sbm_fizikus.const import (
    CONF_APARTMENT_ID,
    CONF_PWHASH,
    DOMAIN,
)

FIXTURES = Path(__file__).parent / "fixtures"
SBM_URL = f"{BASE_URL}/frontend"

# The credentials of the tests of the bridge, and the hash of the password.
USERNAME = "user@example.com"
PASSWORD = "secret"
PWHASH = "2bb80d537b1da3e38bd30361aa855686bde0eacd7162fef6a25fe97bf527a25b"
APARTMENT_ID = 123
APARTMENT_NAME = "BP-TEST-001-101"
LIVING_ROOM_ID = 1001


def load_fixture_json(name: str) -> Any:
    """Load a copy of an SBM answer of the bridge's tests."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@dataclass(frozen=True, kw_only=True)
class _Answer:
    """An answer of the fake SBM API that is used once."""

    status: int = 200
    json_body: Any = None
    text: str | None = None
    content: bytes | None = None
    exc: Exception | None = None


class SbmStub:
    """A fake SBM API that answers every operation from the fixtures.

    Tests change `apartment_data`, the answer to get_apartment_data, and queue other
    answers with `queue`: each is used once, in order, before the fixtures.
    """

    def __init__(self, aioclient_mock: AiohttpClientMocker) -> None:
        """Answer the SBM requests of the mocked aiohttp client."""
        self.apartment_data: dict[str, Any] = load_fixture_json(
            "get_apartment_data.json"
        )
        self.requests: list[dict[str, Any]] = []
        self.bodies: list[bytes] = []
        self._queued: defaultdict[str, deque[_Answer]] = defaultdict(deque)
        aioclient_mock.put(SBM_URL, side_effect=self._async_answer)

    def queue(
        self,
        operation: str,
        *,
        status: int = 200,
        json_body: Any = None,
        text: str | None = None,
        content: bytes | None = None,
        exc: Exception | None = None,
    ) -> None:
        """Answer the next request of an operation with this answer."""
        self._queued[operation].append(
            _Answer(
                status=status, json_body=json_body, text=text, content=content, exc=exc
            )
        )

    @property
    def operations(self) -> list[str]:
        """Return the operations of the requests, in order."""
        return [request["operation"] for request in self.requests]

    def payloads(self, operation: str) -> list[dict[str, Any]]:
        """Return the payloads of the requests of an operation, in order."""
        return [
            request["payload"]
            for request in self.requests
            if request["operation"] == operation
        ]

    async def _async_answer(
        self, method: str, url: URL, data: bytes
    ) -> AiohttpClientMockResponse:
        """Record a request and answer it."""
        # Let other tasks run, as a real request would.
        await asyncio.sleep(0)
        request = json.loads(data)
        self.requests.append(request)
        self.bodies.append(data)
        operation = request["operation"]
        if self._queued[operation]:
            answer = self._queued[operation].popleft()
            return AiohttpClientMockResponse(
                method,
                url,
                status=answer.status,
                json=answer.json_body,
                text=answer.text,
                response=answer.content,
                exc=answer.exc,
            )
        if operation == "get_apartment_data":
            return AiohttpClientMockResponse(method, url, json=self.apartment_data)
        return AiohttpClientMockResponse(
            method,
            url,
            text=(FIXTURES / f"{operation}.json").read_text(encoding="utf-8"),
        )


@pytest.fixture
def sbm(aioclient_mock: AiohttpClientMocker) -> SbmStub:
    """Return the fake SBM API."""
    return SbmStub(aioclient_mock)


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry of the apartment of the fixtures."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=APARTMENT_NAME,
        unique_id=str(APARTMENT_ID),
        data={
            CONF_USERNAME: USERNAME,
            CONF_PWHASH: PWHASH,
            CONF_APARTMENT_ID: APARTMENT_ID,
        },
    )


async def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Set up the integration with a config entry."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    # Also the first outdoor fetch, which runs in the background.
    await hass.async_block_till_done(wait_background_tasks=True)
