"""Sheets API end-to-end tests (P5, plan §4.5).

``httpx.AsyncClient`` over the ASGI app for request/response endpoints;
a hand-rolled ASGI client for the SSE stream — httpx's ``ASGITransport``
buffers the whole response body, so it can never prove streaming, while
driving ``app(scope, receive, send)`` exercises the FULL middleware
stack including ``request_context`` (the ``BaseHTTPMiddleware`` that U6
targets).

Providers are in-memory fakes — the fixture-backed mocks are broken (the
fixture files were never committed). No network anywhere.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import sse_starlette.sse as _sse
from conftest import FakeExa, identity_result, structured_result, utcnow
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.types import ASGIApp, Message, Scope

from app.config import Settings
from app.engine.limiter import RateLimiter
from app.engine.runner import SheetRunner
from app.main import create_app
from app.providers.types import ExaSearchRequest, ExaSearchResult, LlmRequest, LlmResult


@pytest.fixture(autouse=True)
def _no_sse_shutdown_watcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """sse-starlette polls a process-level shutdown watcher on the running
    loop; under pytest it would leak one stray task per test loop."""
    monkeypatch.setattr(_sse, "_ensure_watcher_started_on_this_loop", lambda: None)


def _settings(tmp_path: Path) -> Settings:
    # Keys are forced None and modes pinned so the developer's real .env
    # can never leak in.
    return Settings(
        exa_api_key=None,
        exa_mode="live",
        groq_api_key=None,
        groq_mode="live",
        moltsets_api_key=None,
        moltsets_mode="off",
        sheets_db_path=str(tmp_path / "sheets.db"),
    )


class FakeLlm:
    """In-memory ``LlmProvider`` returning a canned parsed plan."""

    def __init__(self, content: Any) -> None:
        self._content = content
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResult:
        self.requests.append(request)
        return LlmResult(content=self._content, calls=1, mock=True)


Handler = Callable[[ExaSearchRequest], Awaitable[ExaSearchResult]]


async def _identity_aware(request: ExaSearchRequest) -> ExaSearchResult:
    """Resolve every row to a hit on its own domain; cells get a
    synthetic structured answer (documented output shape)."""
    if request.output_schema is not None:
        return structured_result(value="synthetic answer")
    domain = (request.include_domains or ["acme.test"])[0]
    return identity_result(f"https://{domain}/", "Acme Inc")


@asynccontextmanager
async def _app_client(
    tmp_path: Path, handler: Handler
) -> AsyncIterator[tuple[FastAPI, AsyncClient, FakeExa]]:
    """The app inside its lifespan with in-memory providers substituted.

    The lifespan creates the sheets DB, service, hubs and runner from
    ``build_providers``; tests then swap Exa for a fake and rebuild the
    runner on the SAME hub registry — the pattern ``app.state.providers``
    / dependency overrides already allow.
    """
    settings = _settings(tmp_path)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        fake = FakeExa(handler)
        app.state.providers.exa = fake
        app.state.runner = SheetRunner(
            settings.sheets_db_path,
            fake,
            hubs=app.state.hubs,
            limiter=RateLimiter(1000),
            clock=utcnow,
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client, fake


class SseConnection:
    """A raw ASGI client for streaming endpoints.

    Feeds the app one ``http.request`` message, then relays
    ``http.disconnect`` on demand. Incoming ``http.response.*`` messages
    are queued for the test — proving events flow (U6) rather than being
    buffered by the ``BaseHTTPMiddleware``.
    """

    def __init__(self, app: ASGIApp, path: str) -> None:
        self._app = app
        self._path = path
        self._incoming: asyncio.Queue[Message] = asyncio.Queue()
        self._outgoing: asyncio.Queue[Message] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def _receive(self) -> Message:
        return await self._incoming.get()

    async def _send(self, message: Message) -> None:
        await self._outgoing.put(message)

    async def __aenter__(self) -> "SseConnection":
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": self._path,
            "raw_path": self._path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"test"),
                (b"accept", b"text/event-stream"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
            "app": self._app,
        }
        self._task = asyncio.create_task(self._app(scope, self._receive, self._send))
        await self._incoming.put({"type": "http.request", "body": b"", "more_body": False})
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._task is None or self._task.done():
            if self._task is not None:
                self._task.result()  # surface an app-side failure
            return
        await self.disconnect()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=5)
        except (TimeoutError, asyncio.CancelledError) as exc:
            self._task.cancel()
            raise AssertionError("SSE connection did not close on disconnect") from exc
        if self._task.done() and not self._task.cancelled():
            self._task.result()  # surface an app-side failure

    async def next_message(self, timeout: float = 5.0) -> Message:
        return await asyncio.wait_for(self._outgoing.get(), timeout)

    async def next_event(self, timeout: float = 5.0) -> list[dict[str, Any]]:
        """The next SSE frame's ``data:`` payload — a JSON event array."""
        while True:
            message = await self.next_message(timeout)
            assert message["type"] == "http.response.body"
            body = bytes(message.get("body", b""))
            for line in body.decode().splitlines():
                if line.startswith("data:"):
                    payload = json.loads(line[len("data:") :].strip())
                    assert isinstance(payload, list)
                    return payload

    async def disconnect(self) -> None:
        await self._incoming.put({"type": "http.disconnect"})


