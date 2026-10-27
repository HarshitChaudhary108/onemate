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


def test_hubspot_settings_defaults() -> None:
    settings = Settings(hubspot_client_id=None, hubspot_client_secret=None, hubspot_token_key=None)
    assert settings.hubspot_redirect_uri == (
        "http://localhost:8000/api/integrations/hubspot/callback"
    )
    assert settings.hubspot_scopes == (
        "crm.objects.companies.read crm.objects.companies.write "
        "crm.objects.contacts.read crm.objects.contacts.write"
    )
    assert settings.hubspot_mode == "live"
    assert settings.hubspot_post_connect_url == "http://localhost:3000/integrations"
    assert settings.hubspot_max_qps == 5


def test_hubspot_configured_requires_all_three_secrets() -> None:
    kwargs: dict[str, SecretStr] = {
        "hubspot_client_id": SecretStr("client-id"),
        "hubspot_client_secret": SecretStr("client-secret"),
        "hubspot_token_key": SecretStr("fernet-key"),
    }
    assert Settings(**kwargs).hubspot_configured is True
    for field in kwargs:
        # missing or whitespace-only on ANY of the three → not configured
        assert Settings(**{**kwargs, field: None}).hubspot_configured is False  # type: ignore[arg-type]
        blank: dict[str, SecretStr | None] = {**kwargs, field: SecretStr("   ")}
        assert Settings(**blank).hubspot_configured is False  # type: ignore[arg-type]
