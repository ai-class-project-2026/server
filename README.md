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

## API

`make dev` 후 http://localhost:8000/docs 에서 전부 확인하고 바로 호출해볼 수 있다.

| 영역 | 엔드포인트 |
|---|---|
| 인증 | `POST /auth/signup`, `/auth/login`, `/auth/google`, `GET /auth/me` |
| 워크스페이스 | `GET/POST /workspaces`, `GET/PATCH /workspaces/{id}`, `GET /workspaces/{id}/members`, `POST /invites/{code}` |
| 채팅 | `GET/POST /workspaces/{id}/channels`, `GET/POST /channels/{id}/messages`, `WS /ws/channels/{id}?token=`, `POST /reports` |
| 캘린더 | `GET/POST /workspaces/{id}/events`, `PATCH/DELETE /events/{id}` |
| AI 카드 | `GET /channels/{id}/suggestions`, `POST /suggestions/{id}/action` |
| 할 일 | `GET/POST /workspaces/{id}/todos`, `PATCH/DELETE /todos/{id}` |
| AI | `POST /moderate`, `POST /schedule` (`AI_API_KEY` 설정 시 `X-API-Key` 필요) |

WebSocket은 받기 전용이다: `{"type": "message" | "suggestion" | "suggestion_resolved", "data": ...}`.

## 흐름

메시지 POST → `moderation.moderate()` → 저장 → WS로 `message` → (AI 켜진 워크스페이스면) 10초 디바운스 또는 5턴마다 `schedule/pipeline.run()` → trigger → dateparse_ko 주석 → LLM(tracker) → reconcile → 중복 아닌 것만 WS로 `suggestion`.

| 위치 | 담당 |
|---|---|
| `backend/app/moderation/` (normalize, dict_filter, classifier, 단어 목록) | 파트 3 |
| `ml/common/dateparse_ko/`, `backend/tests/test_dateparse.py` | 파트 4 |
| `backend/app/schedule/`, `ml/schedule/` | 파트 1 |

서버는 워커 1개로 띄운다 (WebSocket 허브와 디바운스가 프로세스 메모리에 있음).

기획서: [plan](https://github.com/ai-class-project-2026/plan)
