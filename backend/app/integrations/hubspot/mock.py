"""Deterministic in-memory HubSpot portal (``FakeHubSpotClient``).

Used only in tests or when ``HUBSPOT_MODE=mock`` is explicitly configured.
Contents are obviously fictional (``*.test`` domains); ``patch_object``
calls are recorded so tests can assert exactly what would have been
written. ``RemoteObject`` carries no ``mock`` flag — provenance is
disclosed by the caller (connection ``mock`` column, status payload).
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from app.errors.integration_error import IntegrationError
from app.integrations.hubspot.client import HubSpotClient
from app.integrations.hubspot.models import RemoteObject, RemoteObjectType

_DEFAULT_COMPANIES: dict[str, dict[str, str | None]] = {
    "1001": {
        "name": "Example Robotics Ltd",
        "domain": "example-robotics.test",
        "industry": "Robotics",
    },
    "1002": {
        "name": "Synthetic Components Ltd",
        "domain": "synthetic-components.test",
        "industry": "Manufacturing",
    },
}

_DEFAULT_CONTACTS: dict[str, dict[str, str | None]] = {
    "2001": {
        "firstname": "Ada",
        "lastname": "Example",
        "email": "ada@example-robotics.test",
        "jobtitle": "CTO",
        "associatedcompanyid": "1001",
    },
}


class FakeHubSpotClient:
    """In-memory ``HubSpotClient``. Never performs I/O.

    ``companies``/``contacts`` map remote ids to property dicts; pass
    custom dicts to seed a test scenario or rely on the fictional
    defaults. ``patch_calls`` records every ``patch_object`` invocation in
    order. ``calls`` counts method invocations.
    """

    def __init__(
        self,
        *,
        companies: dict[str, dict[str, str | None]] | None = None,
        contacts: dict[str, dict[str, str | None]] | None = None,
    ) -> None:
        self._objects: dict[RemoteObjectType, dict[str, dict[str, str | None]]] = {
            "company": dict(companies) if companies is not None else dict(_DEFAULT_COMPANIES),
            "contact": dict(contacts) if contacts is not None else dict(_DEFAULT_CONTACTS),
        }
        # Copy one level deeper so caller mutations don't alias the defaults.
        for mapping in self._objects.values():
            for key, value in mapping.items():
                mapping[key] = dict(value)
        self.patch_calls: list[dict[str, Any]] = []
        self.calls = 0

    async def list_objects(
        self,
        access_token: str,
        object_type: RemoteObjectType,
        properties: Sequence[str],
        limit: int = 100,
        after: str | None = None,
    ) -> tuple[list[RemoteObject], str | None]:
        self.calls += 1
        ids = sorted(self._objects[object_type])
        start = ids.index(after) + 1 if after in ids else 0
        page_ids = ids[start : start + limit]
        next_after = page_ids[-1] if start + limit < len(ids) else None
        return (
            [self._to_object(object_type, remote_id, properties) for remote_id in page_ids],
            next_after,
        )

    async def batch_read(
        self,
        access_token: str,
        object_type: RemoteObjectType,
        ids: Sequence[str],
        properties: Sequence[str],
    ) -> list[RemoteObject]:
        self.calls += 1
        return [
            self._to_object(object_type, remote_id, properties)
            for remote_id in ids
            if remote_id in self._objects[object_type]
        ]

    async def patch_object(
        self,
        access_token: str,
        object_type: RemoteObjectType,
        remote_id: str,
        properties: dict[str, str],
    ) -> None:
        self.calls += 1
        self.patch_calls.append(
            {"object_type": object_type, "remote_id": remote_id, "properties": dict(properties)}
        )
        records = self._objects[object_type]
        if remote_id not in records:
            raise IntegrationError(
                code="hubspot_not_found", message="The requested HubSpot object was not found."
            )
        records[remote_id].update(properties)

    async def token_info(self, access_token: str) -> dict[str, Any]:
        self.calls += 1
        return {
            "token": access_token,
            "hub_id": "sandbox-fake",
            "hub_domain": "sandbox-fake.test",
            "scopes": [
                "crm.objects.companies.read",
                "crm.objects.companies.write",
                "crm.objects.contacts.read",
                "crm.objects.contacts.write",
            ],
            "user": "sandbox@example.test",
            "user_id": 1,
            "app_id": 0,
            "expires_in": 1800,
            "token_type": "access",
        }

    def _to_object(
        self,
        object_type: RemoteObjectType,
        remote_id: str,
        properties: Sequence[str],
    ) -> RemoteObject:
        stored = self._objects[object_type][remote_id]
        projected = {k: v for k, v in stored.items() if k in properties} if properties else stored
        return RemoteObject(
            remote_id=remote_id, object_type=object_type, properties=dict(projected)
        )


if TYPE_CHECKING:
    _conformance_check: HubSpotClient = FakeHubSpotClient()
