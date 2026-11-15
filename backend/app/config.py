from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://standoff:change-me@db/standoff"
    bot_token: str = ""
    bot_username: str = "so2_check_bot"
    public_url: str = ""
    auth_max_age: int = 300
    session_ttl: int = 43200
    refresh_ttl: int = 300
    source_timeout: int = 30
    source_min_interval: float = 2.0
    history_max_pages: int = 10
    source_adapter: str = ""
    allowed_origins: str = ""
    collector_enabled: bool = False
    collector_schedule_enabled: bool = False
    collector_storage: str = "/var/lib/so2-check/collector"
    collector_max_storage_mb: int = 512
    collector_pilot_uid: str = "202777998"
    collector_lease_seconds: int = 1800
    manual_import_enabled: bool = True


@lru_cache
def get_settings():
    return Settings()
