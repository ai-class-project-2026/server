# 실제 테스트셋 만들기와 정답 달기 (파트 4)

기획서 8.3(정답 기준), 8.5(날짜 규칙), 8.6 ②(실제 테스트셋)를 라벨러가 바로 쓸 수 있게 정리했다.
이 파일과 기획서가 다르면 기획서를 따른다.

## 순서

| 단계 | 하는 일 | 명령 / 파일 |
|---|---|---|
| 1 | 카드 만들기 (이미 있음: 실제 120장, 파일럿 20장, 약점 24장) | 아래 "카드 묶음" |
| 2 | 카드 한 장마다 단톡방을 만들고 역할극. 사람마다 자기 역할 칸만 보여 주기 | `cards.md` |
| 3 | 단톡 내보내기(.txt)를 테스트셋 한 줄로 바꾸기 | `uv run python eval/schedule/import_chat.py R001 chat.txt` → `unlabeled.jsonl` |
| 4 | 라벨러 두 명이 `unlabeled.jsonl`을 각자 복사해 `events`를 채우기. 서로 보지 않기 | `labels_a.jsonl`, `labels_b.jsonl` |
| 5 | 각자 실수 검사 | `uv run python eval/schedule/check_labels.py data/schedule/real_test/labels_a.jsonl` |
| 6 | 두 사람이 다르게 단 것만 모아서 토론하고 정하기 | `uv run python eval/schedule/check_labels.py labels_a.jsonl labels_b.jsonl` |
| 7 | 합의한 최종본을 `test.jsonl`로 저장하고 커밋 | `data/schedule/real_test/test.jsonl` |

### 카드 묶음

| 묶음 | 카드 | 대화가 쌓이는 파일 | 최종본 | 목표 |
|---|---|---|---|---|
| 실제 테스트셋 R001~R120 | `data/schedule/real_test/cards/` | `data/schedule/real_test/unlabeled.jsonl` | `data/schedule/real_test/test.jsonl` | 100줄 이상 |
| 파일럿 P001~P020 | `data/schedule/pilot/cards/` | `data/schedule/pilot/unlabeled.jsonl` | `data/schedule/pilot/pilot.jsonl` | 20줄 (3/3/4/2/3/3/2) |
| 약점 H001~H024 | `data/schedule/real_test/hard_cards/` | `data/schedule/real_test/hard_unlabeled.jsonl` | `data/schedule/real_test/hard.jsonl` | 약점마다 6줄 |

* `import_chat.py`는 카드 번호의 첫 글자를 보고 알맞은 파일에 넣는다. 위 표의 명령은 R 기준이고, P와 H는 파일 이름만 바꾸면 된다
* 약점 줄의 `weakness`는 고치지 않는다. `score.py`가 이걸로 묶어서 약점마다 점수를 낸다
* `check_labels.py` 끝에 유형별 개수가 나온다. 파일럿 비율이 맞는지 여기서 본다. 흐름이 바뀌어 비율이 어긋나면 모자란 유형의 카드로 한 번 더 한다
* **대화와 정답은 사람만 만든다.** AI로 대화를 만들거나, AI에게 정답을 달게 하거나, 대화를 AI 채팅에 붙여 넣지 않는다
* 약점 대화를 만드는 사람은 파트 1의 학습용 대화(v4)를 보지 않는다

### 지킬 것

* 카드는 연기 부탁일 뿐 정답이 아니다. 대화가 카드와 다르게 흘러갔으면 **대화대로** 정답을 단다. `type`도 실제 흐름에 맞게 고친다
* 일치도 숫자가 필요하면 `uv run python eval/schedule/score.py labels_a.jsonl labels_b.jsonl --label agreement --report agreement.csv`
* `test.jsonl`은 학습이나 합성 데이터 프롬프트에 절대 넣지 않는다

## 한 줄 모양

```json
{"card_id": "R001", "type": "revise", "ref_datetime": "2026-11-16T20:30:00+09:00",
 "participants": ["하린", "예준", "시우"],
 "dialogue": [{"speaker": "하린", "text": "목 4시 어때", "ts": "..."}, "..."],
 "existing_events": [],
 "events": [
   {"title": "역할 분담 회의", "date": "2026-11-17", "weekday": "화", "start": null,
    "time_text": "점심시간", "location": null, "status": "confirmed",
    "attendees": ["하린", "예준", "시우"], "absent": [], "note": null}
 ]}
```

라벨러는 `events`와 `type`만 고친다. 나머지는 `import_chat.py`가 채운다.

