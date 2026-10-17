"""Business rules for sheets, enforced server-side (plan §4.3, §4.5).

- Columns are append-only with unique names; ``{{Company}}``/``{{Website}}``
  are reserved and can never be column names.
- ``depends_on`` is ALWAYS recomputed from the prompt template — caller
  input is never trusted; unknown references, self-references and cycles
  are rejected at write time.
- Rename is refused while another column references the old name; delete
  is refused while dependents exist.
- Limits: ``sheet_max_rows`` rows per sheet, ``sheet_max_columns`` columns,
  at most 100 rows per import call.
- Row imports dedupe by ``normalize_domain(raw).compare_key``.
- A prompt edit marks the column's cells — and transitively dependent
  columns' cells — stale, and never wipes a value.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.engine.budget import ESTIMATED_SEARCH_COST_USD
from app.engine.cell_exec import result_cost
from app.engine.graph import RESERVED_REFS, ColumnGraph, TemplateError
from app.errors.sheet_error import SheetError
from app.normalize.domain import DomainError, normalize_domain
from app.providers.types import ExaProvider
from app.research.rows_query import generate_rows_request
from app.sheets import repo
from app.sheets.models import (
    Cell,
    CellRevision,
    Column,
    Depth,
    OutputType,
    Row,
    RowInput,
    Run,
    Sheet,
    SheetSnapshot,
    SourceScope,
)

MAX_ROWS_PER_IMPORT = 100


@dataclass(frozen=True)
class ColumnSpec:
    """One column write passed to :meth:`SheetService.add_columns`.

    ``strategy`` and ``depends_on`` are server-owned — the strategy is
    always ``"research"`` and dependencies are recomputed from
    ``prompt_template`` here, never accepted from a caller.
    """

    name: str
    prompt_template: str
    output_type: OutputType
    source_scope: SourceScope
    depth: Depth = "standard"
    enum_options: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeneratedRows:
    """Outcome of one list-building generation call (P9a).

    ``estimated_cost_usd`` is the labelled estimate (the documented
    ``/search`` upper bound); ``cost_usd`` is provider-reported spend only
    — ``None`` when the result carried no ``costDollars.total``, never
    invented. ``mock`` is provenance, carried through verbatim.
    """

    rows: list[Row]
    skipped: int
    estimated_cost_usd: float
    cost_usd: float | None
    mock: bool


class SheetService:
    """The validated write path for sheets; limits are injectable for tests."""

    def __init__(
        self,
        db_path: str,
        *,
        max_rows: int = 200,
        max_columns: int = 30,
        max_import_rows: int = MAX_ROWS_PER_IMPORT,
    ) -> None:
        self._db = db_path
        self._max_rows = max_rows
        self._max_columns = max_columns
        self._max_import_rows = max_import_rows

    async def create_sheet(self, name: str) -> Sheet:
        return await repo.create_sheet(self._db, name)

    async def get_sheet(self, sheet_id: str) -> Sheet | None:
        return await repo.get_sheet(self._db, sheet_id)

    async def list_sheets(self) -> list[Sheet]:
        return await repo.list_sheets(self._db)

    async def snapshot(self, sheet_id: str) -> SheetSnapshot:
        snap = await repo.snapshot(self._db, sheet_id)
        if snap is None:
            raise SheetError(code="not_found", message="sheet not found")
        return snap

    async def delete_sheet(self, sheet_id: str) -> None:
        if not await repo.delete_sheet(self._db, sheet_id):
            raise SheetError(code="not_found", message="sheet not found")

    async def run_for_sheet(self, sheet_id: str, run_id: str) -> Run:
        run = await repo.get_run(self._db, run_id)
        if run is None or run.sheet_id != sheet_id:
            raise SheetError(code="not_found", message="run not found")
        return run

    async def cancel_run(self, run_id: str) -> Run:
        """Flag a run cancelled; ``run_for_sheet`` verified it exists."""
        run = await repo.cancel_run(self._db, run_id)
        assert run is not None
        return run

    async def column_for_sheet(self, sheet_id: str, column_id: str) -> Column:
        """A live column scoped to its sheet — 404 on any mismatch."""
        column = await repo.get_column(self._db, column_id)
        if column is None or column.deleted or column.sheet_id != sheet_id:
            raise SheetError(code="not_found", message="column not found")
        return column

    async def cell_detail(
        self, sheet_id: str, row_id: str, column_id: str
    ) -> tuple[Cell, list[CellRevision]]:
        """One cell plus its revision history, sheet-scoped — 404 on any
        mismatch (a wrong-sheet id is indistinguishable from missing)."""
        if await repo.get_sheet(self._db, sheet_id) is None:
            raise SheetError(code="not_found", message="sheet not found")
        row = next(
            (row for row in await repo.list_rows(self._db, sheet_id) if row.id == row_id),
            None,
        )
        column = await repo.get_column(self._db, column_id)
        cell = await repo.get_cell(self._db, row_id, column_id)
        if (
            row is None
            or column is None
            or column.deleted
            or column.sheet_id != sheet_id
            or cell is None
        ):
            raise SheetError(code="not_found", message="cell not found")
        revisions = await repo.list_cell_revisions(self._db, row_id, column_id)
        return cell, revisions

    # -- rows ----------------------------------------------------------------

    async def import_rows(self, sheet_id: str, inputs: Sequence[RowInput]) -> list[Row]:
        """Bulk-import rows, deduped by normalized domain compare_key.

        Rows whose domain duplicates an existing live row — or an earlier
        row in the same batch — are skipped. Returns the created rows.
        """
        if await repo.get_sheet(self._db, sheet_id) is None:
            raise SheetError(code="not_found", message="sheet not found")
        if len(inputs) > self._max_import_rows:
            raise SheetError(
                code="import_limit",
                message=f"at most {self._max_import_rows} rows per import",
            )
        existing = await repo.list_rows(self._db, sheet_id)
        seen = {key for row in existing if (key := _compare_key(row.input_domain)) is not None}
        accepted: list[RowInput] = []
        for row_input in inputs:
            key = _compare_key_or_raise(row_input.input_domain)
            if key is not None:
                if key in seen:
                    continue  # dedupe — silently skipped
                seen.add(key)
            accepted.append(row_input)
        if len(existing) + len(accepted) > self._max_rows:
            raise SheetError(
                code="row_limit",
                message=f"a sheet holds at most {self._max_rows} rows",
            )
        created = await repo.create_rows(self._db, sheet_id, accepted)
        columns = await repo.list_columns(self._db, sheet_id)
        for row in created:
            for column in columns:
                await repo.upsert_cell(self._db, row.id, column.id)
        return created

    async def generate_rows(
        self,
        exa: ExaProvider,
        sheet_id: str,
        query: str,
        max_results: int,
        cap: float | None = None,
    ) -> GeneratedRows:
        """List-building (P9a): ONE Exa ``category="company"`` search whose
        hits are imported exactly like pasted rows — ``input_domain`` only
        (a name is NEVER guessed from a hit title), ``identity_status``
        "pending", resolved inside the next user-chosen run.

        Nothing auto-runs: generation creates idle cells only and no
        ``runs`` row exists — the search spend travels in this response.
        The cap is enforced BEFORE the call against the labelled estimate,
        so a request that could spend past the cap is refused outright.
        """
        if await repo.get_sheet(self._db, sheet_id) is None:
            raise SheetError(code="not_found", message="sheet not found")
        if cap is not None and ESTIMATED_SEARCH_COST_USD > cap:
            raise SheetError(
                code="cap_exceeded",
                message=(
                    f"the labelled estimate ${ESTIMATED_SEARCH_COST_USD:.3f} for one "
                    f"company search exceeds the cap ${cap:.2f}; the call was not made"
                ),
            )
        result = await exa.search(generate_rows_request(query, max_results))
        inputs: list[RowInput] = []
        for hit in result.hits:
            if not hit.url:
                continue  # a hit without a URL cannot become a row
            try:
                host = normalize_domain(hit.url).host
            except DomainError:
                continue  # unparseable hit URL — honestly skipped
            inputs.append(RowInput(input_domain=host))
        # Truncate to the remaining capacity — the same sheet cap
        # import_rows enforces — so a nearly-full sheet gets the rows that
        # fit instead of a whole-call rejection.
        existing = await repo.list_rows(self._db, sheet_id)
        capacity = max(0, self._max_rows - len(existing))
        created = await self.import_rows(sheet_id, inputs[:capacity])
        return GeneratedRows(
            rows=created,
            # Honest accounting: hits that did not become rows (no/invalid
            # URL, duplicate domain, over-cap truncation).
            skipped=len(result.hits) - len(created),
            estimated_cost_usd=ESTIMATED_SEARCH_COST_USD,
            cost_usd=result_cost(result),
            mock=result.mock,
        )

    # -- columns -------------------------------------------------------------

    async def add_column(
        self,
        sheet_id: str,
        *,
        name: str,
        prompt_template: str,
        output_type: OutputType,
        source_scope: SourceScope,
        depth: Depth = "standard",
        enum_options: Sequence[str] | None = None,
    ) -> Column:
        """Append one column. ``depends_on`` is derived from the template."""
        columns = await self.add_columns(
            sheet_id,
            [
                ColumnSpec(
                    name=name,
                    prompt_template=prompt_template,
                    output_type=output_type,
                    source_scope=source_scope,
                    depth=depth,
                    enum_options=tuple(enum_options) if enum_options else (),
                )
            ],
        )
        return columns[0]

    async def add_columns(self, sheet_id: str, specs: Sequence[ColumnSpec]) -> list[Column]:
        """Append a batch of columns atomically.

        Every spec is validated against the sheet AND its siblings —
        unique unreserved names, the column cap, enum options, and one
        dependency graph over existing + new templates — BEFORE any column
        is written, so a batch never half-applies at the rule level. A
        later spec may reference an earlier new column by name.
        """
        if not specs:
            raise SheetError(code="invalid_column", message="at least one column is required")
        if await repo.get_sheet(self._db, sheet_id) is None:
            raise SheetError(code="not_found", message="sheet not found")
        columns = await repo.list_columns(self._db, sheet_id)
        if len(columns) + len(specs) > self._max_columns:
            raise SheetError(
                code="column_limit",
                message=f"a sheet holds at most {self._max_columns} columns",
            )
        templates = {column.name: column.prompt_template for column in columns}
        clean_names: list[str] = []
        for spec in specs:
            clean_name = _checked_name(spec.name, columns)
            if clean_name in clean_names:
                raise SheetError(
                    code="name_conflict",
                    message=f"a column named {clean_name!r} already exists",
                )
            _checked_enum(spec.output_type, spec.enum_options)
            clean_names.append(clean_name)
            templates[clean_name] = spec.prompt_template
        graph = _build_graph(templates)
        rows = await repo.list_rows(self._db, sheet_id)
        created: list[Column] = []
        for spec, clean_name in zip(specs, clean_names, strict=True):
            column = await repo.create_column(
                self._db,
                sheet_id,
                name=clean_name,
                prompt_template=spec.prompt_template,
                output_type=spec.output_type,
                source_scope=spec.source_scope,
                depth=spec.depth,
                enum_options=list(spec.enum_options) if spec.enum_options else None,
                depends_on=graph.dependencies(clean_name),
            )
            for row in rows:
                await repo.upsert_cell(self._db, row.id, column.id)
            created.append(column)
        return created

    async def edit_column(
        self,
        column_id: str,
        *,
        name: str,
        prompt_template: str,
        output_type: OutputType,
        source_scope: SourceScope,
        depth: Depth = "standard",
        enum_options: Sequence[str] | None = None,
    ) -> Column:
        """Full-column update. A prompt edit marks the column's cells —
        and every transitively dependent column's cells — stale without
        wiping values. Rename is refused while another column references
        the old name."""
        column = await repo.get_column(self._db, column_id)
        if column is None or column.deleted:
            raise SheetError(code="not_found", message="column not found")
        columns = await repo.list_columns(self._db, column.sheet_id)
        others = [c for c in columns if c.id != column_id]
        clean_name = name.strip()
        renaming = clean_name != column.name
        if renaming:
            _checked_name(clean_name, others)
            referencing = [c.name for c in others if column.name in c.depends_on]
            if referencing:
                raise SheetError(
                    code="column_referenced",
                    message=(
                        f"cannot rename {column.name!r}: referenced by " + ", ".join(referencing)
                    ),
                )
        _checked_enum(output_type, enum_options)
        templates = {c.name: c.prompt_template for c in others}
        templates[clean_name] = prompt_template
        graph = _build_graph(templates)
        updated = await repo.update_column(
            self._db,
            column_id,
            name=clean_name,
            prompt_template=prompt_template,
            output_type=output_type,
            enum_options=list(enum_options) if enum_options is not None else None,
            source_scope=source_scope,
            depth=depth,
            depends_on=graph.dependencies(clean_name),
        )
        assert updated is not None
        dependent_ids = [
            c.id for c in others if c.name in set(graph.transitive_dependents(clean_name))
        ]
        if dependent_ids:
            await repo.mark_cells_stale(self._db, dependent_ids)
        return updated

    async def delete_column(self, column_id: str) -> None:
        """Soft-delete a column; refused while dependents exist."""
        column = await repo.get_column(self._db, column_id)
        if column is None or column.deleted:
            raise SheetError(code="not_found", message="column not found")
        columns = await repo.list_columns(self._db, column.sheet_id)
        dependents = [c.name for c in columns if c.id != column_id and column.name in c.depends_on]
        if dependents:
            raise SheetError(
                code="has_dependents",
                message=(f"cannot delete {column.name!r}: referenced by " + ", ".join(dependents)),
            )
        await repo.soft_delete_column(self._db, column_id)


def _compare_key(raw_domain: str | None) -> str | None:
    if not raw_domain:
        return None
    try:
        return normalize_domain(raw_domain).compare_key
    except DomainError:
        return None


def _compare_key_or_raise(raw_domain: str | None) -> str | None:
    if not raw_domain:
        return None
    try:
        return normalize_domain(raw_domain).compare_key
    except DomainError as exc:
        raise SheetError(
            code="invalid_domain",
            message=f"{raw_domain!r} is not a valid company domain",
        ) from exc


def _checked_name(name: str, existing: Sequence[Column]) -> str:
    clean = name.strip()
    if not clean:
        raise SheetError(code="invalid_column", message="column name is required")
    if clean in RESERVED_REFS:
        raise SheetError(
            code="reserved_name",
            message=f"{clean!r} is reserved for the row identity",
        )
    if any(column.name == clean for column in existing):
        raise SheetError(
            code="name_conflict",
            message=f"a column named {clean!r} already exists",
        )
    return clean


def _checked_enum(output_type: OutputType, enum_options: Sequence[str] | None) -> None:
    if output_type == "enum" and not enum_options:
        raise SheetError(
            code="invalid_column",
            message="an enum column needs at least one option",
        )


def _build_graph(templates: dict[str, str]) -> ColumnGraph:
    try:
        return ColumnGraph(templates)
    except TemplateError as exc:
        raise SheetError(code="invalid_template", message=exc.message) from exc
