"""SD-13: outside production, the gateway dials allowlisted numbers only.

The refusal is raised before any HTTP request exists, so these tests
mount a mock transport that fails the test if a request ever leaves —
except where the dial is supposed to proceed.
"""

import httpx
import pytest

from becca.telnyx.gateway import DialRefused
from becca.telnyx.http_gateway import HttpTelnyxGateway

OWN_PHONE = "+2348031925030"
STRANGER = "+2348099999999"


def _gateway(environment: str, allowlist: frozenset[str], dialled: list[str]) -> HttpTelnyxGateway:
    gateway = HttpTelnyxGateway(
        api_key="test-key", environment=environment, dial_allowlist=allowlist
    )

    def _handle(request: httpx.Request) -> httpx.Response:
        dialled.append(request.url.path)
        return httpx.Response(200, json={"data": {"call_sid": "sid-1"}})

    gateway._client = httpx.AsyncClient(
        base_url="https://example.invalid", transport=httpx.MockTransport(_handle)
    )
    return gateway


async def _dial(gateway: HttpTelnyxGateway, to: str) -> None:
    await gateway.place_call(
        connection_id="conn-1",
        assistant_id="assistant-1",
        to=to,
        from_="+2342093940544",
        variables={},
        metadata={},
        record=False,
        status_callback="",
        amd_status_callback="",
    )


async def test_dev_refuses_a_number_not_on_the_allowlist() -> None:
    dialled: list[str] = []
    gateway = _gateway("dev", frozenset({OWN_PHONE}), dialled)
    with pytest.raises(DialRefused):
        await _dial(gateway, STRANGER)
    assert dialled == []  # refused before any request was sent


async def test_staging_with_no_allowlist_dials_nobody() -> None:
    """Fail closed: the dangerous default is the safe one."""
    dialled: list[str] = []
    gateway = _gateway("staging", frozenset(), dialled)
    with pytest.raises(DialRefused):
        await _dial(gateway, OWN_PHONE)
    assert dialled == []


async def test_dev_dials_an_allowlisted_number() -> None:
    dialled: list[str] = []
    gateway = _gateway("dev", frozenset({OWN_PHONE}), dialled)
    await _dial(gateway, OWN_PHONE)
    assert dialled  # the request went out


async def test_allowlist_matching_ignores_formatting() -> None:
    """ "+234 803 192 5030" in config still admits +2348031925030."""
    dialled: list[str] = []
    gateway = _gateway("dev", frozenset({"+234 803 192 5030"}), dialled)
    await _dial(gateway, "+234-803-192-5030")
    assert dialled


async def test_production_is_unaffected_by_the_allowlist() -> None:
    dialled: list[str] = []
    gateway = _gateway("production", frozenset(), dialled)
    await _dial(gateway, STRANGER)
    assert dialled
