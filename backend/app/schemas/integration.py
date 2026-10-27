"""API contracts for the integrations endpoints (plan §5.3, §5.6).

Same discipline as :mod:`app.schemas.sheet`: request bodies are
validated here (``extra="forbid"``, whitespace-stripped, bounded);
response models serialize the integration service's results. Tokens
and secrets never appear in these contracts — ``HubSpotStatus`` carries
metadata only.

This API has NO authentication — run it locally or behind a trusted
boundary only.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.integrations.hubspot.models import RemoteObjectType


class HubSpotStatus(BaseModel):
    """Connection state for the status card.

    ``connected`` is False when no connection row exists — never an
    error. ``mock`` discloses mock mode / a mock connection; counts are
    real aggregates over ``integration_links``, never hardcoded.
    """

    model_config = ConfigDict(extra="forbid")

    connected: bool
    portal_id: str | None = None
    scopes: list[str] = []
    connected_at: datetime | None = None
    last_sync_at: datetime | None = None
    companies_imported: int = 0
    contacts_imported: int = 0
    mock: bool = False


class RemoteObjectPreview(BaseModel):
    """One remote object for the import picker — the normalized shape
    the HubSpot client returns."""

    model_config = ConfigDict(extra="forbid")

    remote_id: str
    object_type: RemoteObjectType
    properties: dict[str, str | None]


class ImportRequest(BaseModel):
    """Import selected remote objects into a sheet. ``None`` id lists
    mean "import everything fetched"; an empty list imports none of
    that object type."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sheet_id: str = Field(min_length=1, max_length=64)
    company_ids: list[str] | None = Field(default=None, max_length=200)
    contact_ids: list[str] | None = Field(default=None, max_length=200)


class ImportResponse(BaseModel):
    """Honest accounting: ``created`` new rows, ``skipped`` fetched
    companies that produced no row (already linked, domain-dupe,
    unimportable), ``linked_*`` links written for this sheet."""

    model_config = ConfigDict(extra="forbid")

    created: int
    skipped: int
    linked_companies: int
    linked_contacts: int


class PushApproval(BaseModel):
    """One approved write. The client says WHICH cell and WHICH remote
    property — never WHAT value; the backend re-reads the value
    server-side (plan §3.4)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    row_id: str = Field(min_length=1, max_length=64)
    column_id: str = Field(min_length=1, max_length=64)
    property: str = Field(min_length=1, max_length=100)


class PushRequest(BaseModel):
    """A bounded batch of approvals — at most 50 per call."""

    model_config = ConfigDict(extra="forbid")

    sheet_id: str = Field(min_length=1, max_length=64)
    approvals: list[PushApproval] = Field(min_length=1, max_length=50)


class PushItemResult(BaseModel):
    """Per-approval outcome: ``pushed`` writes went through; ``skipped``
    means a precondition failed (``not_linked``/``no_value``) — nothing
    was attempted; ``error`` means a bad request or a failed PATCH
    (``detail`` carries the code)."""

    model_config = ConfigDict(extra="forbid")

    row_id: str
    column_id: str
    property: str
    status: Literal["pushed", "skipped", "error"]
    detail: str | None = None


class PushResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[PushItemResult]


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_id: str = Field(min_length=1, max_length=64)


class RefreshChange(BaseModel):
    """One remote property that differs from the stored snapshot.
    ``previous`` is the snapshot value (``None`` when it was absent);
    ``remote_value`` is what HubSpot returned (``None`` when the remote
    no longer returns the property). Cells are never touched."""

    model_config = ConfigDict(extra="forbid")

    row_id: str | None
    remote_id: str
    property: str
    remote_value: str | None
    previous: str | None


class RefreshResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: list[RefreshChange]


class LinkInfo(BaseModel):
    """One remote→row binding. ``row_id`` is ``None`` for contacts —
    snapshot-only in v1."""

    model_config = ConfigDict(extra="forbid")

    row_id: str | None
    remote_id: str
    object_type: RemoteObjectType


class LinksResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    links: list[LinkInfo]
