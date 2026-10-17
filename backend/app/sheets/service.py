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

from app.engine.graph import RESERVED_REFS, ColumnGraph, TemplateError
from app.errors.sheet_error import SheetError
from app.normalize.domain import DomainError, normalize_domain
from app.sheets import repo
from app.sheets.models import (
    Column,
    Depth,
    OutputType,
    Row,
    RowInput,
    Sheet,
    SourceScope,
)

MAX_ROWS_PER_IMPORT = 100


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
        if await repo.get_sheet(self._db, sheet_id) is None:
            raise SheetError(code="not_found", message="sheet not found")
        columns = await repo.list_columns(self._db, sheet_id)
        if len(columns) >= self._max_columns:
            raise SheetError(
                code="column_limit",
                message=f"a sheet holds at most {self._max_columns} columns",
            )
        clean_name = _checked_name(name, columns)
        _checked_enum(output_type, enum_options)
        templates = {column.name: column.prompt_template for column in columns}
        templates[clean_name] = prompt_template
        graph = _build_graph(templates)
        column = await repo.create_column(
            self._db,
            sheet_id,
            name=clean_name,
            prompt_template=prompt_template,
            output_type=output_type,
            source_scope=source_scope,
            depth=depth,
            enum_options=list(enum_options) if enum_options is not None else None,
            depends_on=graph.dependencies(clean_name),
        )
        for row in await repo.list_rows(self._db, sheet_id):
            await repo.upsert_cell(self._db, row.id, column.id)
        return column

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
