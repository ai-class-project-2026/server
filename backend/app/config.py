from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "sqlite+aiosqlite:///./app.db"
    # Required, no default: a guessable key lets anyone forge tokens.
    jwt_secret: str = Field(min_length=32)
    jwt_expire_days: int = 7
    google_client_id: str = ""
    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()
