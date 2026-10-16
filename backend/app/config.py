"""Application settings (pydantic-settings).

Provider secrets live only here / in the backend environment. Blank values in
`.env.example` mean "not configured"; a blank key never enables mock mode.
"""

import functools
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    exa_api_key: SecretStr | None = None
    exa_mode: Literal["live", "mock"] = "live"
    moltsets_api_key: SecretStr | None = None
    moltsets_mode: Literal["live", "mock", "off"] = "live"
    moltsets_user_agent: str = "outmate-prototype/1.0"
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse the comma-separated CORS_ORIGINS env var into a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def exa_configured(self) -> bool:
        """True only when a non-blank Exa key is present."""
        return self.exa_api_key is not None and bool(self.exa_api_key.get_secret_value().strip())

    @property
    def moltsets_configured(self) -> bool:
        """True only when a non-blank MoltSets key is present."""
        return self.moltsets_api_key is not None and bool(
            self.moltsets_api_key.get_secret_value().strip()
        )


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()