def _plan(*columns: tuple[str, str]) -> dict[str, Any]:
    """A Groq-shaped ``add_columns`` payload (parsed content)."""
    return {
        "intent": "add_columns",
        "columns": [
            {
                "name": name,
                "prompt_template": template,
                "output_type": "text",
                "enum_options": [],
                "source_scope": "web",
                "depth": "standard",
                "depends_on": [],
            }
            for name, template in columns
        ],
        "question": "",
        "reason": "",
    }


def _spec(name: str, template: str, **overrides: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": name,
        "prompt_template": template,
        "output_type": "text",
        "enum_options": [],
        "source_scope": "web",
        "depth": "standard",
    }
    spec.update(overrides)
    return spec


async def _new_sheet(client: AsyncClient, rows: list[dict[str, str]]) -> str:
    created = await client.post("/api/sheets", json={"name": "targets"})
    assert created.status_code == 201
    sheet_id = created.json()["id"]
    if rows:
        imported = await client.post(f"/api/sheets/{sheet_id}/rows", json={"rows": rows})
        assert imported.status_code == 201
    return sheet_id


_TERMINAL = {"done", "error", "blocked"}


async def _wait_cells_settled(client: AsyncClient, sheet_id: str) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + 15
    while True:
        snap = (await client.get(f"/api/sheets/{sheet_id}/snapshot")).json()
        if all(cell["status"] in _TERMINAL for cell in snap["cells"]):
            return snap
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"cells did not settle: {snap['cells']!r}")
        await asyncio.sleep(0.02)


async def _wait_cell_status(client: AsyncClient, sheet_id: str, status: str) -> dict[str, Any]:
    """The first cell reaching ``status`` — used to cancel only once the
    worker has CLAIMED the cell, not merely queued it (a queued cell goes
    back to idle on cancel by design)."""
    deadline = asyncio.get_running_loop().time() + 15
    while True:
        snap = (await client.get(f"/api/sheets/{sheet_id}/snapshot")).json()
        for cell in snap["cells"]:
            if cell["status"] == status:
                return cell
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"no cell reached {status!r}: {snap['cells']!r}")
        await asyncio.sleep(0.02)


async def _wait_run_over(app: FastAPI, sheet_id: str) -> None:
    """Cells settle before the run context unregisters — callers that
    start another run must wait for ``_active`` to clear, not just for
    terminal cell statuses."""
    for _ in range(300):
        runner = app.state.runner
        if runner is None or runner.active_run_for(sheet_id) is None:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"run still active for sheet {sheet_id}")


# --- sheet / row CRUD ----------------------------------------------------------


async def test_sheet_crud_and_error_shape(tmp_path: Path) -> None:
    async with _app_client(tmp_path, _identity_aware) as (app, client, _):
        created = await client.post("/api/sheets", json={"name": "a"})
        assert created.status_code == 201
        assert created.headers["x-request-id"]
        sheet_id = created.json()["id"]

        listed = await client.get("/api/sheets")
        assert [s["id"] for s in listed.json()["sheets"]] == [sheet_id]

        snap = await client.get(f"/api/sheets/{sheet_id}/snapshot")
        assert snap.status_code == 200
        assert snap.json()["sheet"]["name"] == "a"
        assert snap.json()["cells"] == []

        missing = await client.get("/api/sheets/nope/snapshot")
        assert missing.status_code == 404
        error = missing.json()["error"]
        assert error["code"] == "not_found"
        assert error["request_id"]

        deleted = await client.delete(f"/api/sheets/{sheet_id}")
        assert deleted.status_code == 204
        assert (await client.get(f"/api/sheets/{sheet_id}/snapshot")).status_code == 404


