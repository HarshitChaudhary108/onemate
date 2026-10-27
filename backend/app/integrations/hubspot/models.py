"""Pydantic row models for the HubSpot integration store (plan §5.5).

Models mirror the integration tables in :mod:`app.sheets.db`: the INTEGER
0/1 ``mock`` flag is ``bool``, ``properties_json`` is parsed into a real
``dict`` at the model boundary, and OAuth tokens are ``SecretStr`` —
decrypted only inside this backend layer and never serialized into an API
response.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr

RemoteObjectType = Literal["company", "contact"]
"""HubSpot object kinds handled in v1 (companies → rows, contacts → snapshots)."""


class ConnectionRecord(BaseModel):
    """One provider connection (``integration_connections`` row).

    ``access_token``/``refresh_token`` arrive decrypted from
    :mod:`app.integrations.hubspot.store` — the only place ciphertext is
    turned back into plaintext.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    portal_id: str | None = None
    access_token: SecretStr
    refresh_token: SecretStr
    token_expires_at: datetime
    scopes: list[str]
    mock: bool = False
    connected_at: datetime
    last_sync_at: datetime | None = None


class OAuthState(BaseModel):
    """One ``oauth_states`` row — single-use, TTL-bound CSRF token."""

    model_config = ConfigDict(extra="forbid")

    state: str
    provider: str
    expires_at: datetime
    created_at: datetime


class IntegrationLink(BaseModel):
    """One ``integration_links`` row binding a remote object to a sheet.

    ``row_id`` is ``None`` for contacts — they are snapshot-only and never
    materialize as rows. ``properties`` is the remote property snapshot
    parsed from ``properties_json``; refresh diffs are computed against it.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    object_type: RemoteObjectType
    remote_id: str
    sheet_id: str
    row_id: str | None = None
    properties: dict[str, str | None] = {}
    remote_updated_at: datetime | None = None
    created_at: datetime


class PushLogEntry(BaseModel):
    """One ``integration_push_log`` row — append-only push audit trail.

    ``id`` defaults to 0 as a construction-time placeholder; SQLite's
    autoincrement assigns the real id on insert.
    """

    model_config = ConfigDict(extra="forbid")

    id: int = 0
    provider: str
    row_id: str
    remote_id: str
    property: str
    value: str | None = None
    status: Literal["ok", "error"]
    error: str | None = None
    created_at: datetime


class RemoteObject(BaseModel):
    """One normalized HubSpot object as the client returns it."""

    model_config = ConfigDict(extra="forbid")

    remote_id: str
    object_type: RemoteObjectType
    properties: dict[str, str | None]
