"""Centralized configuration. The only module that reads environment variables."""

from functools import lru_cache
from typing import Annotated, Final, Literal

from pydantic import Field, SecretStr, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]

# A current desktop Chrome string, kept in one place so it is easy to bump.
DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)

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
    user_agent: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = (
        DEFAULT_USER_AGENT
    )
    """User-Agent sent to every source: a fixed standard browser string (owner decision,
    2026-10-26). A User-Agent alone does not pass fingerprint-based firewalls."""
    http_timeout_seconds: float = Field(default=60.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
