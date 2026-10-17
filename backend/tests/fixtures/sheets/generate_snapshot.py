"""Regenerate the synthetic sheet-snapshot fixture for the frontend Zod
drift test (``frontend/tests/sheet-schema.test.ts``).

SYNTHETIC — every value below is hand-written; no provider calls and no
captured live data. The serialization is real: the fixture is produced by
building a sheet through :mod:`app.sheets.repo` and dumping the same
:class:`app.sheets.models.SheetSnapshot` the ``GET /snapshot`` endpoint
returns (``model_dump_json`` — what FastAPI serializes).

Run from ``backend/``:

    .venv/Scripts/python.exe tests/fixtures/sheets/generate_snapshot.py
"""

import asyncio
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.schemas.report import CompanyIdentity, Field, Source  # noqa: E402
from app.sheets import repo  # noqa: E402
from app.sheets.db import init_db  # noqa: E402
from app.sheets.models import RowInput  # noqa: E402


def utcnow() -> datetime:
    return datetime.now(UTC)


OUT = Path(__file__).with_name("snapshot.synthetic.json")


async def build() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "sheets.db")
        await init_db(db_path)

        sheet = await repo.create_sheet(db_path, "Fixture targets")
        rows = await repo.create_rows(
            db_path,
            sheet.id,
            [
                RowInput(input_name="Acme Corp", input_domain="acme.test"),
                RowInput(input_name="Globex", input_domain=None),
            ],
        )

        evidence = Source(
            provider="Exa",
            mock=True,
            retrieved_at=utcnow(),
            url="https://acme.test/",
            title="Acme Corp — synthetic",
        )
        await repo.update_row_identity(
            db_path,
            rows[0].id,
            identity_status="resolved",
            resolved_name="Acme Corp",
            resolved_domain="acme.test",
            identity=CompanyIdentity(
                resolved=True,
                company_name=Field[str](value="Acme Corp", status="verified", sources=[evidence]),
                domain=Field[str](value="acme.test", status="verified", sources=[evidence]),
            ),
        )

        await repo.create_column(
            db_path,
            sheet.id,
            name="Industry",
            prompt_template="What industry is {{Company}} in?",
            output_type="text",
            source_scope="web",
        )
        await repo.create_column(
            db_path,
            sheet.id,
            name="Headcount",
            prompt_template="Headcount of {{Company}}; industry context: {{Industry}}",
            output_type="number",
            source_scope="web",
            depends_on=["Industry"],
        )
        for row in rows:
            for col in await repo.list_columns(db_path, sheet.id):
                await repo.upsert_cell(db_path, row.id, col.id)

        run = await repo.create_run(db_path, sheet.id, cap=1.0)
        await repo.queue_cells(db_path, sheet.id, "empty", run_id=run.id)

        columns = {c.name: c for c in await repo.list_columns(db_path, sheet.id)}
        industry = columns["Industry"]
        headcount = columns["Headcount"]

        # One completed verified cell (the only one with a real result).
        assert await repo.claim_cell(db_path, rows[0].id, industry.id, run_id=run.id) is not None
        await repo.finish_cell(
            db_path,
            rows[0].id,
            industry.id,
            status="done",
            result=Field[str](
                value="Industrial robotics",
                status="verified",
                sources=[
                    Source(
                        provider="Exa",
                        mock=True,
                        retrieved_at=utcnow(),
                        url="https://acme.test/about",
                        title="About Acme — synthetic",
                    )
                ],
            ),
            cost=0.007,
            rendered_prompt="What industry is Acme Corp in?",
            run_id=run.id,
            column_version=industry.version,
        )
        await repo.add_run_spend(db_path, run.id, 0.007)

        # One unavailable cell — found=false path (empty value allowed).
        assert await repo.claim_cell(db_path, rows[1].id, industry.id, run_id=run.id) is not None
        await repo.finish_cell(
            db_path,
            rows[1].id,
            industry.id,
            status="done",
            result=Field[str](value=None, status="unavailable", note="found=false"),
            cost=0.007,
            rendered_prompt="What industry is Globex in?",
            run_id=run.id,
            column_version=industry.version,
        )

        # One blocked cell on the dependent column.
        await repo.mark_blocked(
            db_path, rows[1].id, headcount.id, "upstream cell unavailable: Industry"
        )

        snapshot = await repo.snapshot(db_path, sheet.id)
        assert snapshot is not None
        OUT.write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(build())
