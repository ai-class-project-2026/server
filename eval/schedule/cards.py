"""역할극 시나리오 카드 만들기 (기획서 8.6 ②, 파트 4 4주차).

    uv run python eval/schedule/cards.py              # 실제 테스트셋 카드 R001~R120
    uv run python eval/schedule/cards.py --set pilot  # 파일럿 카드 P001~P020 (8.6 ③ 비율 그대로)
    uv run python eval/schedule/cards.py --set hard   # 약점 카드 H001~H024 (알려진 실수를 노림)

결과 (아래 SETS의 폴더):
  cards.md     인쇄하거나 단톡에 나눠 줄 카드. 사람마다 자기 역할만 보면 된다
  cards.jsonl  카드 원본. import_chat.py가 기준일, 참여자, 기존 일정을 여기서 가져간다

카드는 "이런 흐름이 되도록 연기해 달라"는 부탁일 뿐 정답이 아니다. 사람이
즉흥으로 하다 보면 흐름이 바뀌므로, 정답(events)은 대화를 보고
guideline.md대로 따로 단다.

합성 학습 데이터(ml/schedule/synth)와는 일부러 코드를 공유하지 않는다.
테스트셋이 학습 데이터와 같은 틀에서 나오면 점수가 부풀려지기 때문이다.
"""

import argparse
import datetime as dt
import json
import random
from pathlib import Path

WD = "월화수목금토일"
NAMES = ["지민", "유나", "태현", "소희", "건우", "다은", "준호", "하린", "시우", "채원", "민재", "예준"]
TITLES = ["팀플 회의", "발표 연습", "자료 조사 회의", "PPT 마무리", "역할 분담 회의", "최종 리허설"]
PLACES = ["디코", "줌", "학교 도서관", "스터디카페", "교실", "역 앞 카페"]
TIMES = ["4시", "4시 반", "5시", "6시", "7시", "7시 반", "저녁 8시", "밤 9시", "점심시간", "방과후", "수업 끝나고"]
EXCUSES = ["학원", "병원 예약", "가족 약속", "알바", "동아리", "과외"]

# 파일럿 비율(8.6 ③: 3/3/4/2/3/3/2)을 그대로 키운다. 20장 단위로 정확히 맞는다.
# 카드 묶음: 번호 글자, 장수, seed, 카드 폴더, import_chat.py가 대화를 쌓는 파일
SETS = {
    "real": ("R", 120, 2026, "data/schedule/real_test/cards", "data/schedule/real_test/unlabeled.jsonl"),
    "pilot": ("P", 20, 20, "data/schedule/pilot/cards", "data/schedule/pilot/unlabeled.jsonl"),
    "hard": ("H", 24, 24, "data/schedule/real_test/hard_cards", "data/schedule/real_test/hard_unlabeled.jsonl"),
}

# 약점 카드에서 노리는 것과, 그 안에서 바꿔 가며 쓰는 흐름. 실수를 부르는 쪽과 그 반대쪽(지나치게 조심하는 실수)을 섞는다
WEAK = {
    "애매한 답": ["모두 애매", "모두 애매", "모두 애매", "한 명만 애매", "한 명만 애매", "섞임"],
    "시각만 변경": ["변경", "변경", "변경", "변경", "보류", "보류"],
    "그때까지 바쁨": ["괜찮음", "괜찮음", "미룸", "미룸", "결론 없음", "결론 없음"],
    "같은 요일": ["요일만", "요일만", "요일만", "이번주", "이번주", "이번주"],
}

MIX = {
    "confirm": 3,
    "proposal_only": 3,
    "revise": 4,
    "cancel": 2,
    "partial_absent": 3,
    "relative_date": 3,
    "not_schedule": 2,
}

# relative_date 카드에서 꼭 쓰게 할 표현. {d}는 요일 한 글자
REL_EXPR = [
    "다음주 {d}",
    "담주 {d}요일",
    "다다음주 {d}",
    "이번 주말",
    "글피",
    "모레",
    "{m}/{dd}의 그 다음주 {d}요일",
    "{m}/{dd}의 그 전주 {d}요일",
]

