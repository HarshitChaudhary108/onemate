"""``build_providers`` wiring tests (P2): groq mode handling.

Explicit constructor kwargs are used so the assertions hold regardless of
the developer's real ``.env`` contents.
"""

from pydantic import SecretStr

from app.api.research import Providers, build_providers
from app.config import Settings
from app.providers.groq import GroqProvider
from app.providers.groq_mock import GroqMockProvider


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "exa_api_key": None,
        "moltsets_api_key": None,
        "moltsets_mode": "off",
        "groq_api_key": None,
    }
    return Settings(**{**base, **overrides})


def test_groq_mock_mode_wires_mock_provider() -> None:
    providers = build_providers(_settings(groq_mode="mock"))
    assert isinstance(providers.groq, GroqMockProvider)
    assert providers.groq_mode == "mock"


def test_groq_live_with_key_wires_http_provider() -> None:
    providers = build_providers(
        _settings(groq_mode="live", groq_api_key=SecretStr("gsk_synthetic"))
    )
    assert isinstance(providers.groq, GroqProvider)
    assert providers.groq_mode == "live"


def test_groq_live_without_key_is_none_not_mock() -> None:
    providers = build_providers(_settings(groq_mode="live"))
    assert providers.groq is None
    assert providers.groq_mode == "live"


def test_groq_live_with_blank_key_is_none_not_mock() -> None:
    providers = build_providers(_settings(groq_api_key=SecretStr("   ")))
    assert providers.groq is None


def test_providers_dataclass_defaults_keep_existing_sites_working() -> None:
    providers = Providers(
        exa=None,
        moltsets=None,
        moltsets_mode="off",
        exa_mode="live",
    )
    assert providers.groq is None
    assert providers.groq_mode == "live"
