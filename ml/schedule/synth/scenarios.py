"""Step 1 of plan 8.6: build the answer first, then ask an LLM to write a chat
that leads to it. The label is known by construction and follows plan 8.3:

  confirmed = a majority including the proposer agrees and nobody objects, or
              an explicit declaration ("그럼 그때 보자") + at least one agreement.

Dates and times are written the way students write them. A date expression is
only used if dateparse_ko resolves it to the gold date.
"""

import datetime as dt
import random

from dateparse_ko import parse

WEEKDAYS = "월화수목금토일"
NAMES = """민준 서연 지호 하은 도윤 서준 지우 예린 현우 수아 시우 하린 주원 지민 건우
유나 준서 채원 은우 소율 태윤 다은 승현 나연 연우 윤서 재민 가은 민재 아린""".split()
TITLES = ["팀 회의", "발표 연습", "자료 조사 회의", "최종 점검", "스터디", "역할 분담 회의",
          "PPT 회의", "대본 맞추기", "보고서 정리", "중간 점검"]  # fmt: skip
# (name stored in the answer, how people say it). None = no place is mentioned.
PLACES = [("디스코드", "디스코드"), ("디스코드", "디코"), ("줌", "줌"), ("학교 도서관", "학교 도서관"),
          ("도서관", "도서관"), ("스터디카페", "스터디카페"), ("스터디카페", "스카"), ("카페", "카페"),
          ("교실", "교실"), ("스터디룸", "스터디룸"), (None, None), (None, None), (None, None)]  # fmt: skip
NON_NUMERIC = ["점심시간", "방과후", "수업 끝나고"]  # plan 8.5: kept as words
# type → weight. The hard cases (not yet agreed, changed plans) get the most.
TYPES = {
    "confirm": 3,
    "proposal_only": 3,
    "revise": 4,
    "cancel": 2,
    "reschedule": 2,
    "partial_absent": 3,
    "two_events": 1,
    "rejected": 1,
    "not_schedule": 2,
}
STYLES = [
    "단답 위주", "ㅋㅋ를 많이 쓰는 장난스러운 말투", "오타와 띄어쓰기 실수가 많음",
    "초성 줄임말을 많이 씀", "한 명은 존댓말을 쓰는 후배", "문장부호를 거의 안 씀",
    "한 사람이 짧은 메시지를 연달아 보내기도 함", "말끝을 흐리는 말투 (~듯, ~려나)",
    "또박또박한 말투", "다들 바빠서 답이 짧고 건조함",
]  # fmt: skip
SMALL_TALK = [
    None, None, "시험 범위", "동아리", "웹툰", "게임", "날씨", "버스 놓친 얘기", "체육 수업",
    "편의점 신상", "좋아하는 가수", "폰 배터리", "학원 숙제", "주말에 본 영화", "축제 준비",
]  # fmt: skip
AGREE = ["ㅇㅋ", "ㄱㄱ", "ㅇㅇ", "콜", "좋음", "난 됨", "그때 괜찮아", "오케이", "가능", "ㅇㅈ",
         "굿", "그래", "좋아", "ok", "ㅇㅋㅇㅋ", "나쁘지 않네", "그날 비어있음", "맞춰볼게"]  # fmt: skip


def _date_of(text: str, ref: dt.date) -> dt.date | None:
    dates = [m.date for m in parse(f"{text} 회의", ref) if m.date]
    return dates[0] if len(dates) == 1 else None


def _say_date(ref: dt.date, d: dt.date, rng: random.Random) -> str:
    """How people in the chat refer to `d`. Bare "일" is left out: it is also a common word."""
    w = WEEKDAYS[d.weekday()]
    options = [f"{w}요일", f"{w}욜", f"이번주 {w}", f"이번 주 {w}요일", f"담주 {w}", f"다음주 {w}",
               f"다음 주 {w}요일", f"다다음주 {w}", f"{d.month}/{d.day}", f"{d.month}월 {d.day}일"]  # fmt: skip
    if w != "일":
        options.append(w)
    delta = (d - ref).days
    if delta <= 3:
        options += {0: ["오늘"], 1: ["내일", "낼"], 2: ["모레"], 3: ["글피"]}[delta] * 2
    return rng.choice([o for o in options if _date_of(o, ref) == d])


