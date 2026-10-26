"""Sheet service business-rule tests (plan §4.3/§4.5)."""

import pytest
import pytest_asyncio

from app.errors.sheet_error import SheetError
from app.schemas.report import Field
from app.sheets import repo
from app.sheets.models import CellValue, RowInput
from app.sheets.service import SheetService


@pytest_asyncio.fixture
async def service(db: str) -> SheetService:
    return SheetService(db)


async def _column(service: SheetService, sheet_id: str, name: str, template: str = "p"):
    return await service.add_column(
        sheet_id,
        name=name,
        prompt_template=template,
        output_type="text",
        source_scope="web",
    )


# -- imports -------------------------------------------------------------------


async def test_import_dedupes_by_normalized_domain(db: str, service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    created = await service.import_rows(
        sheet.id,
        [
            RowInput(input_name="Acme", input_domain="acme.test"),
            RowInput(input_name="Acme Two", input_domain="www.acme.test"),
            RowInput(input_name="NameOnly"),
        ],
    )
    assert len(created) == 2  # www. collapsed onto the same compare_key
    again = await service.import_rows(
        sheet.id, [RowInput(input_name="Dup", input_domain="ACME.test")]
    )
    assert again == []  # already present — skipped, not duplicated
    assert len(await repo.list_rows(db, sheet.id)) == 2


async def test_import_rejects_invalid_domain(service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    with pytest.raises(SheetError) as exc:
        await service.import_rows(sheet.id, [RowInput(input_domain="not a domain !!")])
    assert exc.value.code == "invalid_domain"


async def test_import_limit_per_call(db: str) -> None:
    service = SheetService(db, max_import_rows=2)
    sheet = await service.create_sheet("s")
    with pytest.raises(SheetError) as exc:
        await service.import_rows(sheet.id, [RowInput(input_name=f"c{i}") for i in range(3)])
    assert exc.value.code == "import_limit"


async def test_row_limit(db: str) -> None:
    service = SheetService(db, max_rows=2, max_import_rows=10)
    sheet = await service.create_sheet("s")
    await service.import_rows(sheet.id, [RowInput(input_name="a"), RowInput(input_name="b")])
    with pytest.raises(SheetError) as exc:
        await service.import_rows(sheet.id, [RowInput(input_name="c")])
    assert exc.value.code == "row_limit"


async def test_imported_rows_get_cells_for_each_column(db: str, service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    column = await _column(service, sheet.id, "Col")
    rows = await service.import_rows(sheet.id, [RowInput(input_name="Acme")])
    cell = await repo.get_cell(db, rows[0].id, column.id)
    assert cell is not None and cell.status == "idle"


# -- column rules ----------------------------------------------------------------


async def test_add_column_creates_cells_and_derives_depends_on(
    db: str, service: SheetService
) -> None:
    sheet = await service.create_sheet("s")
    rows = await service.import_rows(sheet.id, [RowInput(input_name="Acme")])
    first = await _column(service, sheet.id, "A", "about {{Company}}")
    second = await _column(service, sheet.id, "B", "summarize {{A}}")
    assert first.depends_on == []
    assert second.depends_on == ["A"]
    assert await repo.get_cell(db, rows[0].id, second.id) is not None


async def test_column_names_are_unique(service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    await _column(service, sheet.id, "A")
    with pytest.raises(SheetError) as exc:
        await _column(service, sheet.id, "A")
    assert exc.value.code == "name_conflict"


async def test_reserved_names_rejected(service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    for name in ("Company", "Website"):
        with pytest.raises(SheetError) as exc:
            await _column(service, sheet.id, name)
        assert exc.value.code == "reserved_name"


async def test_unknown_and_self_references_rejected(service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    with pytest.raises(SheetError) as exc:
        await _column(service, sheet.id, "A", "{{Ghost}}")
    assert exc.value.code == "invalid_template"
    with pytest.raises(SheetError) as exc:
        await _column(service, sheet.id, "A", "{{A}}")
    assert exc.value.code == "invalid_template"


async def test_column_limit(db: str) -> None:
    service = SheetService(db, max_columns=1)
    sheet = await service.create_sheet("s")
    await _column(service, sheet.id, "A")
    with pytest.raises(SheetError) as exc:
        await _column(service, sheet.id, "B")
    assert exc.value.code == "column_limit"


async def test_enum_column_needs_options(service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    with pytest.raises(SheetError) as exc:
        await service.add_column(
            sheet.id,
            name="E",
            prompt_template="p",
            output_type="enum",
            source_scope="web",
        )
    assert exc.value.code == "invalid_column"


# -- edit / delete guards ---------------------------------------------------------


async def test_rename_blocked_when_referenced(service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    col_a = await _column(service, sheet.id, "A")
    await _column(service, sheet.id, "B", "{{A}} twice")
    with pytest.raises(SheetError) as exc:
        await service.edit_column(
            col_a.id,
            name="Renamed",
            prompt_template="p",
            output_type="text",
            source_scope="web",
        )
    assert exc.value.code == "column_referenced"


async def test_rename_allowed_when_unreferenced(service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    col = await _column(service, sheet.id, "A")
    updated = await service.edit_column(
        col.id,
        name="Renamed",
        prompt_template="p",
        output_type="text",
        source_scope="web",
    )
    assert updated.name == "Renamed"
    assert updated.version == 2


async def test_edit_introducing_cycle_rejected(service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    col_a = await _column(service, sheet.id, "A")
    await _column(service, sheet.id, "B", "{{A}}")
    with pytest.raises(SheetError) as exc:
        await service.edit_column(
            col_a.id,
            name="A",
            prompt_template="{{B}}",
            output_type="text",
            source_scope="web",
        )
    assert exc.value.code == "invalid_template"


async def test_delete_blocked_while_dependents_exist(service: SheetService) -> None:
    sheet = await service.create_sheet("s")
    col_a = await _column(service, sheet.id, "A")
    col_b = await _column(service, sheet.id, "B", "{{A}}")
    with pytest.raises(SheetError) as exc:
        await service.delete_column(col_a.id)
    assert exc.value.code == "has_dependents"
    await service.delete_column(col_b.id)  # leaf deletes fine
    await service.delete_column(col_a.id)


async def test_prompt_edit_marks_cells_and_dependents_stale_without_wipe(
    db: str, service: SheetService
) -> None:
    sheet = await service.create_sheet("s")
    rows = await service.import_rows(sheet.id, [RowInput(input_name="Acme")])
    col_a = await _column(service, sheet.id, "A")
    col_b = await _column(service, sheet.id, "B", "sum {{A}}")
    for col in (col_a, col_b):
        await repo.upsert_cell(db, rows[0].id, col.id)
        await repo.queue_cells(db, sheet.id, "empty")
        await repo.claim_cell(db, rows[0].id, col.id)
        await repo.finish_cell(
            db,
            rows[0].id,
            col.id,
            status="done",
            result=Field[CellValue](value="v", status="inferred"),
            column_version=1,
        )

    await service.edit_column(
        col_a.id,
        name="A",
        prompt_template="new prompt",
        output_type="text",
        source_scope="web",
    )

    cell_a = await repo.get_cell(db, rows[0].id, col_a.id)
    cell_b = await repo.get_cell(db, rows[0].id, col_b.id)
    assert cell_a is not None and cell_a.stale is True
    assert cell_b is not None and cell_b.stale is True
    # Never wiped.
    assert cell_a.result is not None and cell_a.result.value == "v"
    assert cell_b.result is not None and cell_b.result.value == "v"
    assert cell_a.status == "done" and cell_b.status == "done"