RULES = (
    "- 카드에 적힌 문장을 그대로 읽지 말고 평소 단톡처럼 말하기 (줄임말, 오타, ㅇㅋ, ㄱㄱ 다 괜찮음)\n"
    "- 8~20턴 정도. 흐름이 자연스럽게 바뀌어도 괜찮다 (정답은 대화를 보고 나중에 단다)\n"
    "- 날짜는 '10월 15일'처럼 풀어 쓰지 말고 카드에 적힌 표현으로 말하기\n"
    "- 닉네임을 카드의 이름으로 바꾸고 하기. 끝나면 대화 내보내기 파일을 담당자에게 주기\n"
    "- 진짜 개인정보(실명, 학교, 전화번호)는 말하지 않기"
)


def _wd(d: dt.date) -> str:
    return WD[d.weekday()]


def _final(word: str) -> int:
    """마지막 글자의 받침 번호 (0이면 받침 없음, 8이면 ㄹ). 한글이 아니면 0."""
    ch = word[-1]
    return (ord(ch) - 0xAC00) % 28 if "가" <= ch <= "힣" else 0


def _josa(word: str, with_final: str, without: str) -> str:
    """받침에 맞는 조사 붙이기: _josa("점심시간", "으로", "로") → "점심시간으로"."""
    f = _final(word)
    if with_final == "으로" and f == 8:  # ㄹ 받침은 "로" (교실로)
        return word + without
    return word + (with_final if f else without)


def _ref(rng: random.Random) -> dt.datetime:
    day = dt.date(2026, 10, 1) + dt.timedelta(days=rng.randint(0, 75))
    return dt.datetime(2026, day.month, day.day, rng.choice([20, 21, 22]), rng.choice([0, 10, 30, 40]))


def _say_day(ref: dt.date, rng: random.Random) -> str:
    """이번 주 안쪽의 날을 사람들이 부르는 방식 하나."""
    off = rng.randint(1, 6)
    d = ref + dt.timedelta(days=off)
    return rng.choice([f"{_wd(d)}요일", _wd(d), f"{d.month}/{d.day}"] + (["내일"] if off == 1 else []))


def _existing(ref: dt.date, rng: random.Random, people: list[str]) -> dict:
    d = ref + dt.timedelta(days=rng.randint(1, 6))
    hour = rng.choice([16, 17, 19, 20])
    return {
        "title": rng.choice(TITLES),
        "date": d.isoformat(),
        "weekday": _wd(d),
        "start": f"{hour:02d}:00",
        "time_text": None,
        "location": rng.choice(PLACES),
        "status": "confirmed",
        "attendees": people,
        "absent": [],
        "note": None,
    }