def _say_time(rng: random.Random) -> tuple[dt.time | None, str | None, str]:
    """(start, time_text, how it is said). Bare 8~11시 is ambiguous (plan 8.5), so it is avoided."""
    if rng.random() < 0.1:
        words = rng.choice(NON_NUMERIC)
        return None, words, words
    h = rng.choices([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
                    [2, 2, 2, 3, 4, 5, 6, 7, 7, 8, 6, 3])[0]  # fmt: skip
    m = rng.choice([0, 0, 0, 30])
    half = " 반" if m else ""
    if h < 12:
        options = [f"오전 {h}시{half}", f"아침 {h}시{half}"]
    elif h == 12:
        options = [f"낮 12시{half}", f"12시{half}"]
    elif h <= 19:
        n = h - 12
        options = [f"{n}시{half}"] * 4 + [f"오후 {n}시{half}", f"{h}시{half}"]
        if h >= 17:
            options.append(f"저녁 {n}시{half}")
    else:
        n = h - 12
        options = [f"저녁 {n}시{half}", f"밤 {n}시{half}", f"오후 {n}시{half}", f"{h}시{half}"]
    return dt.time(h, m), None, rng.choice(options)


def _slot(ref, rng, used: set, title=None) -> dict:
    """One possible meeting: when, where, what, and how the chat says it."""
    while True:
        d = ref + dt.timedelta(days=rng.choices(range(17), [2] + [5] * 7 + [3] * 7 + [1] * 2)[0])
        if d not in used:
            break
    used.add(d)
    start, time_text, say_time = _say_time(rng)
    place, say_place = rng.choice(PLACES)
    return {
        "d": d,
        "start": start,
        "time_text": time_text,
        "say_date": _say_date(ref, d, rng),
        "say_time": say_time,
        "place": place,
        "say_place": say_place,
        "title": title or rng.choice(TITLES),
    }


def _pitch(s: dict) -> str:
    where = f", 장소 '{s['say_place']}'" if s["say_place"] else ""
    return f"'{s['say_date']}' '{s['say_time']}'{where}, 일정 이름 '{s['title']}'"


def _must(*slots) -> list[str]:
    """Words the chat has to contain, or its label can't be trusted."""
    out = []
    for s in slots:
        out += [s["say_date"], s["say_time"], s["title"]]
        if s["say_place"]:
            out.append(s["say_place"])
    return out


def _event(s, status, attendees=(), absent=(), note=None) -> dict:
    return {
        "title": s["title"],
        "date": s["d"].isoformat(),
        "weekday": WEEKDAYS[s["d"].weekday()],
        "start": s["start"].strftime("%H:%M") if s["start"] else None,
        "time_text": s["time_text"],
        "location": s["place"],
        "status": status,
        "attendees": list(attendees),
        "absent": list(absent),
        "note": note,
    }


def _need(n: int) -> int:
    """People needed for a majority, proposer included."""
    return n // 2 + 1


def _settle(lead, people, rng) -> tuple[list[str], bool]:
    """Beats that make `lead`'s proposal confirmed. Most chats don't say "확정"."""
    others = [p for p in people if p != lead]
    mode = rng.choices(["majority", "see_you", "declare"], [45, 35, 20])[0]
    if mode == "majority":
        yes = rng.sample(others, rng.randint(_need(len(people)) - 1, len(others)))
        rest = [p for p in others if p not in yes]
        beats = [f"{', '.join(yes)}: 각자 동의. 아무도 결론을 정리하지 않고 동의로 대화가 끝남"]
        if rest:
            beats.append(f"{', '.join(rest)}: 이 일정에 끝까지 아무 답도 안 함")
        return beats, False
    yes = rng.sample(others, rng.randint(1, len(others)))
    if mode == "see_you":
        return [f"{', '.join(yes)}: 동의", f"{lead}: '그럼 그때 보자'처럼 약속을 마무리하는 말을 함"], False
    return [f"{', '.join(yes)}: 동의", f"{lead}: '확정'이라는 말로 결론을 냄"], True


def _confirm(ref, people, rng, used):
    s, lead = _slot(ref, rng, used), people[0]
    beats, word = _settle(lead, people, rng)
    return [f"{lead}: {_pitch(s)} 제안", *beats], [_event(s, "confirmed", people)], [], _must(s), word


def _proposal_only(ref, people, rng, used):
    s, lead = _slot(ref, rng, used), people[0]
    others = people[1:]
    below = _need(len(people)) - 2  # the most agreements that still aren't a majority
    variant = rng.choice(["no_reply", "vague", "minority", "split"])
    if variant == "minority" and below >= 1:
        yes = rng.sample(others, below)
        tail = [f"{', '.join(yes)}: 동의",
                f"{', '.join(p for p in others if p not in yes)}: 이 일정에 끝까지 답이 없음"]  # fmt: skip
    elif variant == "split" and len(others) >= 2:
        no, *rest = rng.sample(others, len(others))
        tail = [f"{no}: 그때는 곤란하다고 함", f"{rest[0]}: 자기는 괜찮다고 함",
                "대안이 나오지 않고 결론 없이 다른 얘기로 넘어감"]  # fmt: skip
    elif variant == "vague":
        tail = [f"{rng.choice(others)}: '아마 될 듯', '봐야 알 듯'처럼 애매하게만 답함",
                "나머지는 답이 없고 결론 없이 끝남"]  # fmt: skip
    else:
        tail = ["아무도 이 제안에 답하지 않고 다른 얘기만 함"]
    return [f"{lead}: {_pitch(s)} 제안", *tail], [_event(s, "proposed", people)], [], _must(s), False


def _revise(ref, people, rng, used):
    title = rng.choice(TITLES)
    slots = [_slot(ref, rng, used, title) for _ in range(rng.choice([2, 2, 3]))]
    *old, final = slots
    beats = []
    for i, s in enumerate(old):
        who = people[i % len(people)]
        no = people[(i + 1) % len(people)]
        beats += [f"{who}: {_pitch(s)} 제안", f"{no}: 그때는 안 된다고 함"]
    lead = people[len(old) % len(people)]
    beats.append(f"{lead}: 대신 {_pitch(final)}(으)로 다시 제안")
    events = [_event(s, "rejected") for s in old]
    word = False
    if rng.random() < 0.15:
        beats.append("이 마지막 제안에는 아무도 답하지 않고 대화가 끝남")
        events.append(_event(final, "proposed", people))
    else:
        settle, word = _settle(lead, people, rng)
        beats += settle
        events.append(_event(final, "confirmed", people))
    return beats, events, [], _must(*slots), word


def _cancel(ref, people, rng, used):
    s, lead = _slot(ref, rng, used), rng.choice(people)
    old = _event(s, "confirmed", people)
    beats = [f"{lead}: 이미 잡혀 있던 {_pitch(s)} 일정을 꺼내면서 사정이 생겼으니 취소하자고 함"]
    if rng.random() < 0.2:  # someone wants to cancel, the team keeps it
        others = ", ".join(p for p in people if p != lead)
        beats.append(f"{others}: 그래도 그냥 하자고 함. {lead}도 알겠다고 해서 일정은 그대로 유지됨")
        return beats, [old], [old], _must(s), False
    settle, word = _settle(lead, people, rng)
    beats += [b.replace("동의", "취소에 동의") for b in settle]
    return beats, [_event(s, "cancelled", people)], [old], _must(s), word


def _reschedule(ref, people, rng, used):
    title = rng.choice(TITLES)
    old, new = _slot(ref, rng, used, title), _slot(ref, rng, used, title)
    lead = rng.choice(people)
    settle, word = _settle(lead, people, rng)
    beats = [f"{lead}: 이미 잡혀 있던 {_pitch(old)} 일정을 {_pitch(new)}(으)로 옮기자고 함", *settle]
    return beats, [_event(new, "confirmed", people)], [_event(old, "confirmed", people)], _must(old, new), word


def _partial_absent(ref, people, rng, used):
    s, lead = _slot(ref, rng, used), people[0]
    others = people[1:]
    variant = rng.choice(["absent", "absent", "late", "both"]) if len(others) >= 2 else "late"
    n_absent = 2 if variant != "late" and len(people) == 5 and rng.random() < 0.3 else 1
    absent = rng.sample(others, n_absent) if variant != "late" else []
    going = [p for p in others if p not in absent]
    beats = [f"{lead}: {_pitch(s)} 제안"]
    if absent:
        beats.append(f"{', '.join(absent)}: 그때 못 온다고 함. 대신 나머지끼리 하라고 함")
    must, note = _must(s), None
    if variant != "absent" and going:
        late = rng.choice(going)
        minutes = rng.choice([10, 15, 20, 30])
        beats.append(f"{late}: 참석은 하는데 '{minutes}분' 늦는다고 함")
        must.append(f"{minutes}분")
        note = f"{late} {minutes}분 늦게 합류"
    beats.append(f"{', '.join(going)}: 동의. 이대로 진행하기로 함")
    attendees = [p for p in people if p not in absent]
    return beats, [_event(s, "confirmed", attendees, absent, note)], [], must, False


def _two_events(ref, people, rng, used):
    a_title, b_title = rng.sample(TITLES, 2)
    a, b = _slot(ref, rng, used, a_title), _slot(ref, rng, used, b_title)
    lead, second = people[0], people[1]
    settle, word = _settle(lead, people, rng)
    beats = [f"{lead}: {_pitch(a)} 제안", *settle, f"이어서 {second}: 별개의 일정으로 {_pitch(b)} 제안"]
    if rng.random() < 0.5:
        settle2, word2 = _settle(second, people, rng)
        return beats + settle2, [_event(a, "confirmed", people), _event(b, "confirmed", people)], [], _must(a, b), word or word2
    beats.append("두 번째 제안에는 아무도 답하지 않고 대화가 끝남")
    return beats, [_event(a, "confirmed", people), _event(b, "proposed", people)], [], _must(a, b), word


def _rejected(ref, people, rng, used):
    s, lead = _slot(ref, rng, used), people[0]
    others = ", ".join(people[1:])
    beats = [f"{lead}: {_pitch(s)} 제안", f"{others}: 그때는 다 안 된다고 함",
             f"{lead}: 그럼 그 일정은 없던 걸로 하자고 하고, 다른 날짜는 정하지 않음"]  # fmt: skip
    return beats, [_event(s, "rejected")], [], _must(s), False


def _not_schedule(ref, people, rng, used):
    variant = rng.choice(["external", "past", "vague"])
    if variant == "external":
        s = _slot(ref, rng, used)
        what = rng.choice(["수학 시험", "수행평가 제출", "영어 듣기평가", "학교 축제", "발표 수업"])
        beats = [f"'{s['say_date']}'에 있는 학교 일정 '{what}' 얘기를 함. 팀 모임을 잡자는 말은 안 함"]
        return beats, [], [], [s["say_date"], what], False
    if variant == "past":
        when = rng.choice(["어제", "그저께", "지난주"])
        return [f"{when} 했던 팀 회의가 어땠는지 뒷얘기만 함. 앞으로의 약속은 안 잡음"], [], [], [when], False
    return ["날짜 없이 '담에 놀러가자', '언제 밥 한번 먹자' 같은 막연한 말만 오가고 아무도 날짜를 안 정함"], [], [], [], False


BUILDERS = {
    "confirm": _confirm,
    "proposal_only": _proposal_only,
    "revise": _revise,
    "cancel": _cancel,
    "reschedule": _reschedule,
    "partial_absent": _partial_absent,
    "two_events": _two_events,
    "rejected": _rejected,
    "not_schedule": _not_schedule,
}


def _unrelated_events(ref, people, rng, used, taken) -> list[dict]:
    """Calendar entries the chat never talks about. The model must leave them alone."""
    titles = [t for t in TITLES if t not in taken]
    count = rng.choices([0, 1, 2], [5, 3, 2])[0]
    return [_event(_slot(ref, rng, used, t), "confirmed", people) for t in rng.sample(titles, count)]


def make(rng: random.Random) -> dict:
    """One scenario: what the LLM must act out + the gold events."""
    ref = dt.date(2026, 3, 1) + dt.timedelta(days=rng.randint(0, 305))
    people = rng.sample(NAMES, rng.choices([3, 4, 5], [3, 4, 2])[0])
    kind = rng.choices(list(TYPES), weights=TYPES.values())[0]
    used: set = set()
    beats, events, existing, must, says_confirm = BUILDERS[kind](ref, people, rng, used)
    taken = {e["title"] for e in events + existing}
    length = rng.choices([(6, 10), (11, 16), (17, 24)], [55, 35, 10])[0]
    return {
        "type": kind,
        "ref_date": ref.isoformat(),
        "participants": people,
        "beats": beats,
        "must": must,
        "says_confirm": says_confirm,
        "events": events,
        "existing_events": existing + _unrelated_events(ref, people, rng, used, taken),
        "style": rng.choice(STYLES),
        "small_talk": rng.choice(SMALL_TALK),
        "agree_words": rng.sample(AGREE, 4),
        "turns": rng.randint(*length),
        "start_minute": rng.randint(8 * 60, 22 * 60),
        "gaps": [rng.randint(0, 4) for _ in range(40)],
    }
