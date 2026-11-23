# AI 세션 2026-11-23 Claude Code

- 파트: part4
- 도구: Claude Code (Claude Opus 5.5)

---

## 프롬프트

대화기록_2026-11-16.md 이거 내가 grok이랑 하던건데 이거 읽고 너가 마저 해

### 결과

지난 세션(3주차 날짜 계산기, 커밋 `2e69aff`)에 이어 4주차 할 일인 "시나리오 카드 → 역할극 → 정답 달기"를 준비했다.
역할극 대화는 사람이 직접 해야 하므로 AI가 만들지 않았다. 그 앞뒤에 필요한 도구만 만들었다.

- 시나리오 카드 120장 생성 (`eval/schedule/cards.py`). 유형 비율은 파일럿(8.6 ③)의 3/3/4/2/3/3/2를 6배 한 것
- 단톡 내보내기(카톡 PC, 카톡 모바일, `이름: 말`) → 테스트셋 한 줄 변환 (`eval/schedule/import_chat.py`)
- 라벨 실수 검사 + 두 라벨러가 다르게 단 대화만 보여 주기 (`eval/schedule/check_labels.py`)
- 라벨러용 정답 기준 정리 (`eval/schedule/guideline.md`, 기획서 8.3/8.5 요약)
- `score.py`, `run.py`가 Windows(cp949)에서 한글 jsonl을 못 읽던 문제 수정: 파일을 `utf-8`로 열게 함.
  고치기 전에는 `UnicodeDecodeError: 'cp949' codec can't decode ...`로 멈췄다

확인: 가짜 대화 2개로 import → 라벨 2벌 → `check_labels.py`(요일 틀림, `6:00`, 참여자 아닌 이름 모두 잡음) → 비교 → `score.py` 끝까지 돌렸다. 가짜 파일은 지웠다.
`examples.py`는 그대로 142/142.

중간에 `main`을 `seojinha`에 합쳤다가, 사용자가 "합치지 말고 독립적으로 해, 머지는 무조건 허가 받고 해"라고 해서 푸시 전에 되돌렸다. 이 커밋은 `main`을 합치지 않은 `seojinha` 위에 있다.

### 변경 사항

- 새 파일: `eval/schedule/cards.py`, `eval/schedule/import_chat.py`, `eval/schedule/check_labels.py`, `eval/schedule/guideline.md`
- 새 파일: `data/schedule/real_test/cards/cards.md`, `cards.jsonl` (카드 120장, seed 2026)
- 수정: `eval/schedule/score.py` (읽기/CSV 쓰기 `encoding="utf-8"`), `eval/schedule/run.py` (읽기/쓰기 `encoding="utf-8"`, 빈 줄 건너뛰기)