## status 정하기

| status | 언제 |
|---|---|
| `confirmed` | 아래 확정 조건 중 하나를 만족 |
| `proposed` | 날짜/시간 제안은 나왔지만 확정 조건을 못 채우고 끝남 |
| `rejected` | 제안됐다가 반대나 수정으로 버려진 안 |
| `cancelled` | `existing_events`에 있던 일정을 취소하기로 확정 (취소도 확정 조건과 같은 기준) |

**확정 조건 (둘 중 하나)**

1. 마지막 수정안에 제안자 포함 과반이 동의하고 반대가 없다. 4명이면 3명, 5명이면 3명, 3명이면 2명
2. 제안자나 팀장이 "이걸로 확정", "그럼 그때 보자"처럼 확정을 선언하고, 1명 이상 동의

헷갈리는 경우:

* "아마?", "일정 봐야 할 듯", "난 아무때나"는 동의가 아니다 → 다른 동의가 모자라면 `proposed`
* 번복이 있으면 마지막 안만 `confirmed`. 버려진 안은 `rejected`로 남긴다 (점수는 `confirmed`, `cancelled`만 센다)
* "봐서", "그날 봐서", "나도 봐서"도 동의가 아니다. "ㅇㅇ"이 앞에 붙어도 뒤가 "봐서"면 동의가 아니다. 나중에 확실히 된다고 말했으면 그때부터 동의다
* "목요일 거 못 할 듯"이라고만 하고 결론이 없으면 취소가 아니다. `existing_events`의 일정은 다시 적지 않는다. 다른 일정도 없으면 `events: []`, `type: "proposal_only"`
* 취소하고 새 날짜를 잡았으면 옛 일정은 `cancelled`, 새 일정은 `confirmed`로 둘 다 적는다
* "그때까지 바쁘다"("6시까지 학원")는 사정을 말한 것이지 6시에 하자는 제안이 아니다. 일정으로 적지 않는다

**기존 일정을 바꾼 대화** (기획서 8.3에 없어서 여기서 정함. 라벨링 전에 파트 1과 확인)

| 대화 | 정답 |
|---|---|
| 시각만 바꾸기로 정함 ("목요일 거 6시로 바꾸자") | 바뀐 일정만 `confirmed`. 옛 시각은 `rejected`. **`cancelled`가 아니다** |
| 날짜를 옮기기로 정함 ("목요일 거 금요일로 미루자") | 새 날짜는 `confirmed`, 옛 날짜는 `rejected` |
| 취소하기로 정함 ("목요일 거 취소하자") | 옛 일정 `cancelled` |
| 취소하기로 정하고 따로 새 약속을 잡음 | 옛 일정 `cancelled`, 새 일정 `confirmed` |
| 바꾸자는 말만 나오고 정하지 못함 | 새 안만 `proposed`. 옛 일정은 적지 않는다 |

바뀐 일정의 `title`과 `location`은 대화에서 새로 말하지 않았으면 `existing_events`의 값을 그대로 쓴다.

## 참석자

* 불참을 말한 사람은 `absent`에 넣고 `attendees`에서 뺀다
* 늦게 온다는 사람은 `attendees`에 넣고 `note`에 적는다 (예: `"예준 30분 늦음"`)
* 아무 말도 안 한 사람도 확정되면 `attendees`에 넣는다

## 날짜와 시간 (기준일 = 그 메시지를 보낸 날)

기준일은 보통 `ref_datetime`의 날짜와 같다. 대화가 자정을 넘겼으면 그 말을 한 메시지의 `ts` 날짜가 기준일이다 (서버도 이렇게 계산한다).

한 글자 요일은 후보로만 알리는 것과 같은 요일이면 다음 주인 것은 파트 4가 2026-11-25에 받아들여 확정했다.