def make_card(cid: str, kind: str, rng: random.Random) -> dict:
    ref = _ref(rng)
    people = rng.sample(NAMES, rng.randint(3, 5))
    lead, other, *rest = people
    title, place, time = rng.choice(TITLES), rng.choice(PLACES), rng.choice(TIMES)
    day = _say_day(ref.date(), rng)
    existing = []
    roles: dict[str, str] = {}
    quiet = "흐름 보고 자유롭게 반응하기"

    if kind == "confirm":
        situation = f"{title} 날짜를 정해야 한다."
        roles[lead] = f"{day} {time} {place}에서 하자고 먼저 제안하기. 다들 괜찮으면 '그럼 그때 보자'처럼 정리하기"
        roles[other] = "좋다고 하기"
        for p in rest:
            roles[p] = rng.choice(["동의하기", "조금 늦게 와서 ㅇㅋ 하기", quiet])
    elif kind == "proposal_only":
        situation = f"{title} 얘기가 나왔지만 아직 정해지면 안 된다."
        roles[lead] = f"{day} {time} 어떠냐고 물어보기. 확정 선언은 하지 않기"
        roles[other] = "'음 일정 봐야 할 듯', '아마?'처럼 애매하게만 답하기"
        for p in rest:
            roles[p] = rng.choice(["답 안 하기 (대화에 참여 X)", "다른 얘기로 새기", "'난 아무때나'라고만 하기"])
    elif kind == "revise":
        n_rev = rng.choice([1, 1, 2])
        situation = f"{title} 날짜를 정하는데 처음 안이 {n_rev}번 바뀐다."
        day2 = _say_day(ref.date(), rng)
        roles[lead] = f"{day} {time} 제안하기. 안 된다는 사람이 나오면 다른 안 받아들이기"
        roles[other] = f"{rng.choice(EXCUSES)} 때문에 안 된다고 하고 {_josa(f'{day2} {rng.choice(TIMES)}', '으로', '로')} 바꾸자고 하기"
        if n_rev == 2 and rest:
            roles[rest[0]] = f"바뀐 안도 애매하다고 하고 시간만 한 번 더 바꾸자고 하기 ({rng.choice(TIMES)})"
            rest = rest[1:]
        for p in rest:
            roles[p] = "마지막 안에 동의하기"
    elif kind == "cancel":
        ex = _existing(ref.date(), rng, people)
        existing = [ex]
        d = dt.date.fromisoformat(ex["date"])
        situation = f"캘린더에 이미 {_wd(d)}요일 {int(ex['start'][:2]) - 12}시 {_josa(ex['title'], '이', '가')} 잡혀 있다."
        cancel_mode = rng.choice(["취소", "취소", "보류"])
        if cancel_mode == "취소":
            roles[lead] = f"사정이 생겨서 {_wd(d)}요일 거 취소하자고 하기"
            roles[other] = "아쉽지만 동의하기"
        else:
            roles[lead] = f"{_wd(d)}요일 거 못 할 것 같다고 말만 꺼내기"
            roles[other] = "'일단 그건 나중에 다시 얘기하자'고 하고 결론 내지 않기"
        for p in rest:
            roles[p] = rng.choice(["동의하기", quiet])
    elif kind == "partial_absent":
        situation = f"{title} 날짜를 정하는데 한 명은 못 온다."
        roles[lead] = f"{day} {time} {place} 제안하기. 정리 멘트로 마무리하기"
        roles[other] = f"{rng.choice(EXCUSES)} 때문에 못 간다고 하기. 나머지끼리 하라고 하기"
        if rest:
            roles[rest[0]] = rng.choice(["30분 늦게 갈 수 있다고 하기", "동의하기"])
        for p in rest[1:]:
            roles[p] = "동의하기"
    elif kind == "relative_date":
        target = ref.date() + dt.timedelta(days=rng.randint(10, 25))
        expr = rng.choice(REL_EXPR).format(d=_wd(target), m=target.month, dd=target.day)
        situation = f"{title} 날짜를 '{expr}'{'이라는' if _final(expr) else '라는'} 표현으로 정한다."
        roles[lead] = f"'{expr} {time}' 어떠냐고 제안하기. 날짜는 반드시 이 표현으로 말하기"
        roles[other] = "좋다고 하기"
        for p in rest:
            roles[p] = rng.choice(["동의하기", "'그게 며칠이지?'라고 묻고 나서 동의하기"])
    else:  # not_schedule
        situation = "날짜 얘기는 나오지만 우리끼리 약속은 잡지 않는다."
        topic = rng.choice(["수행평가 마감", "중간고사 범위", "발표 순서 공지", "체육대회"])
        roles[lead] = f"{day} {topic} 얘기 꺼내기 (학교 일정이지 우리 약속이 아님)"
        roles[other] = "'시험 끝나면 담에 놀러가자' 같은 막연한 말 하기. 날짜는 정하지 않기"
        for p in rest:
            roles[p] = quiet

    for p in people:
        roles.setdefault(p, quiet)
    return {
        "card_id": cid,
        "type": kind,
        "ref_datetime": ref.strftime("%Y-%m-%dT%H:%M:00+09:00"),
        "participants": people,
        "situation": situation,
        "roles": roles,
        "existing_events": existing,
    }


