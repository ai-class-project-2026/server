from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")  # .env is shared with the Discord bot

    database_url: str = "sqlite+aiosqlite:///./app.db"
    # Required, no default: a guessable key lets anyone forge tokens.
    jwt_secret: str = Field(min_length=32)
    jwt_expire_days: int = 7
    cors_origins: list[str] = ["http://localhost:3000"]

    # /moderate and /schedule: required X-API-Key when set (eval scripts, Discord bot)
    ai_api_key: str = ""
    # 키가 비어 있어도 /moderate, /schedule을 열어 두려면 1 (개발용: make dev 에서만 켠다)
    allow_open_ai_api: bool = False
    max_body_bytes: int = 1_048_576  # 요청 본문 최대 크기(1MiB). 넘으면 413
    # 모든 경로 앞에 붙는 말. 웹과 같은 도메인에서 /api 로 나눠 배포할 때 "/api" (개발에서는 비운다)
    api_prefix: str = ""
    # Schedule LLM behind an OpenAI-compatible server (llama.cpp, vLLM, Ollama...).
    # Empty = schedule AI off; chat still works.
    schedule_llm_url: str = ""
    schedule_llm_model: str = "teamplmate-schedule"
    schedule_model_version: str = "v0"
    schedule_llm_timeout: float = 30  # runs in the background, but keep demos snappy
    schedule_debounce_seconds: float = 10
    # Exported KcELECTRA (part 3). Empty = dictionary filter only.
    moderation_onnx_path: str = ""
    # 욕설 판정을 맡길 모델 서버 (같은 코드를 띄운 서버의 주소, 예: http://100.x.x.x:8765). 비우면 이 서버 안에서 판정.
    # 답이 없으면 이 서버 안에서 판정한다 (app/moderation_client.py)
    moderation_url: str = ""
    moderation_api_key: str = ""  # 그 서버의 AI_API_KEY
    moderation_timeout: float = 3


settings = Settings()