| 표현 | 정답 |
|---|---|
| 오늘/내일·낼/모레/글피 | 기준일 +0/+1/+2/+3 |
| 요일만 (`수`, `수요일`) | 기준일 다음에 오는 그 요일. **기준일이 그 요일이면 다음 주 그 요일** (수요일에 "수 보자" → 7일 뒤) |
| 이번주 X요일 | 기준일 이후 가장 가까운 그 요일. **기준일이 그 요일이면 당일** (수요일에 "이번주 수" → 오늘) |
| 날짜와 요일을 같이 (`10/15(목)`, `오늘이 수요일이니까`) | 날짜를 따른다. 요일은 따로 세지 않는다 |
| 다음주·담주 X | 기준일이 속한 주(월요일 시작) 다음 주의 X요일 |
| 다다음주 X | 다음주 X + 7일 |
| 이번 주말 | 이번 주 토요일 (토요일이 지났으면 일요일) |
| M/D, M월 D일 | 올해. 기준일보다 앞이면 내년 |
| M/D의 그 전주/다음주 X | M/D가 속한 주의 전주/다음 주 X요일 |
| 1~7시 (오전/오후 없음) | 오후. `7시` → `"19:00"` |
| 8~11시 (오전/오후 없음) | 문맥으로 알 수 있으면 숫자, 아니면 `start: null`, `time_text: "8시"` |
| N시 반 | `N:30` |
| 점심시간, 방과후, 수업 끝나고 | `start: null`, `time_text`에 원문 그대로 |

* `start`는 항상 두 자리 24시간 `"HH:MM"`. 오전/오후 없는 6시는 오후라서 `"18:00"`
* `weekday`는 날짜의 요일 한 글자. `check_labels.py`가 틀린 요일을 잡아 준다
* 한 글자 요일(`목`, `수`, `일`)은 다른 낱말일 수 있다 ("목 아파", "할 일"). 날짜 계산기는 이런 것을 `tentative=True`(후보)로 알려 줄 뿐이고, **진짜 요일인지는 라벨러가 대화를 보고 정한다**. 진짜 요일이면 정답은 위 표와 똑같이 적는다
* 규칙표는 대화에서 다르게 합의하지 않았을 때 쓴다. 누가 "오늘 말하는 거지?" 하고 물어서 "ㅇㅇ"로 정해졌으면 그 합의가 정답이다
* 날짜가 헷갈리면 날짜 계산기로 확인: `uv run python -c "import sys, datetime as dt; sys.path.insert(0, 'ml/common'); from dateparse_ko import parse; print(parse('다음주 화', dt.date(2026, 11, 16)))"`. 단, 계산기도 틀릴 수 있으니 규칙표가 우선이다

## 적지 않는 것 (8.3 제외 대상)

* 우리끼리 약속이 아닌 외부 일정: 시험, 수행평가 마감, 발표일, 체육대회
* 이미 지난 일정
* 날짜 없는 막연한 약속: "담에 놀러가자", "시험 끝나면 보자"

이런 것만 있는 대화는 `events: []`, `type: "not_schedule"`이다.

## 평가 돌리기 (모델이 연결된 뒤)

`make`가 없는 Windows PowerShell 기준이다. 서버 창과 채점 창을 따로 연다.

```powershell
# 서버 창 (주소와 이름은 파트 1이 알려 주는 값. 학습 전 모델은 http://localhost:11434, qwen2.5:3b)
$env:ALLOW_OPEN_AI_API="1"; $env:PYTHONPATH="ml/common"
$env:SCHEDULE_LLM_URL="<주소>"; $env:SCHEDULE_LLM_MODEL="<모델 이름>"
uv run uvicorn app.main:app --app-dir backend
```

```powershell
# 채점 창: test.jsonl 자리에 pilot.jsonl, hard.jsonl도 같은 방법으로
uv run --group ml python eval/schedule/run.py data/schedule/real_test/test.jsonl preds_ours.jsonl --target server
uv run python eval/schedule/score.py data/schedule/real_test/test.jsonl preds_ours.jsonl --label <버전 이름>
# 상용 LLM 기준선 ($env:ANTHROPIC_API_KEY는 파트 1에게 받는다. 파일에 적거나 커밋하지 않는다)
uv run --group ml python eval/schedule/run.py data/schedule/real_test/test.jsonl preds_claude.jsonl --target claude
uv run python eval/schedule/score.py data/schedule/real_test/test.jsonl preds_claude.jsonl --label claude-zeroshot
```

* 서버를 켜려면 `.env`에 `JWT_SECRET`(32자 이상)이 있어야 한다
* **서버는 LLM이 꺼져 있어도 빈 답을 정상 응답으로 돌려준다.** 그대로 채점하면 오등록률이 0%로 나온다. 답이 전부 비면 `run.py`가 저장하지 않고 멈춘다. 일부만 실패한 것은 서버 창의 `schedule LLM failed` 줄로 확인한다. 이 줄이 있으면 점수를 쓰지 않고 다시 돌린다
* `preds_*.jsonl`은 커밋하지 않는다. `eval/reports/schedule.csv`, `schedule_by_type.csv`만 커밋한다