async def test_rows_import_dedupes_by_domain(tmp_path: Path) -> None:
    async with _app_client(tmp_path, _identity_aware) as (app, client, _):
        sheet_id = (await client.post("/api/sheets", json={"name": "s"})).json()["id"]
        imported = await client.post(
            f"/api/sheets/{sheet_id}/rows",
            json={
                "rows": [
                    {"name": "Acme", "domain": "acme.test"},
                    {"name": "Acme dup", "domain": "https://www.acme.test/path"},
                    {"name": "Globex", "domain": "globex.test"},
                ]
            },
        )
        assert imported.status_code == 201
        assert (imported.json()["created"], imported.json()["skipped"]) == (2, 1)


async def test_rows_import_limit_is_100(tmp_path: Path) -> None:
    async with _app_client(tmp_path, _identity_aware) as (app, client, _):
        sheet_id = (await client.post("/api/sheets", json={"name": "s"})).json()["id"]
        resp = await client.post(
            f"/api/sheets/{sheet_id}/rows",
            json={"rows": [{"name": f"Co{i}"} for i in range(101)]},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


# --- the full P5 flow ----------------------------------------------------------


async def test_full_flow_plan_apply_events_cells_fill(tmp_path: Path) -> None:
    async with _app_client(tmp_path, _identity_aware) as (app, client, exa):
        app.state.providers.groq = FakeLlm(_plan(("Industry", "industry of {{Company}}")))
        sheet_id = await _new_sheet(
            client,
            [
                {"name": "Co0", "domain": "co0.test"},
                {"name": "Co1", "domain": "co1.test"},
                {"name": "Co2", "domain": "co2.test"},
            ],
        )

        planned = await client.post(
            f"/api/sheets/{sheet_id}/plan", json={"prompt": "add the industry"}
        )
        assert planned.status_code == 200
        result = planned.json()
        assert result["intent"] == "add_columns"
        assert result["columns"][0]["name"] == "Industry"
        assert result["estimated_cost_usd"] is not None  # labelled estimate

        # Client protocol: subscribe FIRST, then snapshot, then merge.
        async with SseConnection(app, f"/api/sheets/{sheet_id}/events") as sse:
            start = await sse.next_message()
            assert start["type"] == "http.response.start"
            assert start["status"] == 200
            headers = {k.decode(): v.decode() for k, v in start["headers"]}
            assert headers["content-type"].startswith("text/event-stream")
            assert headers["cache-control"] == "no-store"  # request_context ran

            applied = await client.post(
                f"/api/sheets/{sheet_id}/columns",
                json={
                    "columns": [
                        {
                            "name": c["name"],
                            "prompt_template": c["prompt_template"],
                            "output_type": c["output_type"],
                            "enum_options": c["enum_options"],
                            "source_scope": c["source_scope"],
                            "depth": c["depth"],
                        }
                        for c in result["columns"]
                    ],
                    "run": True,
                },
            )
            assert applied.status_code == 201
            assert applied.json()["run"] is not None

            seen: list[dict[str, Any]] = []
            for _ in range(60):
                for event in await sse.next_event():
                    seen.append(event)
                if any(e.get("type") == "run" and e.get("status") == "finished" for e in seen):
                    break
            assert any(e["type"] == "run" and e["status"] == "started" for e in seen)
            assert any(e["type"] == "row" and e["identity_status"] == "resolved" for e in seen)
            done_cells = [e for e in seen if e["type"] == "cell" and e["status"] == "done"]
            assert len(done_cells) == 3  # 3 rows x 1 column, streamed live

        snap = await _wait_cells_settled(client, sheet_id)
        assert all(cell["status"] == "done" for cell in snap["cells"])
        assert all(cell["result"]["status"] == "verified" for cell in snap["cells"])
        assert all(cell["revision"] == 1 for cell in snap["cells"])
        assert exa.cell_requests()  # cells really ran through the fake provider


async def test_u6_sse_events_flow_through_basehttpmiddleware(tmp_path: Path) -> None:
    """U6 RESOLUTION: SSE events pass through ``request_context``.

    If ``BaseHTTPMiddleware`` buffered the stream, ``http.response.body``
    frames would not arrive until the connection closed — the bounded
    read below would time out instead of yielding live cell events while
    the response is still open.
    """
    async with _app_client(tmp_path, _identity_aware) as (app, client, _):
        sheet_id = await _new_sheet(client, [{"name": "Co0", "domain": "co0.test"}])
        applied = await client.post(
            f"/api/sheets/{sheet_id}/columns",
            json={"columns": [_spec("A", "about {{Company}}")]},
        )
        assert applied.status_code == 201

        async with SseConnection(app, f"/api/sheets/{sheet_id}/events") as sse:
            assert (await sse.next_message())["type"] == "http.response.start"
            run = await client.post(f"/api/sheets/{sheet_id}/runs", json={"scope": "empty"})
            assert run.status_code == 202

            events: list[dict[str, Any]] = []
            for _ in range(20):
                for event in await sse.next_event():
                    events.append(event)
                if any(e.get("type") == "run" and e.get("status") == "finished" for e in events):
                    break
            # Events arrived while the response was open — no buffering.
            assert any(e["type"] == "cell" for e in events)


async def test_sse_disconnect_cleans_up_queue(tmp_path: Path) -> None:
    async with _app_client(tmp_path, _identity_aware) as (app, client, _):
        sheet_id = await _new_sheet(client, [])
        hub = app.state.hubs.for_sheet(sheet_id)
        assert hub.subscriber_count == 0

        async with SseConnection(app, f"/api/sheets/{sheet_id}/events") as sse:
            assert (await sse.next_message())["type"] == "http.response.start"
            for _ in range(100):
                if hub.subscriber_count == 1:
                    break
                await asyncio.sleep(0.01)
            assert hub.subscriber_count == 1

            await sse.disconnect()
            for _ in range(150):
                if hub.subscriber_count == 0:
                    break
                await asyncio.sleep(0.02)
            assert hub.subscriber_count == 0  # queue unsubscribed, no leak


# --- append-only proof (A1) -----------------------------------------------------


async def test_append_only_new_column_leaves_cells_identical(tmp_path: Path) -> None:
    async with _app_client(tmp_path, _identity_aware) as (app, client, _):
        sheet_id = await _new_sheet(client, [{"name": "Co0", "domain": "co0.test"}])
        applied = await client.post(
            f"/api/sheets/{sheet_id}/columns",
            json={
                "columns": [
                    _spec("A", "about {{Company}}"),
                    _spec("B", "sum {{A}}"),
                    _spec("C", "more"),
                ],
                "run": True,
            },
        )
        assert applied.status_code == 201
        before_snap = await _wait_cells_settled(client, sheet_id)
        await _wait_run_over(app, sheet_id)
        before = {(c["row_id"], c["column_id"]): c for c in before_snap["cells"]}
        assert len(before) == 3

        added = await client.post(
            f"/api/sheets/{sheet_id}/columns",
            json={"columns": [_spec("D", "uses {{A}}")], "run": True},
        )
        assert added.status_code == 201
        after_snap = await _wait_cells_settled(client, sheet_id)
        await _wait_run_over(app, sheet_id)
        after = {(c["row_id"], c["column_id"]): c for c in after_snap["cells"]}
        assert len(after) == 4

        # Every pre-existing cell is byte-identical: value, status,
        # sources, revision, updated_at — the full serialized shape.
        for key, cell in before.items():
            assert key in after
            assert after[key] == cell
        new_key = next(iter(after.keys() - before.keys()))
        assert after[new_key]["status"] == "done"


# --- columns / runs rules --------------------------------------------------------


async def test_column_rename_and_delete_conflicts(tmp_path: Path) -> None:
    async with _app_client(tmp_path, _identity_aware) as (app, client, _):
        sheet_id = await _new_sheet(client, [])
        applied = await client.post(
            f"/api/sheets/{sheet_id}/columns",
            json={"columns": [_spec("A", "a"), _spec("B", "sum {{A}}")]},
        )
        col_a, col_b = applied.json()["columns"]

        renamed = await client.patch(
            f"/api/sheets/{sheet_id}/columns/{col_a['id']}",
            json=_spec("A2", "a"),
        )
        assert renamed.status_code == 409
        assert renamed.json()["error"]["code"] == "column_referenced"

        deleted = await client.delete(f"/api/sheets/{sheet_id}/columns/{col_a['id']}")
        assert deleted.status_code == 409
        assert deleted.json()["error"]["code"] == "has_dependents"

        ok = await client.delete(f"/api/sheets/{sheet_id}/columns/{col_b['id']}")
        assert ok.status_code == 204


async def test_run_scope_all_requires_confirm(tmp_path: Path) -> None:
    async with _app_client(tmp_path, _identity_aware) as (app, client, _):
        sheet_id = await _new_sheet(client, [{"name": "Co0", "domain": "co0.test"}])
        denied = await client.post(f"/api/sheets/{sheet_id}/runs", json={"scope": "all"})
        assert denied.status_code == 409
        assert denied.json()["error"]["code"] == "confirm_required"

        allowed = await client.post(
            f"/api/sheets/{sheet_id}/runs",
            json={"scope": "all", "confirm_overwrite": True},
        )
        assert allowed.status_code == 202
        await _wait_cells_settled(client, sheet_id)


async def test_second_run_conflicts_and_cancel(tmp_path: Path) -> None:
    gate = asyncio.Event()

    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is not None:
            await gate.wait()
        return await _identity_aware(request)

    async with _app_client(tmp_path, handler) as (app, client, _):
        sheet_id = await _new_sheet(client, [{"name": "Co0", "domain": "co0.test"}])
        await client.post(
            f"/api/sheets/{sheet_id}/columns",
            json={"columns": [_spec("A", "a")]},
        )
        run = await client.post(f"/api/sheets/{sheet_id}/runs", json={})
        assert run.status_code == 202
        run_id = run.json()["id"]

        # Wait until the cell is CLAIMED (worker parked in gate.wait()) —
        # a queued cell would go back to idle on cancel, not done.
        await _wait_cell_status(client, sheet_id, "running")

        second = await client.post(f"/api/sheets/{sheet_id}/runs", json={})
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "run_active"

        cancelled = await client.post(f"/api/sheets/{sheet_id}/runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        gate.set()  # let in-flight cells settle
        # stop_run writes cancelled=1 after the workers settle.
        deadline = asyncio.get_running_loop().time() + 15
        while True:
            snap = (await client.get(f"/api/sheets/{sheet_id}/snapshot")).json()
            final_run = snap["latest_run"]
            if final_run is not None and final_run["cancelled"]:
                break
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"run not cancelled: {final_run!r}")
            await asyncio.sleep(0.02)
        assert final_run["id"] == run_id


async def test_plan_503_when_groq_missing(tmp_path: Path) -> None:
    async with _app_client(tmp_path, _identity_aware) as (app, client, _):
        sheet_id = await _new_sheet(client, [])
        resp = await client.post(f"/api/sheets/{sheet_id}/plan", json={"prompt": "anything"})
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "groq_not_configured"


async def test_runs_503_when_exa_missing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        assert app.state.runner is None  # no key -> no runner, never a mock
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sheet_id = await _new_sheet(client, [])
            resp = await client.post(f"/api/sheets/{sheet_id}/runs", json={})
            assert resp.status_code == 503
            assert resp.json()["error"]["code"] == "exa_not_configured"


# --- cell detail + export ----------------------------------------------------------


async def test_cell_detail_and_csv_export(tmp_path: Path) -> None:
    async def handler(request: ExaSearchRequest) -> ExaSearchResult:
        if request.output_schema is not None:
            return structured_result(value="=1+1")  # formula-injection probe
        domain = (request.include_domains or ["acme.test"])[0]
        return identity_result(f"https://{domain}/", "Acme Inc")

    async with _app_client(tmp_path, handler) as (app, client, _):
        sheet_id = await _new_sheet(client, [{"name": "Co0", "domain": "co0.test"}])
        await client.post(
            f"/api/sheets/{sheet_id}/columns",
            json={"columns": [_spec("A", "a")], "run": True},
        )
        snap = await _wait_cells_settled(client, sheet_id)
        cell = snap["cells"][0]

        detail = await client.get(
            f"/api/sheets/{sheet_id}/cells/{cell['row_id']}/{cell['column_id']}"
        )
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["cell"]["result"]["value"] == "=1+1"
        assert payload["cell"]["result"]["sources"]
        assert payload["cell"]["rendered_prompt"]
        assert payload["cell"]["cost"] == pytest.approx(0.007)
        assert len(payload["revisions"]) == 1
        assert payload["revisions"][0]["revision"] == 1

        exported = await client.get(f"/api/sheets/{sheet_id}/export.csv")
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith("text/csv")
        lines = exported.text.strip().split("\n")
        assert lines[0].startswith("Company,Domain,A")
        assert "'=1+1" in lines[1]  # single-quote-prefixed


async def test_cell_detail_wrong_sheet_is_404(tmp_path: Path) -> None:
    async with _app_client(tmp_path, _identity_aware) as (app, client, _):
        sheet_id = await _new_sheet(client, [{"name": "Co0", "domain": "co0.test"}])
        other = await client.post("/api/sheets", json={"name": "other"})
        other_id = other.json()["id"]
        await client.post(
            f"/api/sheets/{sheet_id}/columns",
            json={"columns": [_spec("A", "a")]},
        )
        snap = (await client.get(f"/api/sheets/{sheet_id}/snapshot")).json()
        cell = snap["cells"][0]
        resp = await client.get(
            f"/api/sheets/{other_id}/cells/{cell['row_id']}/{cell['column_id']}"
        )
        assert resp.status_code == 404
