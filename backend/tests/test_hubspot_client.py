"""HttpHubSpotClient tests (I2).

All response payloads below are SYNTHETIC, hand-written from the documented
HubSpot shapes (``results`` pages with ``paging.next.after``, batch-read
``{inputs, properties}``, ``/oauth/v1/access-tokens/{token}`` metadata).
No network is touched — respx intercepts every call.
"""

import json
from collections.abc import Awaitable, Callable

import httpx
import pytest
import respx

from app.errors.integration_error import IntegrationError
from app.integrations.hubspot.client import (
    BOUNDED_BACKOFF_SECONDS,
    HUBSPOT_API_BASE,
    RETRY_WAIT_BUDGET_SECONDS,
    HttpHubSpotClient,
    HubSpotClient,
)
from app.integrations.hubspot.mock import FakeHubSpotClient

TOKEN = "pat-na1-synthetic-token-XYZ"
OBJECTS_URL = f"{HUBSPOT_API_BASE}/crm/v3/objects/companies"
BATCH_URL = f"{OBJECTS_URL}/batch/read"
PATCH_URL = f"{OBJECTS_URL}/4242"
TOKEN_URL = f"{HUBSPOT_API_BASE}/oauth/v1/access-tokens/{TOKEN}"

COMPANY_PAGE_1: dict = {
    "results": [
        {
            "id": "1001",
            "properties": {"name": "Example Robotics Ltd", "domain": "example-robotics.test"},
        },
        {
            "id": "1002",
            "properties": {
                "name": "Synthetic Components Ltd",
                "domain": "synthetic-components.test",
            },
        },
    ],
    "paging": {"next": {"after": "1002"}},
}
COMPANY_PAGE_2: dict = {
    "results": [
        {
            "id": "1003",
            "properties": {"name": "Fixture Fabrication Ltd", "domain": "fixture-fab.test"},
        },
    ],
}


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def no_sleep(sleeps: list[float]) -> Callable[[float], Awaitable[None]]:
    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return _sleep


def _client(no_sleep: Callable[[float], Awaitable[None]]) -> HttpHubSpotClient:
    return HttpHubSpotClient(sleep=no_sleep)


# --- list_objects -------------------------------------------------------------


async def test_list_objects_pagination_and_projection(no_sleep) -> None:
    with respx.mock:
        route = respx.get(OBJECTS_URL).mock(
            side_effect=[
                httpx.Response(200, json=COMPANY_PAGE_1),
                httpx.Response(200, json=COMPANY_PAGE_2),
            ]
        )
        client = _client(no_sleep)
        try:
            page1, after = await client.list_objects(
                TOKEN, "company", ["name", "domain"], limit=100
            )
            page2, after2 = await client.list_objects(
                TOKEN, "company", ["name", "domain"], limit=100, after=after
            )
        finally:
            await client.aclose()

    assert [o.remote_id for o in page1] == ["1001", "1002"]
    assert page1[0].object_type == "company"
    assert page1[0].properties["domain"] == "example-robotics.test"
    assert after == "1002"
    assert [o.remote_id for o in page2] == ["1003"]
    assert after2 is None

    req1 = route.calls[0].request
    assert req1.headers["authorization"] == f"Bearer {TOKEN}"
    assert req1.url.params["limit"] == "100"
    assert req1.url.params["properties"] == "name,domain"
    req2 = route.calls[1].request
    assert req2.url.params["after"] == "1002"


async def test_list_objects_no_paging_key_is_last_page(no_sleep) -> None:
    with respx.mock:
        respx.get(OBJECTS_URL).mock(return_value=httpx.Response(200, json={"results": []}))
        client = _client(no_sleep)
        try:
            objects, after = await client.list_objects(TOKEN, "company", ["name"])
        finally:
            await client.aclose()
    assert objects == []
    assert after is None


# --- batch_read ---------------------------------------------------------------


