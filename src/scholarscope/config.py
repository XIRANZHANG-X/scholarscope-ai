"""Runtime configuration, read from environment variables and the project `.env` file."""

from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Connection and API settings.

    Field names double as environment variable names (case-insensitive), so the
    same `.env` feeds both Docker Compose (`POSTGRES_*`) and the Python code.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Not "localhost": on Windows it resolves to ::1 first, which Docker Desktop leaves unanswered
    # (compose binds 127.0.0.1 only), so every connection would stall until connect_timeout.
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "scholarscope"
    postgres_password: SecretStr
    postgres_db: str = "scholarscope"

    openalex_base_url: str = "https://api.openalex.org"
    openalex_api_key: SecretStr | None = None

    raw_data_dir: Path = Path("data/raw")

    @field_validator("openalex_api_key", mode="before")
    @classmethod
    def _blank_key_is_none(cls, value: object) -> object:
        return value or None
