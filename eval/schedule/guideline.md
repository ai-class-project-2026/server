# 실제 테스트셋 만들기와 정답 달기 (파트 4)

기획서 8.3(정답 기준), 8.5(날짜 규칙), 8.6 ②(실제 테스트셋)를 라벨러가 바로 쓸 수 있게 정리했다.
이 파일과 기획서가 다르면 기획서를 따른다.

## 순서

| 단계 | 하는 일 | 명령 / 파일 |
|---|---|---|
| 1 | 카드 만들기 (이미 120장 있음) | `uv run python eval/schedule/cards.py` → `data/schedule/real_test/cards/` |
| 2 | 카드 한 장마다 단톡방을 만들고 역할극. 사람마다 자기 역할 칸만 보여 주기 | `cards.md` |
| 3 | 단톡 내보내기(.txt)를 테스트셋 한 줄로 바꾸기 | `uv run python eval/schedule/import_chat.py R001 chat.txt` → `unlabeled.jsonl` |
| 4 | 라벨러 두 명이 `unlabeled.jsonl`을 각자 복사해 `events`를 채우기. 서로 보지 않기 | `labels_a.jsonl`, `labels_b.jsonl` |
| 5 | 각자 실수 검사 | `uv run python eval/schedule/check_labels.py data/schedule/real_test/labels_a.jsonl` |
| 6 | 두 사람이 다르게 단 것만 모아서 토론하고 정하기 | `uv run python eval/schedule/check_labels.py labels_a.jsonl labels_b.jsonl` |
| 7 | 합의한 최종본을 `test.jsonl`로 저장하고 커밋 | `data/schedule/real_test/test.jsonl` |

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
* "목요일 거 못 할 듯"이라고만 하고 결론이 없으면 취소가 아니다. `existing_events`의 일정은 다시 적지 않는다
* 취소하고 새 날짜를 잡았으면 옛 일정은 `cancelled`, 새 일정은 `confirmed`로 둘 다 적는다

## 참석자

* 불참을 말한 사람은 `absent`에 넣고 `attendees`에서 뺀다
* 늦게 온다는 사람은 `attendees`에 넣고 `note`에 적는다 (예: `"예준 30분 늦음"`)
* 아무 말도 안 한 사람도 확정되면 `attendees`에 넣는다

## 날짜와 시간 (기준일 = `ref_datetime`)

| 표현 | 정답 |
|---|---|
| 오늘/내일·낼/모레/글피 | 기준일 +0/+1/+2/+3 |
| 요일만, 이번주 X요일 | 기준일 이후 가장 가까운 그 요일 (기준일이 그 요일이면 당일) |
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
* 날짜가 헷갈리면 날짜 계산기로 확인: `uv run python -c "import sys, datetime as dt; sys.path.insert(0, 'ml/common'); from dateparse_ko import parse; print(parse('다음주 화', dt.date(2026, 11, 16)))"`. 단, 계산기도 틀릴 수 있으니 규칙표가 우선이다

## 적지 않는 것 (8.3 제외 대상)

* 우리끼리 약속이 아닌 외부 일정: 시험, 수행평가 마감, 발표일, 체육대회
* 이미 지난 일정
* 날짜 없는 막연한 약속: "담에 놀러가자", "시험 끝나면 보자"

이런 것만 있는 대화는 `events: []`, `type: "not_schedule"`이다.