async def test_batch_read_payload_shape(no_sleep) -> None:
    payload = {
        "results": [
            {"id": "1001", "properties": {"name": "Example Robotics Ltd"}},
            {"id": "1003", "properties": {"name": "Fixture Fabrication Ltd"}},
        ],
        "status": "COMPLETE",
    }
    with respx.mock:
        route = respx.post(BATCH_URL).mock(return_value=httpx.Response(200, json=payload))
        client = _client(no_sleep)
        try:
            objects = await client.batch_read(
                TOKEN, "company", ["1001", "1003"], ["name", "domain"]
            )
        finally:
            await client.aclose()

    body = json.loads(route.calls[0].request.content)
    assert body == {
        "inputs": [{"id": "1001"}, {"id": "1003"}],
        "properties": ["name", "domain"],
    }
    assert [o.remote_id for o in objects] == ["1001", "1003"]
    assert objects[0].properties == {"name": "Example Robotics Ltd"}


async def test_batch_read_contacts_path(no_sleep) -> None:
    url = f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/batch/read"
    with respx.mock:
        route = respx.post(url).mock(return_value=httpx.Response(200, json={"results": []}))
        client = _client(no_sleep)
        try:
            assert await client.batch_read(TOKEN, "contact", ["1"], ["email"]) == []
        finally:
            await client.aclose()
    assert route.called


# --- patch_object -------------------------------------------------------------


async def test_patch_object_body_is_properties_wrapper(no_sleep) -> None:
    with respx.mock:
        route = respx.patch(PATCH_URL).mock(
            return_value=httpx.Response(200, json={"id": "4242", "properties": {}})
        )
        client = _client(no_sleep)
        try:
            await client.patch_object(
                TOKEN, "company", "4242", {"outmate_tech_stack": "synthetic stack"}
            )
        finally:
            await client.aclose()

    body = json.loads(route.calls[0].request.content)
    assert body == {"properties": {"outmate_tech_stack": "synthetic stack"}}
    assert route.calls[0].request.method == "PATCH"


# --- token_info ---------------------------------------------------------------


async def test_token_info_returns_metadata_dict(no_sleep) -> None:
    payload = {
        "token": TOKEN,
        "hub_id": 7654321,
        "hub_domain": "example.test",
        "scopes": ["crm.objects.companies.read"],
        "user_id": 42,
        "token_type": "access",
    }
    with respx.mock:
        respx.get(TOKEN_URL).mock(return_value=httpx.Response(200, json=payload))
        client = _client(no_sleep)
        try:
            info = await client.token_info(TOKEN)
        finally:
            await client.aclose()
    assert info["hub_id"] == 7654321
    assert info["scopes"] == ["crm.objects.companies.read"]


# --- error mapping -------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "hubspot_api_error"),
        (401, "hubspot_auth"),
        (403, "hubspot_forbidden"),
        (404, "hubspot_not_found"),
    ],
)
async def test_error_status_mapping_no_retry(
    status: int, code: str, no_sleep, sleeps: list[float]
) -> None:
    with respx.mock:
        route = respx.get(OBJECTS_URL).mock(
            return_value=httpx.Response(status, json={"message": "synthetic"})
        )
        client = _client(no_sleep)
        try:
            with pytest.raises(IntegrationError) as excinfo:
                await client.list_objects(TOKEN, "company", ["name"])
        finally:
            await client.aclose()
    assert excinfo.value.code == code
    assert route.call_count == 1
    assert sleeps == []


async def test_property_missing_maps_distinct_code(no_sleep) -> None:
    """A 400 PROPERTY_DOESNT_EXIST surfaces as ``hubspot_property_missing`` —
    the portal-setup gap the push dialog must be able to name."""
    body = {
        "status": "error",
        "message": 'Property values were not valid: "outmate_x" does not exist',
        "errors": [
            {
                "message": 'Property "outmate_x" does not exist',
                "code": "PROPERTY_DOESNT_EXIST",
            }
        ],
        "category": "VALIDATION_ERROR",
    }
    with respx.mock:
        respx.patch(PATCH_URL).mock(return_value=httpx.Response(400, json=body))
        client = _client(no_sleep)
        try:
            with pytest.raises(IntegrationError) as excinfo:
                await client.patch_object(TOKEN, "company", "4242", {"outmate_x": "v"})
        finally:
            await client.aclose()
    assert excinfo.value.code == "hubspot_property_missing"


