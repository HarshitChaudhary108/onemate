"""Business rules for the HubSpot integration (plan §5.1, §3.2–§3.5).

``IntegrationService`` sits between the API router and
:mod:`app.integrations.hubspot.store`/``client``/``oauth``:

- **import** dedupes twice — by ``integration_links`` remote id AND by
  ``normalize_domain().compare_key`` against existing sheet rows — then
  writes companies as rows through :meth:`SheetService.import_rows`
  and contacts as snapshot-only links (``row_id`` NULL).
- **push** re-reads cell values server-side and PATCHes only
  ``outmate_*`` properties on linked companies; ``done`` cells with a
  non-null value only — ``unavailable``/unfinished cells are never
  pushed. Every attempted write lands in ``integration_push_log``.
- **refresh** batch-reads linked remote objects, diffs them against the
  stored snapshot, and updates the snapshot ONLY — cells are never
  modified.

``build_client`` mirrors ``build_providers``: ``HUBSPOT_MODE=off`` or
``live`` without credentials yields ``None`` — routes then answer
``hubspot_not_configured`` (503), never a silent mock. In ``mock`` mode
the client is :class:`FakeHubSpotClient` and no OAuth connection is
required (the fake ignores the token); every response still discloses
``mock: true``.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from app.config import Settings
from app.errors.integration_error import IntegrationError
from app.errors.sheet_error import SheetError
from app.integrations.hubspot import oauth, store
from app.integrations.hubspot.client import HttpHubSpotClient, HubSpotClient
from app.integrations.hubspot.mapping import (
    ALLOWED_PUSH_PROPERTIES,
    ensure_push_property,
    serialize_value,
)
from app.integrations.hubspot.mock import FakeHubSpotClient
from app.integrations.hubspot.models import (
    ConnectionRecord,
    IntegrationLink,
    PushLogEntry,
    RemoteObject,
    RemoteObjectType,
)
from app.integrations.hubspot.store import TokenCipher
from app.normalize.domain import DomainError, NormalizedDomain, normalize_domain
from app.schemas.integration import (
    HubSpotStatus,
    ImportResponse,
    LinkInfo,
    PushApproval,
    PushItemResult,
    RefreshChange,
    RemoteObjectPreview,
)
from app.sheets import repo
from app.sheets.models import Row, RowInput
from app.sheets.service import MAX_ROWS_PER_IMPORT, SheetService

_PROVIDER = "hubspot"

_COMPANY_PROPERTIES = ["name", "domain", "industry", "website", "description"]
_CONTACT_PROPERTIES = ["firstname", "lastname", "email", "jobtitle", "associatedcompanyid"]
_FETCH_CAP = 100
_MOCK_ACCESS_TOKEN = "mock-access-token"
"""Synthetic token used only with ``FakeHubSpotClient`` — it is never
sent to a real host because the fake performs no I/O."""

_NOT_CONFIGURED_MSG = (
    "HubSpot is not configured: set the client id, client secret, and token key on the backend."
)


def build_client(settings: Settings) -> HubSpotClient | None:
    """Map settings to a client implementation — explicit mock opt-in,
    ``None`` for off/unconfigured (never a silent mock)."""
    if settings.hubspot_mode == "off":
        return None
    if settings.hubspot_mode == "mock":
        return FakeHubSpotClient()
    return HttpHubSpotClient() if settings.hubspot_configured else None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _company_domain(properties: dict[str, str | None]) -> NormalizedDomain | None:
    """First parseable domain among ``domain``/``website`` — write-once
    ``input_domain`` source (plan §6)."""
    for key in ("domain", "website"):
        raw = properties.get(key)
        if not raw:
            continue
        try:
            return normalize_domain(raw)
        except DomainError:
            continue
    return None


def _compare_key(domain: NormalizedDomain) -> str:
    return domain.compare_key


class IntegrationService:
    """The validated integration path; the client is injectable for tests.

    ``fernet`` is the token-encryption cipher — ``None`` when
    ``HUBSPOT_TOKEN_KEY`` is unset; every operation that touches stored
    tokens then reports ``hubspot_not_configured`` instead of failing
    obscurely inside the crypto layer.
    """

    def __init__(
        self,
        db_path: str,
        settings: Settings,
        fernet: TokenCipher | None,
        sheet_service: SheetService,
        *,
        client: HubSpotClient | None = None,
    ) -> None:
        self._db = db_path
        self._settings = settings
        self._fernet = fernet
        self._sheets = sheet_service
        self.client: HubSpotClient | None = client if client is not None else build_client(settings)

    async def aclose(self) -> None:
        """Close the owned client transport, if it has one."""
        aclose = getattr(self.client, "aclose", None)
        if aclose is not None:
            await aclose()

    def require_client(self) -> HubSpotClient:
        """The configured client, or ``hubspot_not_configured`` — never
        a silent mock."""
        if self.client is None:
            raise IntegrationError(code="hubspot_not_configured", message=_NOT_CONFIGURED_MSG)
        return self.client

    async def _access_token(self) -> str:
        """A live access token for one outbound call.

        Mock mode needs no OAuth connection — the fake ignores the
        token. Live mode loads the stored connection, refreshing it
        when it is about to expire; a missing connection is
        ``hubspot_not_connected`` (409).
        """
        self.require_client()
        if self._settings.hubspot_mode == "mock":
            return _MOCK_ACCESS_TOKEN
        if self._fernet is None:
            raise IntegrationError(code="hubspot_not_configured", message=_NOT_CONFIGURED_MSG)
        record = await oauth.ensure_fresh_token(self._db, self._fernet, self._settings)
        return record.access_token.get_secret_value()

    # --- connection lifecycle (thin wrappers for the routes) ---------------

    async def create_state(self) -> str:
        return await store.create_state(self._db, _PROVIDER)

    async def consume_state(self, state: str) -> bool:
        return await store.consume_state(self._db, _PROVIDER, state)

    async def save_connection(self, record: ConnectionRecord) -> None:
        """Encrypt+persist tokens after the OAuth exchange. Without a
        configured token key there is no honest ciphertext to store."""
        if self._fernet is None:
            raise IntegrationError(code="hubspot_not_configured", message=_NOT_CONFIGURED_MSG)
        await store.save_connection(self._db, self._fernet, record)

    async def delete_connection(self) -> bool:
        """Disconnect: delete the connection row; links survive a
        reconnect (plan §5.3)."""
        return await store.delete_connection(self._db, _PROVIDER)

    async def status(self) -> HubSpotStatus:
        """Connection status — ``connected=False`` when no connection
        row exists (never an error); mock mode is an implicit
        connection, always disclosed as ``mock=True``."""
        counts = await store.import_counts(self._db, _PROVIDER)
        record = (
            await store.get_connection(self._db, self._fernet, _PROVIDER)
            if self._fernet is not None
            else None
        )
        mock = self._settings.hubspot_mode == "mock" or (record is not None and record.mock)
        if record is None:
            return HubSpotStatus(
                connected=self._settings.hubspot_mode == "mock",
                portal_id=None,
                scopes=[],
                connected_at=None,
                last_sync_at=None,
                companies_imported=counts["companies"],
                contacts_imported=counts["contacts"],
                mock=mock,
            )
        return HubSpotStatus(
            connected=True,
            portal_id=record.portal_id,
            scopes=record.scopes,
            connected_at=record.connected_at,
            last_sync_at=record.last_sync_at,
            companies_imported=counts["companies"],
            contacts_imported=counts["contacts"],
            mock=mock,
        )

    async def list_links(self, sheet_id: str) -> list[LinkInfo]:
        links = await store.links_for_sheet(self._db, _PROVIDER, sheet_id)
        return [
            LinkInfo(row_id=link.row_id, remote_id=link.remote_id, object_type=link.object_type)
            for link in links
        ]

    async def preview_objects(self, object_type: RemoteObjectType) -> list[RemoteObjectPreview]:
        """Remote objects for the import picker — needs a live
        connection in live mode (``hubspot_not_connected`` → 409)."""
        client = self.require_client()
        token = await self._access_token()
        properties = _COMPANY_PROPERTIES if object_type == "company" else _CONTACT_PROPERTIES
        objects = await self._fetch(client, token, object_type, None, properties)
        return [
            RemoteObjectPreview(
                remote_id=obj.remote_id, object_type=obj.object_type, properties=obj.properties
            )
            for obj in objects
        ]

    # --- import (plan §3.2) --------------------------------------------------

    async def _fetch(
        self,
        client: HubSpotClient,
        token: str,
        object_type: RemoteObjectType,
        ids: Sequence[str] | None,
        properties: Sequence[str],
    ) -> list[RemoteObject]:
        """Paginate ``list_objects`` filtered to ``ids`` (``None`` =
        everything), capped at ``_FETCH_CAP`` objects. An explicit empty
        id list fetches nothing."""
        if ids is not None:
            wanted = set(ids)
            if not wanted:
                return []
        else:
            wanted = None
        out: list[RemoteObject] = []
        after: str | None = None
        while len(out) < _FETCH_CAP:
            page, after = await client.list_objects(
                token, object_type, properties, limit=100, after=after
            )
            for obj in page:
                if wanted is None or obj.remote_id in wanted:
                    out.append(obj)
                    if len(out) >= _FETCH_CAP:
                        break
            if after is None:
                break
            if wanted is not None and wanted.issubset({o.remote_id for o in out}):
                break
        return out

    async def import_records(
        self,
        sheet_id: str,
        company_ids: Sequence[str] | None,
        contact_ids: Sequence[str] | None,
    ) -> ImportResponse:
        """HubSpot → Outmate: companies become rows, contacts become
        snapshot links (plan §3.2, §6)."""
        if await self._sheets.get_sheet(sheet_id) is None:
            raise SheetError(code="not_found", message="sheet not found")
        client = self.require_client()
        token = await self._access_token()

        companies = await self._fetch(client, token, "company", company_ids, _COMPANY_PROPERTIES)
        created, skipped, linked_companies = await self._import_companies(sheet_id, companies)
        contacts = await self._fetch(client, token, "contact", contact_ids, _CONTACT_PROPERTIES)
        linked_contacts = await self._import_contacts(sheet_id, contacts)
        await store.touch_last_sync(self._db, _PROVIDER, _utcnow())
        return ImportResponse(
            created=created,
            skipped=skipped,
            linked_companies=linked_companies,
            linked_contacts=linked_contacts,
        )

    async def _import_companies(
        self, sheet_id: str, companies: Sequence[RemoteObject]
    ) -> tuple[int, int, int]:
        """Returns ``(created, skipped, linked_companies)``.

        - already-linked remote ids: snapshot refreshed in place,
          binding untouched, counted ``skipped``;
        - domain matching an existing row's ``compare_key``: link
          attaches to that row, no twin created — ``skipped`` + linked;
        - the rest go through ``SheetService.import_rows`` in
          ``MAX_ROWS_PER_IMPORT`` batches, then get their link.
        """
        rows_by_key = await self._rows_by_compare_key(sheet_id)
        skipped = 0
        linked = 0
        pending: list[tuple[RemoteObject, RowInput, str | None]] = []
        for remote in companies:
            existing = await store.get_link(self._db, _PROVIDER, "company", remote.remote_id)
            if existing is not None:
                # Snapshot refresh only — the link (and its sheet/row
                # binding, possibly another sheet's) is never rewritten.
                await store.update_link_snapshot(
                    self._db, _PROVIDER, "company", remote.remote_id, remote.properties, None
                )
                skipped += 1
                if existing.sheet_id == sheet_id:
                    linked += 1
                continue
            domain = _company_domain(remote.properties)
            name = remote.properties.get("name") or None
            if name is None and domain is None:
                skipped += 1  # nothing a row identity could be built from
                continue
            key = _compare_key(domain) if domain is not None else None
            if key is not None and key in rows_by_key:
                await self._link_company(sheet_id, remote, rows_by_key[key].id)
                linked += 1
                skipped += 1
                continue
            pending.append(
                (
                    remote,
                    RowInput(
                        input_name=name,
                        input_domain=domain.host if domain is not None else None,
                    ),
                    key,
                )
            )

        created_rows: list[Row] = []
        for i in range(0, len(pending), MAX_ROWS_PER_IMPORT):
            created_rows.extend(
                await self._sheets.import_rows(
                    sheet_id, [inp for _, inp, _ in pending[i : i + MAX_ROWS_PER_IMPORT]]
                )
            )
        # Map each imported company onto its row by replaying import_rows'
        # dedupe: a pending item consumes a created row unless its
        # compare_key was already claimed by an earlier pending sibling —
        # then it links to that sibling's row.
        row_for_key: dict[str, str] = {}
        created_iter = iter(created_rows)
        for remote, _inp, key in pending:
            if key is not None and key in row_for_key:
                row_id = row_for_key[key]
            else:
                row = next(created_iter)
                row_id = row.id
                if key is not None:
                    row_for_key[key] = row_id
            await self._link_company(sheet_id, remote, row_id)
            linked += 1
        created = len(created_rows)
        return created, skipped + len(pending) - created, linked

    async def _import_contacts(self, sheet_id: str, contacts: Sequence[RemoteObject]) -> int:
        """Contacts are snapshot-only links — ``row_id`` NULL (plan §4,
        contacts decision); re-importing refreshes the snapshot."""
        for remote in contacts:
            await store.upsert_link(
                self._db,
                IntegrationLink(
                    provider=_PROVIDER,
                    object_type="contact",
                    remote_id=remote.remote_id,
                    sheet_id=sheet_id,
                    row_id=None,
                    properties=dict(remote.properties),
                    remote_updated_at=None,
                    created_at=_utcnow(),
                ),
            )
        return len(contacts)

    async def _link_company(self, sheet_id: str, remote: RemoteObject, row_id: str) -> None:
        await store.upsert_link(
            self._db,
            IntegrationLink(
                provider=_PROVIDER,
                object_type="company",
                remote_id=remote.remote_id,
                sheet_id=sheet_id,
                row_id=row_id,
                properties=dict(remote.properties),
                remote_updated_at=None,
                created_at=_utcnow(),
            ),
        )

    async def _rows_by_compare_key(self, sheet_id: str) -> dict[str, Row]:
        """Live rows indexed by normalized ``input_domain`` compare_key —
        the same dedupe key ``SheetService.import_rows`` enforces."""
        out: dict[str, Row] = {}
        for row in await repo.list_rows(self._db, sheet_id):
            raw = row.input_domain
            if not raw:
                continue
            try:
                key = normalize_domain(raw).compare_key
            except DomainError:
                continue
            out.setdefault(key, row)
        return out

    # --- push (plan §3.4) ----------------------------------------------------

    async def push(self, sheet_id: str, approvals: Sequence[PushApproval]) -> list[PushItemResult]:
        """Outmate → HubSpot: one PATCH per approved item (plan §3.4).

        Per item: the row must exist in the sheet (``row_not_found``),
        a company link must exist for it (``not_linked``), the cell must
        be ``done`` with a non-null value (``no_value`` — unavailable
        cells are never pushed), and the property must be in
        :data:`ALLOWED_PUSH_PROPERTIES` (``property_not_allowed``).
        Every attempted write is audit-logged."""
        client = self.require_client()
        token = await self._access_token()
        row_ids = {row.id for row in await repo.list_rows(self._db, sheet_id)}
        results: list[PushItemResult] = []

        def item(
            row_id: str,
            column_id: str,
            prop: str,
            status: Literal["pushed", "skipped", "error"],
            detail: str | None,
        ) -> PushItemResult:
            return PushItemResult(
                row_id=row_id, column_id=column_id, property=prop, status=status, detail=detail
            )

        for approval in approvals:
            rid, cid, prop = approval.row_id, approval.column_id, approval.property
            if rid not in row_ids:
                results.append(item(rid, cid, prop, "error", "row_not_found"))
                continue
            link = await store.link_for_row(self._db, _PROVIDER, "company", rid)
            if link is None:
                results.append(item(rid, cid, prop, "skipped", "not_linked"))
                continue
            cell = await repo.get_cell(self._db, rid, cid)
            value = cell.result.value if cell is not None and cell.result is not None else None
            if cell is None or cell.status != "done" or value is None:
                results.append(item(rid, cid, prop, "skipped", "no_value"))
                continue
            try:
                ensure_push_property(prop)  # the overwrite guard
            except IntegrationError:
                results.append(item(rid, cid, prop, "error", "property_not_allowed"))
                continue
            serialized = serialize_value(value)
            try:
                await client.patch_object(token, "company", link.remote_id, {prop: serialized})
            except IntegrationError as exc:
                await store.append_push_log(
                    self._db,
                    self._push_log(rid, link.remote_id, prop, serialized, "error", exc.code),
                )
                results.append(item(rid, cid, prop, "error", exc.code))
                continue
            await store.append_push_log(
                self._db,
                self._push_log(rid, link.remote_id, prop, serialized, "ok", None),
            )
            results.append(item(rid, cid, prop, "pushed", None))
        return results

    def _push_log(
        self,
        row_id: str,
        remote_id: str,
        prop: str,
        value: str,
        status: Literal["ok", "error"],
        error: str | None,
    ) -> PushLogEntry:
        return PushLogEntry(
            provider=_PROVIDER,
            row_id=row_id,
            remote_id=remote_id,
            property=prop,
            value=value,
            status=status,
            error=error,
            created_at=_utcnow(),
        )

    # --- refresh (plan §3.5) -------------------------------------------------

    async def refresh(self, sheet_id: str) -> list[RefreshChange]:
        """Pull remote changes: diff each linked company's remote
        properties against the stored snapshot, update the snapshot and
        ``last_sync_at`` — cells are NEVER modified (plan §3.5)."""
        client = self.require_client()
        links = [
            link
            for link in await store.links_for_sheet(self._db, _PROVIDER, sheet_id)
            if link.object_type == "company"
        ]
        if not links:
            return []
        token = await self._access_token()
        properties = sorted({key for link in links for key in link.properties})
        remotes = await client.batch_read(
            token, "company", [link.remote_id for link in links], properties
        )
        by_remote_id = {link.remote_id: link for link in links}
        changes: list[RefreshChange] = []
        for remote in remotes:
            link = by_remote_id.get(remote.remote_id)
            if link is None:
                continue
            for key in set(remote.properties) | set(link.properties):
                remote_value = remote.properties.get(key)
                previous = link.properties.get(key)
                if remote_value != previous:
                    changes.append(
                        RefreshChange(
                            row_id=link.row_id,
                            remote_id=remote.remote_id,
                            property=key,
                            remote_value=remote_value,
                            previous=previous,
                        )
                    )
            await store.update_link_snapshot(
                self._db, _PROVIDER, "company", remote.remote_id, remote.properties, None
            )
        await store.touch_last_sync(self._db, _PROVIDER, _utcnow())
        return changes


__all__ = [
    "ALLOWED_PUSH_PROPERTIES",
    "IntegrationService",
    "build_client",
]
