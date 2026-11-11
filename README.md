# server

팀플메이트 백엔드. FastAPI + SQLAlchemy(async, aiosqlite) + Alembic, DB는 SQLite.

## 실행

```sh
cp .env.example .env   # JWT_SECRET 채우기 (32자 이상)
uv sync
make migrate   # alembic upgrade head
make dev       # http://localhost:8000/docs
```

| 명령 | 내용 |
|---|---|
| `make test` | pytest |
| `make lint` | ruff |
| `make revision m="add users"` | 모델 변경으로 마이그레이션 생성 |
| `make openapi` | `openapi.json` 생성 (web 타입 생성용) |

- 모델은 `backend/app/db/models.py`. SQLite라 리스트 필드는 `ARRAY` 대신 `JSON`으로.
- DB를 바꾸려면 `DATABASE_URL`만 바꾸면 된다.

기획서: [plan](https://github.com/ai-class-project-2026/plan)