async def test_validation_error_without_subcode_is_generic(no_sleep) -> None:
    """A 400 whose body carries no errors[].code stays ``hubspot_api_error``."""
    with respx.mock:
        respx.patch(PATCH_URL).mock(
            return_value=httpx.Response(400, json={"status": "error", "message": "bad"})
        )
        client = _client(no_sleep)
        try:
            with pytest.raises(IntegrationError) as excinfo:
                await client.patch_object(TOKEN, "company", "4242", {"outmate_x": "v"})
        finally:
            await client.aclose()
    assert excinfo.value.code == "hubspot_api_error"


async def test_rate_limited_retries_once_honoring_retry_after(
    no_sleep, sleeps: list[float]
) -> None:
    with respx.mock:
        route = respx.get(OBJECTS_URL).mock(
            return_value=httpx.Response(429, headers={"Retry-After": "3"})
        )
        client = _client(no_sleep)
        try:
            with pytest.raises(IntegrationError) as excinfo:
                await client.list_objects(TOKEN, "company", ["name"])
        finally:
            await client.aclose()
    assert excinfo.value.code == "hubspot_rate_limited"
    assert route.call_count == 2  # exactly one retry
    assert sleeps == [3.0]  # honored the numeric Retry-After
    assert client.calls == 2


async def test_rate_limited_retry_succeeds(no_sleep, sleeps: list[float]) -> None:
    with respx.mock:
        route = respx.get(OBJECTS_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "2"}),
                httpx.Response(200, json=COMPANY_PAGE_2),
            ]
        )
        client = _client(no_sleep)
        try:
            objects, _ = await client.list_objects(TOKEN, "company", ["name"])
        finally:
            await client.aclose()
    assert route.call_count == 2
    assert sleeps == [2.0]
    assert [o.remote_id for o in objects] == ["1003"]


async def test_rate_limited_wait_over_budget_does_not_retry(no_sleep, sleeps: list[float]) -> None:
    with respx.mock:
        route = respx.get(OBJECTS_URL).mock(
            return_value=httpx.Response(
                429, headers={"Retry-After": str(RETRY_WAIT_BUDGET_SECONDS + 5)}
            )
        )
        client = _client(no_sleep)
        try:
            with pytest.raises(IntegrationError) as excinfo:
                await client.list_objects(TOKEN, "company", ["name"])
        finally:
            await client.aclose()
    assert excinfo.value.code == "hubspot_rate_limited"
    assert route.call_count == 1
    assert sleeps == []


async def test_rate_limited_no_header_uses_bounded_backoff(no_sleep, sleeps: list[float]) -> None:
    with respx.mock:
        respx.get(OBJECTS_URL).mock(return_value=httpx.Response(429))
        client = _client(no_sleep)
        try:
            with pytest.raises(IntegrationError) as excinfo:
                await client.list_objects(TOKEN, "company", ["name"])
        finally:
            await client.aclose()
    assert excinfo.value.code == "hubspot_rate_limited"
    assert sleeps == [BOUNDED_BACKOFF_SECONDS]


async def test_server_error_retries_once(no_sleep, sleeps: list[float]) -> None:
    with respx.mock:
        route = respx.get(OBJECTS_URL).mock(return_value=httpx.Response(500))
        client = _client(no_sleep)
        try:
            with pytest.raises(IntegrationError) as excinfo:
                await client.list_objects(TOKEN, "company", ["name"])
        finally:
            await client.aclose()
    assert excinfo.value.code == "hubspot_server_error"
    assert route.call_count == 2
    assert sleeps == [BOUNDED_BACKOFF_SECONDS]


