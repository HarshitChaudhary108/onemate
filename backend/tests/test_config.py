"""New sheets/planner settings on top of the existing config style.

Explicit constructor kwargs are used so the assertions hold regardless of
the developer's real ``.env`` contents.
"""

from pydantic import SecretStr

from app.config import Settings


def test_sheets_settings_defaults() -> None:
    settings = Settings(groq_api_key=None)
    assert settings.groq_mode == "live"
    assert settings.planner_model == "openai/gpt-oss-120b"
    assert settings.sheets_db_path == "sheets.db"
    assert settings.sheet_max_rows == 200
    assert settings.sheet_max_columns == 30
    assert settings.sheet_max_concurrency == 4
    assert settings.exa_max_qps == 5
    assert settings.run_cost_cap_usd == 1.00


def test_groq_configured_mirrors_provider_pattern() -> None:
    # A blank key never counts as configured — same rule as Exa/MoltSets.
    assert Settings(groq_api_key=None).groq_configured is False
    assert Settings(groq_api_key=SecretStr("   ")).groq_configured is False
    assert Settings(groq_api_key=SecretStr("gsk_test")).groq_configured is True