def make_hard_card(cid: str, weak: str, flow: str, rng: random.Random) -> dict:
    """약점 카드. type은 카드대로 흘러갔을 때의 예상일 뿐이고, 정답은 대화를 보고 정한다."""
    ref = _ref(rng)
    people = rng.sample(NAMES, rng.randint(3, 4))
    lead, other, *rest = people
    title, place = rng.choice(TITLES), rng.choice(PLACES)
    day, time = _say_day(ref.date(), rng), rng.choice(TIMES)
    existing = []
    roles: dict[str, str] = {}
    vague = "된다고도 안 된다고도 하지 말고 나중에 봐서 정하겠다는 식으로만 답하기 (말은 자기 식으로)"

    if weak == "애매한 답":
        situation = f"{title} 날짜를 물어봤는데 답이 시원하지 않다."
        roles[lead] = f"{day} {time} 어떠냐고 물어보기. 확정 선언은 하지 않기"
        roles[other] = vague
        if flow == "모두 애매":
            kind = "proposal_only"
            for p in rest:
                roles[p] = vague
        elif flow == "한 명만 애매":
            kind = "confirm"
            roles[lead] = f"{day} {time} {place}에서 하자고 제안하기. 된다는 사람이 과반이면 '그럼 그때 보자'처럼 정리하기"
            for p in rest:
                roles[p] = "확실하게 된다고 하기"
        else:
            kind = "confirm"
            roles[lead] = f"{day} {time} 제안하기. 모두 확실히 된다고 하면 그때 정리하기"
            roles[other] = "처음에는 애매하게 답했다가, 조금 뒤에 확실히 된다고 하기"
            for p in rest:
                roles[p] = "확실하게 된다고 하기"
    elif weak == "시각만 변경":
        ex = _existing(ref.date(), rng, people)
        existing = [ex]
        d, hour = dt.date.fromisoformat(ex["date"]), int(ex["start"][:2]) - 12
        new = rng.choice([h for h in (4, 5, 6, 7) if h != hour])
        situation = f"캘린더에 이미 {_wd(d)}요일 {hour}시 {_josa(ex['title'], '이', '가')} 잡혀 있다. 날짜는 그대로 둔다."
        if flow == "변경":
            kind = "revise"
            roles[lead] = f"{_wd(d)}요일 거 {_josa(f'{new}시', '으로', '로')} 시간만 바꾸자고 하기 ('취소'라는 말은 쓰지 않기)"
            roles[other] = "좋다고 하기"
            for p in rest:
                roles[p] = "동의하기"
        else:
            kind = "proposal_only"
            roles[lead] = f"{_wd(d)}요일 거 {_josa(f'{new}시', '으로', '로')} 시간만 바꿀 수 있냐고 물어보기. 혼자 정하지 않기"
            roles[other] = "확인해 보고 말해 주겠다고만 하기"
            for p in rest:
                roles[p] = "답 안 하기 (대화에 참여 X)"
    elif weak == "그때까지 바쁨":
        busy = rng.choice([5, 6])
        why = rng.choice(EXCUSES)
        situation = f"{title} 시각을 정하는데 한 명이 {busy}시까지 바쁘다."
        if flow == "괜찮음":
            kind = "confirm"
            roles[lead] = f"{day} {busy + 1}시 {place} 제안하기. 다들 되면 정리하기"
            roles[other] = f"{busy}시까지 {why} 있다고 말하고, 제안한 시각은 된다고 하기. 새 시각을 제안하지는 않기"
        elif flow == "미룸":
            kind = "revise"
            roles[lead] = f"{day} {busy - 1}시 제안하기. 안 되는 사람이 있으면 {_josa(f'{busy + 1}시', '으로', '로')} 늦추고 정리하기"
            roles[other] = f"{busy}시까지 {why} 있다고만 말하기. 새 시각을 먼저 제안하지는 않기. 늦춰 주면 좋다고 하기"
        else:
            kind = "proposal_only"
            roles[lead] = f"{day} {busy - 1}시 어떠냐고 물어보기. 안 되는 사람이 있으면 '그럼 다시 생각해 보자'로 끝내기"
            roles[other] = f"{busy}시까지 {why} 있다고만 말하기. 새 시각을 제안하지는 않기"
        for p in rest:
            roles[p] = "답 안 하기 (대화에 참여 X)" if kind == "proposal_only" else "동의하기"
    else:  # 같은 요일
        kind = "relative_date"
        ref = ref.replace(hour=12, minute=40)  # "오늘"이 아직 남아 있어야 이번주 쪽이 말이 된다
        w = _wd(ref.date())
        time = rng.choice(["5시", "6시", "7시", "7시 반"])
        if flow == "요일만":
            say = rng.choice([w, f"{w}요일"])
            situation = f"오늘은 {w}요일이다. 오늘 {_josa(title, '을', '를')} 하려고 한다."
            roles[lead] = f"'{say} {time}'처럼 요일만 말해서 제안하기 ('오늘', '이번주'라는 말, 날짜 숫자는 쓰지 않기)"
        else:
            say = f"이번주 {w}"
            situation = f"오늘은 {w}요일이다. 오늘 {_josa(title, '을', '를')} 하려고 한다."
            roles[lead] = f"'{say} {time}'처럼 말해서 제안하기 ('오늘'이라는 말, 날짜 숫자는 쓰지 않기)"
        roles[other] = "좋다고 하기"
        for p in rest:
            roles[p] = "동의하기"

    return {
        "card_id": cid,
        "type": kind,
        "weakness": weak,
        "ref_datetime": ref.strftime("%Y-%m-%dT%H:%M:00+09:00"),
        "participants": people,
        "situation": situation,
        "roles": roles,
        "existing_events": existing,
    }


