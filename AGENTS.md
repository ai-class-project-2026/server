# AI 작업 지침 (server)

팀플메이트 백엔드 + AI 레포. FastAPI, SQLAlchemy(async), SQLite, Alembic. 기획서: https://github.com/ai-class-project-2026/plan

## 파트별 담당

작업자의 파트는 `git config ai.part`로 확인한다. 본인 파트 밖의 파일을 고쳐야 하면 먼저 물어본다.

| 파트 | 폴더 |
|---|---|
| part1 | `backend/app/` 전반(DB, 인증, API, WebSocket), `backend/app/schedule/`, `ml/schedule/`, `ml/moderation/train.py`, `ml/moderation/export_onnx.py` |
| part3 | `backend/app/moderation/`, `ml/moderation/augment/`, `ml/moderation/build_train.py`, `data/moderation/`, `eval/moderation/` |
| part4 | `ml/common/dateparse_ko/`, `data/schedule/pilot/`, `data/schedule/real_test/`, `eval/schedule/`, `bots/discord/` |

`TODO(part N)` 주석이 있는 곳이 각 파트가 채울 자리다.

## 지켜야 할 것

* 테스트셋(`data/schedule/pilot/`, `data/schedule/real_test/`, `data/moderation/test_C_human/`, `test_D_fp/`)은 학습 데이터나 합성 데이터 프롬프트에 절대 넣지 않는다
* `.env`, API 키, 비밀번호, 봇 토큰은 코드나 로그에 넣지 않는다
* 공개 데이터 원본, 합성 데이터, `train.jsonl`, 모델 가중치는 커밋하지 않는다 (기획서 16.6)
* `dateparse_ko.parse()`의 시그니처는 바꾸지 않는다 (학습과 서빙이 같이 씀)
* API 형식(`backend/app/schemas.py`)을 바꾸면 `make openapi`를 돌리고 파트 2(web)에 알린다
* 새 도구나 의존성(테스트 프레임워크, 린터, CI 등)은 요청 없이 추가하지 않는다

## 실행

```sh
uv sync --group ml
make migrate && make seed && make dev   # http://localhost:8000/docs
uv run python ml/common/dateparse_ko/examples.py   # 날짜 예시 채점
```

## 설명 방식

팀원 대부분이 초보자다. 코드를 바꾸면 무엇을 왜 바꿨는지 쉬운 말로 짧게 설명한다.
작업 기록은 `ai-sessions/<파트>/`에 자동으로 남는다 (`ai-sessions/README.md`).
