from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "sqlite+aiosqlite:///./app.db"
    jwt_secret: str = "change-me"
    google_client_id: str = ""
    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()
