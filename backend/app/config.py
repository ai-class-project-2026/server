from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "sqlite+aiosqlite:///./app.db"
    # Required, no default: a guessable key lets anyone forge tokens.
    jwt_secret: str = Field(min_length=32)
    jwt_expire_days: int = 7
    cors_origins: list[str] = ["http://localhost:3000"]

    # /moderate and /schedule: required X-API-Key when set (eval scripts, Discord bot)
    ai_api_key: str = ""
    # Schedule LLM behind an OpenAI-compatible server (llama.cpp, vLLM, Ollama...).
    # Empty = schedule AI off; chat still works.
    schedule_llm_url: str = ""
    schedule_llm_model: str = "teamplmate-schedule"
    schedule_model_version: str = "v0"
    schedule_llm_timeout: float = 30  # runs in the background, but keep demos snappy
    schedule_debounce_seconds: float = 10
    # Exported KcELECTRA (part 3). Empty = dictionary filter only.
    moderation_onnx_path: str = ""


settings = Settings()
