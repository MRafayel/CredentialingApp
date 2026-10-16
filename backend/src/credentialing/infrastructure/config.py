"""Centralized configuration. The only module that reads environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]

# The local development database started by `make db-start`. Not a secret: it only exists on a
# developer machine. Every deployed environment must set CREDENTIALING_DATABASE_URL.
_LOCAL_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


class Settings(BaseSettings):
    """Runtime settings, loaded from ``CREDENTIALING_*`` variables or the repo-root ``.env``.

    Secrets must be typed as ``pydantic.SecretStr`` so they cannot leak through logs or reprs.
    """

    model_config = SettingsConfigDict(
        env_prefix="CREDENTIALING_",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_url: SecretStr = SecretStr(_LOCAL_DATABASE_URL)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