async def test_timeout_maps_after_one_retry(no_sleep, sleeps: list[float]) -> None:
    with respx.mock:
        route = respx.get(OBJECTS_URL).mock(side_effect=httpx.TimeoutException("synthetic timeout"))
        client = _client(no_sleep)
        try:
            with pytest.raises(IntegrationError) as excinfo:
                await client.list_objects(TOKEN, "company", ["name"])
        finally:
            await client.aclose()
    assert excinfo.value.code == "hubspot_timeout"
    assert route.call_count == 2
    assert sleeps == [BOUNDED_BACKOFF_SECONDS]


async def test_network_error_maps_after_one_retry(no_sleep) -> None:
    with respx.mock:
        route = respx.get(OBJECTS_URL).mock(
            side_effect=httpx.ConnectError("synthetic connect error")
        )
        client = _client(no_sleep)
        try:
            with pytest.raises(IntegrationError) as excinfo:
                await client.list_objects(TOKEN, "company", ["name"])
        finally:
            await client.aclose()
    assert excinfo.value.code == "hubspot_network"
    assert route.call_count == 2


async def test_unparseable_json_is_invalid_response(no_sleep) -> None:
    with respx.mock:
        respx.get(OBJECTS_URL).mock(return_value=httpx.Response(200, text="<html>not json</html>"))
        client = _client(no_sleep)
        try:
            with pytest.raises(IntegrationError) as excinfo:
                await client.list_objects(TOKEN, "company", ["name"])
        finally:
            await client.aclose()
    assert excinfo.value.code == "hubspot_invalid_response"


async def test_json_missing_results_is_invalid_response(no_sleep) -> None:
    with respx.mock:
        respx.get(OBJECTS_URL).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))
        client = _client(no_sleep)
        try:
            with pytest.raises(IntegrationError) as excinfo:
                await client.list_objects(TOKEN, "company", ["name"])
        finally:
            await client.aclose()
    assert excinfo.value.code == "hubspot_invalid_response"


async def test_result_item_missing_id_is_invalid_response(no_sleep) -> None:
    with respx.mock:
        respx.get(OBJECTS_URL).mock(
            return_value=httpx.Response(200, json={"results": [{"properties": {}}]})
        )
        client = _client(no_sleep)
        try:
            with pytest.raises(IntegrationError) as excinfo:
                await client.list_objects(TOKEN, "company", ["name"])
        finally:
            await client.aclose()
    assert excinfo.value.code == "hubspot_invalid_response"


async def test_token_never_in_error_messages(no_sleep) -> None:
    for status in (401, 403, 429, 500):
        with respx.mock:
            respx.get(OBJECTS_URL).mock(return_value=httpx.Response(status))
            client = _client(no_sleep)
            try:
                with pytest.raises(IntegrationError) as excinfo:
                    await client.list_objects(TOKEN, "company", ["name"])
            finally:
                await client.aclose()
        err = excinfo.value
        assert TOKEN not in err.message
        assert TOKEN not in err.code
        assert TOKEN not in str(err)
        assert TOKEN not in repr(err)


# --- fake client ------------------------------------------------------------


async def test_fake_client_satisfies_protocol_and_records_patches() -> None:
    fake = FakeHubSpotClient()
    assert isinstance(fake, HubSpotClient)

    objects, after = await fake.list_objects("ignored-token", "company", ["name"])
    assert after is None
    assert {o.remote_id for o in objects} == {"1001", "1002"}
    assert all(set(o.properties) == {"name"} for o in objects)

    await fake.patch_object("ignored-token", "company", "1001", {"outmate_x": "1"})
    assert fake.patch_calls == [
        {
            "object_type": "company",
            "remote_id": "1001",
            "properties": {"outmate_x": "1"},
        }
    ]
    got = await fake.batch_read("ignored-token", "company", ["1001"], ["outmate_x"])
    assert got[0].properties["outmate_x"] == "1"

    info = await fake.token_info("ignored-token")
    assert info["hub_id"] == "sandbox-fake"

    with pytest.raises(IntegrationError) as excinfo:
        await fake.patch_object("ignored-token", "company", "ghost", {})
    assert excinfo.value.code == "hubspot_not_found"
