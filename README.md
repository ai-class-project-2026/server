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
| `make revision m="add users"` | 모델 변경으로 마이그레이션 생성 |
| `make openapi` | `openapi.json` 생성 (web 타입 생성용) |

- 모델은 `backend/app/db/models.py`. SQLite라 리스트 필드는 `ARRAY` 대신 `JSON`으로.
- DB를 바꾸려면 `DATABASE_URL`만 바꾸면 된다.

## 시연

```sh
make migrate && make seed     # minjun@demo.com … haeun@demo.com / demo1234
```

AI 카드까지 보려면 OpenAI 호환 LLM 서버를 붙인다. 학습 전에는 Ollama 기본 모델로 흐름을 돌린다:

```sh
ollama pull qwen2.5:3b
SCHEDULE_LLM_URL=http://localhost:11434 SCHEDULE_LLM_MODEL=qwen2.5:3b make dev
```

민준으로 로그인 → `일반` 채널에 "그럼 목 7시 디코로 확정" → 디바운스(10초) 후 카드가 뜬다.

2026-11-11 측정 (M3 16GB, qwen2.5:3b 학습 전, 날짜 주석은 오늘/내일만 지원하는 상태):
응답 3.2초(웜), 확정·19:00은 맞췄지만 "목"을 화요일로 계산하고 "디코"를 "디노코"로 적었다.
가설 ②(상대 날짜 오류)의 첫 사례 — dateparse_ko가 요일을 풀어주면 고쳐져야 할 부분.

## API

`make dev` 후 http://localhost:8000/docs 에서 전부 확인하고 바로 호출해볼 수 있다.

| 영역 | 엔드포인트 |
|---|---|
| 인증 | `POST /auth/signup`, `/auth/login`, `GET /auth/me` |
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
| `ml/common/dateparse_ko/` | 파트 4 |
| `backend/app/schedule/`, `ml/schedule/` | 파트 1 |
| `ml/moderation/train.py`, `ml/moderation/export_onnx.py` (욕설 모델 학습, ONNX 변환) | 파트 1 |
| `ml/moderation/augment/`, `data/moderation/`, `eval/moderation/` (학습 데이터, 채점) | 파트 3 |

## 일정 AI 학습/평가

| 단계 | 명령 |
|---|---|
| 합성 대화 생성 (Claude API, `ANTHROPIC_API_KEY` 필요) | `uv run --group ml python ml/schedule/synth/generate.py --n 20 --out data/schedule/synth/try.jsonl` |
| LoRA 학습 (Colab) | `ml/schedule/train_lora.py` 맨 위 주석 참고 |
| 예측 | `uv run --group ml python eval/schedule/run.py <testset> preds.jsonl --target server\|claude` |
| 채점 | `uv run python eval/schedule/score.py <testset> preds.jsonl --label v1` → `eval/reports/schedule.csv` |

## 욕설 AI 학습/평가

| 단계 | 명령 |
|---|---|
| 증강 | `ml/moderation/augment/` (규칙 추가) |
| 학습 (Colab) | `ml/moderation/train.py` 맨 위 주석 참고 |
| ONNX 변환 | `ml/moderation/export_onnx.py` → 서버에 `MODERATION_ONNX_PATH` 설정, `uv sync --group onnx` |
| 채점 | `uv run python eval/moderation/score.py data/moderation/test_C_human/sample.jsonl --label dict-v0` |

일정 데이터 한 줄 형식: `ScheduleRequest`(`ref_datetime`, `participants`, `dialogue`, `existing_events`) + 정답 `events`(기획서 8.2) + `type`(유형별 점수용).
학습 프롬프트와 서빙 프롬프트는 같은 `app/schedule/prompt.py`를 쓴다.

서버는 워커 1개로 띄운다 (WebSocket 허브와 디바운스가 프로세스 메모리에 있음).

AI 사용 기록: [`ai-sessions/`](ai-sessions/README.md) (처음 한 번 `git config ai.part partN`)

기획서: [plan](https://github.com/ai-class-project-2026/plan)