def to_md(c: dict) -> str:
    ref = dt.datetime.fromisoformat(c["ref_datetime"])
    lines = [
        f"## 카드 {c['card_id']}",
        "",
        f"- 지금: {ref.month}월 {ref.day}일({_wd(ref.date())}) {ref.hour}시 {ref.minute:02d}분, 팀 단톡",
        f"- 참여자: {', '.join(c['participants'])}",
        f"- 상황: {c['situation']}",
        "",
        "| 이름 | 내 역할 (나만 보기) |",
        "|---|---|",
    ]
    lines += [f"| {p} | {r} |" for p, r in c["roles"].items()]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=SETS, default="real")
    ap.add_argument("--n", type=int, help="real, pilot의 장수 (hard는 WEAK에 적힌 대로)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--out")
    args = ap.parse_args()

    letter, n, seed, folder, _ = SETS[args.set]
    n = n if args.n is None else args.n
    rng = random.Random(seed if args.seed is None else args.seed)
    if args.set == "hard":
        plan = [(w, f) for w, flows in WEAK.items() for f in flows]
        rng.shuffle(plan)
        cards = [make_hard_card(f"{letter}{i + 1:03d}", w, f, rng) for i, (w, f) in enumerate(plan)]
    else:
        unit = [k for k, w in MIX.items() for _ in range(w)]  # 20장 묶음
        kinds = (unit * (n // len(unit) + 1))[:n]
        rng.shuffle(kinds)
        cards = [make_card(f"{letter}{i + 1:03d}", k, rng) for i, k in enumerate(kinds)]

    out = Path(args.out or folder)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "cards.jsonl").open("w", encoding="utf-8") as f:
        for c in cards:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    md = ["# 역할극 시나리오 카드", "", "## 모두 지킬 것", "", RULES, ""]
    md += [to_md(c) for c in cards]
    (out / "cards.md").write_text("\n".join(md), encoding="utf-8")

    key, names = ("weakness", WEAK) if args.set == "hard" else ("type", MIX)
    counts = {k: sum(c[key] == k for c in cards) for k in names}
    print(f"{len(cards)}장 → {out}/cards.md, cards.jsonl  {counts}")


if __name__ == "__main__":
    main()
