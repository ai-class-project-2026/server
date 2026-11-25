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
    return dt.time(h, m), None, _say_clock(h, m, rng)


def _say_clock(h: int, m: int, rng: random.Random) -> str:
    """How people say h:m (m is 0 or 30). Never a bare 8~11시."""
    assert m in (0, 30) and 10 <= h <= 22, (h, m)
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
    return rng.choice(options)


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
    events = [_event(old, "rejected"), _event(new, "confirmed", people)]  # the old time is dropped, not a cancelled meeting
    return beats, events, [_event(old, "confirmed", people)], _must(old, new), word


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


# 짧은 대화(2~4턴): 모임 이름이나 장소, 시간까지 생략하고 ㅇㅋ로 끝나는 실제 단톡 말투
NOUNS = [("목", "목이 아픈 얘기"), ("화", "화가 난 얘기"), ("금", "금값이나 금 목걸이 얘기"),
         ("수", "수학 문제의 수 얘기"), ("일", "할 일이 많다는 얘기"), ("토", "속이 안 좋아 토할 것 같다는 얘기")]  # fmt: skip


def _quiet(ref, rng, used, title="모임", has_time=True) -> dict:
    s = _slot(ref, rng, used, title) | {"place": None, "say_place": None}
    return s if has_time else s | {"start": None, "time_text": None, "say_time": ""}


def _when(s: dict) -> str:
    return f"'{s['say_date']}'" + (f" '{s['say_time']}'" if s["say_time"] else "")


def _said(*slots) -> list[str]:
    return [x for s in slots for x in (s["say_date"], s["say_time"]) if x]


def _short_confirm(ref, p, rng, used):
    s = _quiet(ref, rng, used)
    beats = [f"{p[0]}: {_when(s)}에 보자고 아주 짧게 제안. 무슨 모임인지와 장소는 말하지 않음", f"{p[1]}: 한두 글자로 동의"]
    if rng.random() < 0.6:
        beats.append(f"{p[2]}: 한두 글자로 동의")
    return beats, [_event(s, "confirmed", p)], [], _said(s), False


def _short_proposal(ref, p, rng, used):
    s = _quiet(ref, rng, used, has_time=rng.random() > 0.6)
    reply = rng.choice(["아직 모르겠다고 함", "학원 시간을 봐야 안다고 함", "물어보고 말해주겠다고 함", "생각해보겠다고 함"])
    beats = [f"{p[0]}: {_when(s)}에 볼지 짧게 물어봄. 무슨 모임인지와 장소는 말하지 않음",
             f"{p[1]}: {reply}. 동의나 거절은 하지 않음", "아무도 결론을 내지 않고 끝남"]  # fmt: skip
    return beats, [_event(s, "proposed", p)], [], _said(s), False


def _short_revise(ref, p, rng, used):
    old = _quiet(ref, rng, used)
    while True:
        start, time_text, say = _say_time(rng)
        if start and start != old["start"]:
            break
    if rng.random() < 0.6 and old["start"]:  # 같은 날 시간만 바꾼다
        new = old | {"start": start, "time_text": time_text, "say_time": say}
        said = [old["say_date"], old["say_time"], say]
        how = f"'{say}'"
    else:
        new = _quiet(ref, rng, used)
        said = _said(old, new)
        how = _when(new)
    beats = [f"{p[0]}: {_when(old)}에 보자고 짧게 제안", f"{p[1]}: 그때는 어렵다며 {how}은 어떠냐고 함", f"{p[2]}: 짧게 동의"]
    return beats, [_event(old, "rejected"), _event(new, "confirmed", p)], [], said, False


def _short_absent(ref, p, rng, used):
    s = _quiet(ref, rng, used)
    beats = [f"{p[0]}: {_when(s)}에 보자고 짧게 제안", f"{p[1]}: 짧게 동의", f"{p[2]}: 자기는 못 가니 둘이 하라고 함"]
    return beats, [_event(s, "confirmed", p[:2], [p[2]])], [], _said(s), False


def _short_cancel(ref, p, rng, used):
    s = _quiet(ref, rng, used, rng.choice(TITLES))
    old = _event(s, "confirmed", p)
    beats = [f"{p[0]}: '{s['say_date']}' '{s['title']}' 없애자고 짧게 말함. 시간은 말하지 않음", f"{p[1]}: 짧게 동의"]
    return beats, [_event(s, "cancelled", p)], [old], [s["say_date"], s["title"]], False


def _short_reschedule(ref, p, rng, used):
    title = rng.choice(TITLES)
    old, new = _quiet(ref, rng, used, title), _quiet(ref, rng, used, title)
    new |= {"start": old["start"], "time_text": old["time_text"], "say_time": old["say_time"]}
    beats = [f"{p[0]}: '{old['say_date']}' '{title}'을 '{new['say_date']}'로 옮기자고 함. 시간은 그대로라고 말함", f"{p[1]}: 짧게 동의", f"{p[2]}: 짧게 동의"]
    return beats, [_event(old, "rejected"), _event(new, "confirmed", p)], [_event(old, "confirmed", p)], [old["say_date"], title, new["say_date"]], False


def _short_noun(ref, p, rng, used):
    word, topic = rng.choice(NOUNS)
    return [f"{p[0]}: '{word}' 글자를 요일이 아닌 뜻으로 써서 {topic}을 함", f"{p[1]}: 짧게 맞장구"], [], [], [word], False


SHORT = {  # builder, weight
    "proposal_only": (_short_proposal, 4), "revise": (_short_revise, 3), "confirm": (_short_confirm, 2),
    "partial_absent": (_short_absent, 2), "reschedule": (_short_reschedule, 2), "cancel": (_short_cancel, 1),
    "not_schedule": (_short_noun, 2),
}  # fmt: skip


def make_short(rng: random.Random) -> dict:
    """2~4턴짜리 짧은 대화. 기본 시나리오에 없는 말투를 채운다."""
    ref = dt.date(2026, 3, 1) + dt.timedelta(days=rng.randint(0, 305))
    people = rng.sample(NAMES, 3)
    kind = rng.choices(list(SHORT), weights=[w for _, w in SHORT.values()])[0]
    used: set = set()
    beats, events, existing, must, says_confirm = SHORT[kind][0](ref, people, rng, used)
    return {
        "type": kind,
        "ref_date": ref.isoformat(),
        "participants": people,
        "beats": beats,
        "must": must,
        "says_confirm": says_confirm,
        "events": events,
        "existing_events": existing,
        "style": rng.choice(STYLES),
        "small_talk": None,
        "agree_words": rng.sample(AGREE, 4),
        "turns": rng.randint(2, 4),
        "min_turns": 2,
        "max_turns": 5,
        "start_minute": rng.randint(8 * 60, 22 * 60),
        "gaps": [rng.randint(0, 4) for _ in range(40)],
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


# ── make_focus: chats aimed at the four mistakes the model still makes ─────────────
# 1 hedge:        "ㅇㅇ 나도 봐서" sounds like a yes but only puts the decision off → proposed
# 2 time_shift:   "토 발표 연습 3시로 늦추자" on an event already on the calendar → old time
#                 rejected, new time confirmed, same day (or nothing changes if the team says no)
# 3 constraint:   "나 7시까지 학원" is a reason, not a slot. That time is never an event.
# 4 same_weekday: a bare weekday said on that weekday is next week; "이번주 X" is today.
# Phrases in `cues` must appear in the chat but are not dates or times of any event, so they
# are kept out of `must`. Each cue is a list of alternatives; offline.py checks them after
# generate.check().

# Each sounds positive or has an agreement word, but nobody commits.
HEDGES = ["음 봐서", "ㅇㅇ 나도 봐서", "ㅇㅇ 일단 보고 말해줄게", "그래 일단 생각해볼게", "ㅇㅋ 엄마한테 물어보고",
          "좋긴 한데 학원 시간 보고 말해줄게", "오 좋긴 한데 확인해볼게", "그래 봐서 말해줄게", "ㅇㅋ 스케줄 보고 알려줌",
          "ㅇㅇ 생각해볼게", "그래 일단 알아볼게", "ㅇㅋㅇㅋ 확인해보고 말해줄게", "음 그날 가봐야 알 듯",
          "ㅇㅇ 나중에 말해줄게", "좋긴 한데 아직 모르겠어", "나도 봐서", "ㅇㅋ 물어보고 말할게",
          "그래 학원 끝나는 시간 보고", "ㅇㅇ 확실해지면 말해줄게", "오 괜찮을지도? 물어보고"]  # fmt: skip
# how → (what the proposer says, stems that show it was said)
SHIFTS = {"earlier": ("앞당기자", ["앞당", "당기", "땡기", "땡겨"]), "later": ("늦추자", ["늦추", "늦춰", "늦출"]),
          "delay": ("미루자", ["미루", "미뤄", "미룰"]), "change": ("바꾸자", ["바꾸", "바꿔", "바꿀"])}  # fmt: skip
UNTIL = ["{t}까지 학원", "{t}까지 알바", "학원 {t}에 끝남", "{t}까지 수업 있음", "{t} 전엔 안 됨", "{t}까지 과외"]
FROM = ["{t}부터 됨", "{t} 이후로 가능", "{t}부터 시간 남"]
LEAVE = ["{t}까지만 됨", "{t} 전에 집 가야 됨"]


def _clock(minutes: int) -> dt.time:
    return dt.time(minutes // 60, minutes % 60)


def _times_in(text: str, ref: dt.date) -> set[str]:
    return {m.time.strftime("%H:%M") for m in parse(text, ref) if m.time}


def _speaking(beats: list[str], people: list[str]) -> int:
    """Turns the beats need at least: one per person named before the ':'."""
    return sum(len(set(b.split(":")[0].split(", ")) & set(people)) for b in beats if ":" in b and "답도 안 함" not in b)


def _day_after(ref, rng, used, lo=1, hi=16) -> dt.date:
    while True:
        d = ref + dt.timedelta(days=rng.randint(lo, hi))
        if d not in used:
            used.add(d)
            return d


def _at(ref, rng, d, minutes, title=None, quiet=False) -> dict:
    """A slot on day `d` at a given time, said the way students say it."""
    place, say_place = (None, None) if quiet else rng.choice(PLACES)
    return {
        "d": d,
        "start": _clock(minutes),
        "time_text": None,
        "say_date": _say_date(ref, d, rng),
        "say_time": _say_clock(minutes // 60, minutes % 60, rng),
        "place": place,
        "say_place": say_place,
        "title": title or ("모임" if quiet else rng.choice(TITLES)),
    }


def _moved(s: dict, minutes: int, rng) -> dict:
    """Same day, place and title at another time. Times that aren't :00/:30 aren't said."""
    say = _say_clock(minutes // 60, minutes % 60, rng) if minutes % 30 == 0 else ""
    return s | {"start": _clock(minutes), "time_text": None, "say_time": say}


def _words(s: dict, quiet: bool) -> list[str]:
    return _said(s) if quiet else [x for x in _must(s) if x]


def _clear(options: list[str], must: list[str]) -> list[str]:
    """Phrases that contain no required word ("줌" in "알려줌", "수" in "수업"): check() looks
    for each required word anywhere in the chat, so a phrase like that would hide its absence."""
    words = [w.replace(" ", "") for w in must]
    return [o for o in options if not any(w in o.replace(" ", "") for w in words)]


def _focus_hedge(ref, people, rng, used) -> dict:
    lead, others = people[0], people[1:]
    quiet = rng.random() < 0.5
    has_time = rng.random() < 0.65
    s = _quiet(ref, rng, used, has_time=has_time) if quiet else _slot(ref, rng, used)
    if not has_time:
        s |= {"start": None, "time_text": None, "say_time": ""}
    if quiet:
        pitch = f"{_when(s)}에 보자고 짧게 물어봄. 무슨 모임인지와 장소는 말하지 않음"
    elif has_time:
        pitch = f"{_pitch(s)} 제안"
    else:
        where = f", 장소 '{s['say_place']}'" if s["say_place"] else ""
        pitch = f"'{s['say_date']}'{where}, 일정 이름 '{s['title']}' 제안. 시간은 아직 말하지 않음"
    need = _need(len(people))
    variant = rng.choices(["two_turn", "chain", "mixed", "long", "resolved"], [15, 30, 20, 20, 15])[0]
    if variant == "mixed" and need - 2 < 1:  # 3 people: one plain yes would already be a majority
        variant = "chain"
    yes = []
    if variant == "two_turn":
        hedgers = [rng.choice(others)]
    elif variant == "mixed":
        yes = rng.sample(others, rng.randint(1, need - 2))  # proposer + yes stays below a majority
        rest = [p for p in others if p not in yes]
        hedgers = rng.sample(rest, rng.randint(1, len(rest)))
    else:
        least = need - 1 if variant == "resolved" else 2
        hedgers = rng.sample(others, rng.randint(least, len(others)))
    phrases = rng.sample(_clear(HEDGES, _words(s, quiet)), len(hedgers))
    beats = [f"{lead}: {pitch}"]
    beats += [f"{p}: '{h}'라고 답함 (이 말을 그대로 씀). 확답이 아니라 결정을 미룬 말" for p, h in zip(hedgers, phrases)]
    if yes:
        beats.append(f"{', '.join(yes)}: 분명하게 동의")
    if variant == "long":
        beats.append(f"{lead}: 다들 언제 알 수 있냐고 다시 물어봄")
        beats.append(f"{rng.choice(hedgers)}: 아직 모른다고, 정해지면 말해준다고 함")
    silent = [p for p in others if p not in hedgers + yes]
    status = "proposed"
    if variant == "resolved":  # contrast: the hedgers come back with a clear yes, so it is settled
        beats.append(f"{', '.join(rng.sample(hedgers, need - 1))}: 나중에 확인해 보니 된다고 분명하게 말함. 아무도 반대하지 않음")
        status = "confirmed"
    elif silent:
        beats.append(f"{', '.join(silent)}: 이 일정에 끝까지 아무 답도 안 함")
    if status == "proposed":
        beats.append("아무도 결론을 내지 않고 끝남. '그럼 그때 보자' 같은 마무리 말은 하지 않음")
    k = _speaking(beats, people)
    turns = {"two_turn": (2, 2), "long": (max(7, k), 10)}.get(variant, (k, k + 2))
    return {"variant": variant, "beats": beats, "events": [_event(s, status, people)], "existing": [],
            "must": _words(s, quiet), "cues": [[h] for h in phrases], "says_confirm": False, "turns": turns,
            "small_talk": variant == "long"}  # fmt: skip


def _shift_times(rng, how: str) -> tuple[int, int, int]:
    """(old minute, new minute, step) for a time-only change said with SHIFTS[how]."""
    while True:
        old = rng.randrange(11 * 60, 20 * 60 + 1, 30)
        if how == "delay":
            step = rng.choice([10, 15, 20, 30, 30, 40, 60])
        else:
            step = rng.choice([30, 60, 60, 90, 120, 180]) * (-1 if how == "earlier" else 1)
            if how == "change" and rng.random() < 0.5:
                step = -step
        new = old + step
        if 10 * 60 <= new <= 21 * 60 + 30:
            return old, new, step


def _shift_outcome(rng, lead, people, was, now, beats):
    """How the team answers a time-only change → (outcome, beats, events, says 확정, calendar entry)."""
    others = [p for p in people if p != lead]
    variant = rng.choices(["agree", "short_agree", "refuse", "pending"], [45, 20, 22, 13])[0]
    word = False
    keep = _event(was, "confirmed", people)
    if variant == "agree":
        settle, word = _settle(lead, people, rng)
        beats += settle
        events = [_event(was, "rejected"), _event(now, "confirmed", people)]
    elif variant == "short_agree":
        beats.append(f"{', '.join(rng.sample(others, _need(len(people)) - 1))}: 한두 글자로 동의")
        events = [_event(was, "rejected"), _event(now, "confirmed", people)]
    elif variant == "refuse":  # the team says no: the calendar entry stays, the new time is dropped
        no = rng.sample(others, rng.randint(1, len(others)))
        beats += [f"{', '.join(no)}: 바꾼 시간은 안 된다고 함",
                  f"{rng.choice(no)}: 그냥 원래 시간대로 하자고 함",
                  f"{lead}: 알겠다고 함. 일정은 원래 시간 그대로 유지됨"]  # fmt: skip
        events = [dict(keep), _event(now, "rejected")]
    else:  # nobody decides: the calendar entry stays, the new time is only proposed
        beats += [f"{rng.choice(others)}: '{rng.choice(HEDGES)}'처럼 확답 없이 미룸", "나머지는 답이 없고, 결론 없이 끝남"]
        events = [dict(keep), _event(now, "proposed", people)]
    return variant, beats, events, word, keep


def _focus_time_shift(ref, people, rng, used) -> dict:
    lead = rng.choice(people)
    title = rng.choice(TITLES)
    d = _day_after(ref, rng, used)
    how = rng.choices(list(SHIFTS), [25, 30, 20, 25])[0]
    old, new, step = _shift_times(rng, how)
    was = _at(ref, rng, d, old, title)
    now = _moved(was, new, rng)
    verb, stems = SHIFTS[how]
    say_old, say_title = rng.random() < 0.5, rng.random() < 0.65
    amount = ("1시간" if rng.random() < 0.5 else "한 시간") if step == 60 else f"{step}분"
    what = f"'{amount}' {verb}" if how == "delay" else f"'{now['say_time']}'(으)로 {verb}"
    pitch = f"'{was['say_date']}'" + (f" '{was['say_time']}'" if say_old else "") + (f" '{title}'" if say_title else "")
    beat = f"{lead}: 이미 잡혀 있던 {pitch} 일정을 날짜는 그대로 두고 시간만 {what}고 함 ('{verb}'처럼 말함)"
    beat += "" if say_old else ". 원래 시간은 말하지 않음"
    beat += "" if say_title else ". 일정 이름은 말하지 않고 날짜로만 가리킴"
    must = [was["say_date"]] + [was["say_time"]] * say_old + [title] * say_title
    must.append(amount if how == "delay" else now["say_time"])
    variant, beats, events, word, keep = _shift_outcome(rng, lead, people, was, now, [beat])
    k = _speaking(beats, people)
    return {"variant": f"{how}_{variant}", "beats": beats, "events": events, "existing": [keep], "must": must,
            "cues": [stems], "says_confirm": word, "turns": (k, k + 3),
            "small_talk": variant == "agree" and rng.random() < 0.4, "shift": [how, old, new, step]}  # fmt: skip


def _focus_constraint(ref, people, rng, used) -> dict:
    lead, others = people[0], people[1:]
    quiet = rng.random() < 0.4
    d = _day_after(ref, rng, used)
    variant = rng.choices(["late_reject", "first", "from", "window", "late_pending"], [40, 25, 15, 10, 10])[0]
    first = rng.randrange(15 * 60, (17 if variant == "window" else 19) * 60 + 1, 60)
    block = first + 60  # busy until / free from this time
    t1 = _at(ref, rng, d, first, quiet=quiet)
    t2 = _moved(t1, block + rng.choice([30, 60]), rng)
    who = rng.choice(others)
    must = _words(t2, quiet) if variant == "first" else _words(t1, quiet) + [t2["say_time"]]
    say_block = _say_clock(block // 60, 0, rng)
    phrase = rng.choice(_clear([x.format(t=say_block) for x in (FROM if variant == "from" else UNTIL)], must))
    cues = [[phrase]]
    limit = "그 시각부터 된다는" if variant == "from" else "그 시각까지는 안 된다는"
    reason = f"{who}: '{phrase}'라고 말함 (이 말을 그대로 씀). {limit} 사정일 뿐, 그 시각에 하자는 제안이 아님"
    if variant == "first":
        mover = lead
        beats = [reason.replace("라고 말함", "라고 먼저 말함"),
                 f"{lead}: 그럼 {_when(t2) if quiet else _pitch(t2)}(으)로 하자고 제안"]  # fmt: skip
        events = []
    else:
        mover = rng.choice(people)
        beats = [f"{lead}: {_when(t1)}에 보자고 제안" if quiet else f"{lead}: {_pitch(t1)} 제안", reason]
        if variant == "window":
            leave = block + rng.choice([150, 180])
            other = rng.choice([p for p in people if p != who])
            say_leave = _say_clock(leave // 60, leave % 60, rng)
            stop = rng.choice(_clear([x.format(t=say_leave) for x in LEAVE], must))
            cues.append([stop])
            beats.append(f"{other}: '{stop}'라고 말함 (이 말을 그대로 씀). 이것도 사정일 뿐 제안이 아님")
        beats.append(f"{mover}: 그럼 '{t2['say_time']}' 어떠냐고 다시 제안 (날짜는 그대로)")
        events = [_event(t1, "rejected")]
    word = False
    if variant == "late_pending":
        beats.append("이 새 제안에는 아무도 답하지 않고 대화가 끝남")
        events.append(_event(t2, "proposed", people))
    else:
        settle, word = _settle(mover, people, rng)
        beats += settle
        events.append(_event(t2, "confirmed", people))
    k = _speaking(beats, people)
    return {"variant": variant, "beats": beats, "events": events, "existing": [], "must": must, "cues": cues,
            "says_confirm": word, "turns": (k, k + 3), "small_talk": rng.random() < 0.3}  # fmt: skip


def _focus_same_weekday(ref, people, rng, used) -> dict:
    """The chat happens on weekday w and names w. Bare w → next week, 이번주 w → today."""
    w = WEEKDAYS[ref.weekday()]
    variant = rng.choices(["bare", "this_week", "next_week", "clarify"], [45, 25, 15, 15])[0]
    d = ref if variant == "this_week" else ref + dt.timedelta(days=7)
    used.add(d)
    bare = [f"{w}요일", f"{w}욜"] + ([w] if w != "일" else [])
    say = {"bare": bare, "clarify": bare, "this_week": [f"이번주 {w}", f"이번 주 {w}요일", f"이번주 {w}욜"],
           "next_week": [f"다음주 {w}", f"담주 {w}요일", f"다음 주 {w}요일"]}[variant]  # fmt: skip
    while True:
        start, time_text, say_time = _say_time(rng)
        if start and (variant != "this_week" or start.hour >= 15):  # today: still ahead of the chat
            break
    s = {"d": d, "start": start, "time_text": time_text, "say_date": rng.choice(say), "say_time": say_time,
         "place": None, "say_place": None, "title": "모임"}  # fmt: skip
    if variant != "this_week" and rng.random() < 0.3:
        s |= {"start": None, "time_text": None, "say_time": ""}
    p, must = people, _said(s)
    if variant == "clarify":
        again = rng.choice([f"담주 {w}요일", f"다음주 {w}"])
        beats = [f"{p[0]}: {_when(s)}에 보자고 짧게 제안. 오늘이 {w}요일이라 헷갈릴 수 있음",
                 f"{p[1]}: 오늘 말하는 거냐 다음 주 말하는 거냐고 물어봄",
                 f"{p[0]}: '{again}'라고 답함", f"{p[1]}, {p[2]}: 한두 글자로 동의"]  # fmt: skip
        return {"variant": variant, "beats": beats, "events": [_event(s, "confirmed", p)], "existing": [],
                "must": must + [again], "cues": [], "says_confirm": False, "turns": (5, 5), "small_talk": False}
    if rng.random() < 0.65:
        beats = [f"{p[0]}: {_when(s)}에 보자고 짧게 제안. 무슨 모임인지와 장소는 말하지 않음", f"{p[1]}: 한두 글자로 동의"]
        if rng.random() < 0.6:
            beats.append(f"{p[2]}: 한두 글자로 동의")
        status = "confirmed"
    else:
        reply = rng.choice(["아직 모르겠다고 함", "학원 시간을 봐야 안다고 함", "생각해보겠다고 함"])
        beats = [f"{p[0]}: {_when(s)}에 볼지 짧게 물어봄. 무슨 모임인지와 장소는 말하지 않음",
                 f"{p[1]}: {reply}. 동의나 거절은 하지 않음", "아무도 결론을 내지 않고 끝남"]  # fmt: skip
        status = "proposed"
    k = _speaking(beats, people)
    return {"variant": f"{variant}_{status}", "beats": beats, "events": [_event(s, status, p)], "existing": [],
            "must": must, "cues": [], "says_confirm": False, "turns": (k, k + 1), "small_talk": False}  # fmt: skip


def _focus_title_shift(ref, people, rng, used) -> dict:
    """time_shift where the chat never gives a numeric date: the event is named by its title
    ("발표 연습 3시로 늦추자") or by title + bare weekday ("토 발표 연습 3시로 늦추자"). The date
    comes from the calendar entry with that title (or from the weekday, which resolves to it)."""
    lead = rng.choice(people)
    title = rng.choice(TITLES)
    refer = rng.choice(["title", "title_weekday"])
    d = _day_after(ref, rng, used, 1, 7 if refer == "title_weekday" else 16)  # a bare weekday reaches 7 days
    how = rng.choices(list(SHIFTS), [25, 30, 20, 25])[0]
    old, new, step = _shift_times(rng, how)
    was = _at(ref, rng, d, old, title)
    if refer == "title":
        was["say_date"] = ""
    else:
        w = WEEKDAYS[d.weekday()]
        was["say_date"] = rng.choice([o for o in (w, f"{w}요일", f"{w}욜") if o != "일" and _date_of(o, ref) == d])
    now = _moved(was, new, rng)
    verb, stems = SHIFTS[how]
    say_old = rng.random() < 0.5
    amount = ("1시간" if rng.random() < 0.5 else "한 시간") if step == 60 else f"{step}분"
    what = f"'{amount}' {verb}" if how == "delay" else f"'{now['say_time']}'(으)로 {verb}"
    pitch = " ".join(f"'{x}'" for x in (was["say_date"], was["say_time"] if say_old else "", title) if x)
    beat = f"{lead}: 이미 잡혀 있던 {pitch} 일정을 날짜는 그대로 두고 시간만 {what}고 함 ('{verb}'처럼 말함)"
    if refer == "title":
        beat += ". 날짜는 말하지 않고 일정 이름으로만 가리킴 (요일, 오늘/내일, 몇 월 며칠도 말하지 않음)"
    else:
        beat += ". 날짜는 요일로만 말함 (몇 월 며칠, 이번주/다음주 같은 말은 붙이지 않음)"
    beat += "" if say_old else ". 원래 시간은 말하지 않음"
    must = [was["say_date"]] * (refer != "title") + [was["say_time"]] * say_old + [title]
    must.append(amount if how == "delay" else now["say_time"])
    variant, beats, events, word, keep = _shift_outcome(rng, lead, people, was, now, [beat])
    k = _speaking(beats, people)
    return {"variant": f"{refer}_{how}_{variant}", "beats": beats, "events": events, "existing": [keep],
            "must": must, "cues": [stems], "says_confirm": word, "turns": (k, k + 3), "refer": refer,
            "small_talk": variant == "agree" and rng.random() < 0.4, "shift": [how, old, new, step]}  # fmt: skip


def _focus_constraint_chosen(ref, people, rng, used) -> dict:
    """The other side of `constraint`: someone says "7시부터 됨", then 7시 itself is proposed in
    its own message and agreed. The constraint time *is* the answer here, so the model must not
    learn that a time first heard as a constraint can never be chosen."""
    lead, others = people[0], people[1:]
    quiet = rng.random() < 0.4
    d = _day_after(ref, rng, used)
    variant = rng.choices(["chosen", "chosen_first"], [65, 35])[0]
    first = rng.randrange(15 * 60, 19 * 60 + 1, 60)
    block = first + rng.choice([60, 60, 90])  # free from this time, which then becomes the meeting time
    t1 = _at(ref, rng, d, first, quiet=quiet)
    t2 = _moved(t1, block, rng)
    who = rng.choice(others)
    rest = [w for w in _words(t2, quiet) if w != t2["say_time"]] if variant == "chosen_first" else _words(t1, quiet)
    must = rest + [t2["say_time"]]
    say_block = _say_clock(block // 60, block % 60, rng)
    phrase = rng.choice(_clear([x.format(t=say_block) for x in FROM], rest))
    reason = f"{who}: '{phrase}'라고 말함 (이 말을 그대로 씀). 그 시각부터 된다는 사정"
    pick = f"그 시각을 '{t2['say_time']}'라고 따로 다시 말하며"
    if variant == "chosen_first":
        mover = lead
        place = "" if quiet else (f", 장소 '{t2['say_place']}'" if t2["say_place"] else "") + f", 일정 이름 '{t2['title']}'"
        beats = [reason.replace("라고 말함", "라고 먼저 말함"),
                 f"{lead}: 그럼 방금 말한 그 시각에 하자고 함. '{t2['say_date']}'{place}, {pick} 제안"]  # fmt: skip
        events = []
    else:
        mover = rng.choice(people)
        beats = [f"{lead}: {_when(t1)}에 보자고 제안" if quiet else f"{lead}: {_pitch(t1)} 제안", reason,
                 f"{mover}: " + ("그럼 그 시각에 하자고 바로 이어서 말함" if mover == who else f"그럼 {who} 말대로 그 시각에 하자고 함")
                 + f". {pick} 다시 제안 (날짜는 그대로)"]  # fmt: skip
        events = [_event(t1, "rejected")]
    settle, word = _settle(mover, people, rng)
    beats += settle
    events.append(_event(t2, "confirmed", people))
    k = _speaking(beats, people)
    return {"variant": variant, "beats": beats, "events": events, "existing": [], "must": must, "cues": [[phrase]],
            "apart": [t2["say_time"]], "says_confirm": word, "turns": (k, k + 3), "small_talk": rng.random() < 0.3}


FOCUS = {  # builder, weight. "short" = an ordinary make_short() chat
    "hedge": (_focus_hedge, 35), "time_shift": (_focus_time_shift, 25), "constraint": (_focus_constraint, 20),
    "same_weekday": (_focus_same_weekday, 10), "short": (None, 10),
}  # fmt: skip


def _clock_fits(sc: dict, rng) -> bool:
    """Pick start_minute so the chat stays on ref_date (check() and the bare-weekday rule both
    depend on the day) and ends before any event later that same day. False if it can't."""
    span = sum(sc["gaps"][: sc["max_turns"]])
    hi = 24 * 60 - 1 - span
    for e in sc["events"] + sc["existing_events"]:
        if e["date"] == sc["ref_date"] and e["start"]:
            hi = min(hi, int(e["start"][:2]) * 60 + int(e["start"][3:5]) - 30 - span)
    if hi < 6 * 60:
        return False
    sc["start_minute"] = rng.randint(min(8 * 60, hi), min(hi, 22 * 60))
    return True


def make_focus(rng: random.Random) -> dict:
    """One scenario for the four weak spots (see above). Same shape as make_short() plus
    `variant` and `cues`. type is hedge / time_shift / constraint / same_weekday, or the
    make_short() type for the ordinary short chats (variant short_<type>)."""
    while True:
        kind = rng.choices(list(FOCUS), weights=[w for _, w in FOCUS.values()])[0]
        if kind == "short":
            sc = make_short(rng) | {"cues": []}
            sc["variant"] = f"short_{sc['type']}"
        else:
            sc = _focus_scenario(rng, kind, FOCUS[kind][0])
        if _clock_fits(sc, rng):
            return sc


def _focus_scenario(rng, kind: str, build) -> dict:
    ref = dt.date(2026, 3, 1) + dt.timedelta(days=rng.randint(0, 305))
    people = rng.sample(NAMES, 3 if kind == "same_weekday" else rng.choices([3, 4, 5], [4, 4, 2])[0])
    used: set = set()
    b = build(ref, people, rng, used)
    existing = b["existing"]
    if kind != "same_weekday" and rng.random() < 0.4:
        taken = {e["title"] for e in b["events"] + existing}
        existing = existing + _unrelated_events(ref, people, rng, used, taken)
    lo, hi = b["turns"]
    sc = {
        "type": kind,
        "variant": b["variant"],
        "ref_date": ref.isoformat(),
        "participants": people,
        "beats": b["beats"],
        "must": b["must"],
        "cues": b["cues"],
        "says_confirm": b["says_confirm"],
        "events": b["events"],
        "existing_events": existing,
        "style": rng.choice(STYLES),
        "small_talk": rng.choice([t for t in SMALL_TALK if t]) if b["small_talk"] else None,
        "agree_words": rng.sample(AGREE, 4),
        "turns": rng.randint(lo, hi),
        "min_turns": max(2, lo - 1),
        "max_turns": hi + 3,
        "start_minute": 0,
        "gaps": [rng.randint(0, 4) for _ in range(40)],
    }
    # shift: [how, old minute, new minute, step]; refer: how a time_shift names the event;
    # apart: words that must be said outside the cue phrases (offline.py checks them)
    sc |= {k: b[k] for k in ("shift", "refer", "apart") if k in b}
    return sc


FOCUS2 = {  # name → (type, builder, weight). Second batch: title-only changes, chosen constraint times
    "title_shift": ("time_shift", _focus_title_shift, 160), "constraint_chosen": ("constraint", _focus_constraint_chosen, 60),
}  # fmt: skip


def make_focus2(rng: random.Random) -> dict:
    """The second focus batch: time-only changes whose chat names the event by title (or title
    + bare weekday) with the date only on the calendar, and constraint times that are then
    proposed and chosen. Same shape as make_focus()."""
    while True:
        kind, build, _ = rng.choices(list(FOCUS2.values()), weights=[w for *_, w in FOCUS2.values()])[0]
        sc = _focus_scenario(rng, kind, build)
        if _clock_fits(sc, rng):
            return sc


def focus_errors(sc: dict) -> list[str]:
    """What is wrong with a make_focus() gold; empty when it follows plan 8.3 and the chat's
    required words can carry it. Facts are re-derived from the words through the parser
    rather than taken from the builder, so a broken builder shows up here."""
    errs = []
    ref = dt.date.fromisoformat(sc["ref_date"])
    people, events, existing = sc["participants"], sc["events"], sc["existing_events"]
    for e in events:
        d = dt.date.fromisoformat(e["date"])
        if d < ref:
            errs.append(f"past date {e['date']}")
        if e["weekday"] != WEEKDAYS[d.weekday()]:
            errs.append(f"weekday {e['weekday']} for {e['date']}")
        if e["start"] and e["time_text"]:
            errs.append("both start and time_text")
        if set(e["attendees"]) & set(e["absent"]) or set(e["attendees"] + e["absent"]) - set(people):
            errs.append("attendees")
        if e["status"] in ("confirmed", "proposed") and sorted(e["attendees"] + e["absent"]) != sorted(people):
            errs.append("not everyone is attendee or absent")
        if e["status"] == "rejected" and e["attendees"]:
            errs.append("rejected event has attendees")
    squash = lambda xs: [x.replace(" ", "") for x in xs]  # noqa: E731
    cue_words = [c for alts in sc["cues"] for c in alts]
    mentions = [m for w in sc["must"] for m in parse(f"{w} 회의", ref) if m.date]
    said_times = set().union(set(), *(_times_in(w, ref) for w in sc["must"]))
    cue_times = set().union(set(), *(_times_in(c, ref) for c in cue_words))
    gold_dates = {e["date"] for e in events}
    gold_starts = {e["start"] for e in events if e["start"]}
    # a date nobody says is still known if the calendar has it for a title the chat must say
    on_calendar = {e["date"] for e in events for x in existing
                   if (x["date"], x["title"]) == (e["date"], e["title"]) and e["title"] in sc["must"]}  # fmt: skip
    if gold_dates - {m.date.isoformat() for m in mentions} - on_calendar:
        errs.append("a gold date is not in the must expressions")
    if {m.date.isoformat() for m in mentions if not m.tentative} - gold_dates - {e["date"] for e in existing}:
        errs.append("must has a date that is no event's date")
    if said_times - gold_starts - {e["start"] for e in existing if e["start"]}:
        errs.append("must has a time that is no event's time")
    apart = sc.get("apart", [])  # said in a cue *and* on its own (offline.py checks the second)
    if not apart and cue_times & (gold_starts | said_times):
        errs.append("constraint time is an event time or a must expression")
    if any(m in c for m in squash([w for w in sc["must"] if w not in apart]) for c in squash(cue_words)):
        errs.append("a must expression is inside a cue, so check() can't tell it is missing")
    if not sc["says_confirm"] and any("확정" in x for x in sc["beats"] + cue_words):
        errs.append("확정 in beats")
    if any("근데" in c for c in cue_words):
        errs.append("근데 in a cue (the writer prompt forbids it)")
    kind, variant = sc["type"], sc["variant"]
    statuses = [e["status"] for e in events]
    if kind == "hedge":
        want = "confirmed" if variant == "resolved" else "proposed"
        if statuses != [want]:
            errs.append(f"hedge {variant} gold {statuses}")
        if cue_times or any(m.date for c in cue_words for m in parse(c, ref)):
            errs.append("a hedge phrase reads as a date or time")
    elif kind == "time_shift":
        errs += _time_shift_errors(sc)
    elif kind == "constraint" and variant.startswith("chosen"):
        if not cue_times:
            errs.append("constraint phrase has no time")
        elif not events or statuses[-1] != "confirmed" or set(statuses[:-1]) - {"rejected"}:
            errs.append(f"constraint gold {statuses}")
        elif cue_times != {events[-1]["start"]}:
            errs.append("the chosen time is not the constraint time")
        elif any(e["start"] in cue_times for e in events[:-1]):
            errs.append("the constraint time is also a dropped time")
        elif len(apart) != 1 or _times_in(apart[0], ref) != cue_times or apart[0] not in sc["must"]:
            errs.append("the constraint time is not proposed on its own")
    elif kind == "constraint":
        if not cue_times:
            errs.append("constraint phrase has no time")
        if not events or statuses[-1] not in ("confirmed", "proposed") or set(statuses[:-1]) - {"rejected"}:
            errs.append(f"constraint gold {statuses}")
        elif events[-1]["start"]:  # "HH:MM" strings compare in time order
            last = events[-1]["start"]
            busy = _times_in(cue_words[0], ref)  # until / from this time
            leave = _times_in(cue_words[1], ref) if len(cue_words) > 1 else set()  # must be done by
            if any(last <= t for t in busy) or any(last >= t for t in leave):
                errs.append("final time is outside what the constraints allow")
    elif kind == "same_weekday":
        d = dt.date.fromisoformat(events[0]["date"])
        if d.weekday() != ref.weekday() or (d - ref).days != (0 if variant.startswith("this_week") else 7):
            errs.append(f"same weekday {variant}: {d} for ref {ref}")
        if _date_of(sc["must"][0], ref) != d:
            errs.append("the date expression doesn't resolve to the gold date")
    return errs


def _time_shift_errors(sc: dict) -> list[str]:
    events, existing = sc["events"], sc["existing_events"]
    ref = dt.date.fromisoformat(sc["ref_date"])
    refer = sc.get("refer", "date")
    title = events[0]["title"] if events else None
    said_dates = {m.date for w in sc["must"] for m in parse(f"{w} 회의", ref) if m.date}
    if refer == "title" and (said_dates or title not in sc["must"]):
        return ["a title-only change must say the title and no date"]
    if refer == "title_weekday":
        day = sc["must"][0]
        letter = day.removesuffix("요일").removesuffix("욜")
        if len(letter) != 1 or letter not in WEEKDAYS or title not in sc["must"]:
            return ["a title + weekday change must say a bare weekday and the title"]
        if events and _date_of(day, ref) != dt.date.fromisoformat(events[0]["date"]):
            return ["the weekday doesn't resolve to the calendar date"]
    how, old, new, step = sc["shift"]
    old_t, new_t = _clock(old).strftime("%H:%M"), _clock(new).strftime("%H:%M")
    if len(events) != 2 or len({e["date"] for e in events}) != 1 or len({e["title"] for e in events}) != 1:
        return ["a time shift is two events with the same day and title"]
    mine = [x for x in existing if x["date"] == events[0]["date"] and x["title"] == events[0]["title"]]
    if len(mine) != 1 or mine[0]["status"] != "confirmed":
        return ["the moved event is not on the calendar exactly once"]
    if events[0]["start"] == events[1]["start"]:
        return ["time shift events share a start"]
    errs = []
    by_start = {e["start"]: e for e in events}
    if set(by_start) != {old_t, new_t} or mine[0]["start"] != old_t:
        errs.append("time shift starts are not the calendar time and the new time")
    if new - old != step or step == 0 or (how == "earlier") == (step > 0) and how != "change":
        errs.append(f"{how} by {step} min goes the wrong way")
    want = {"agree": ("rejected", "confirmed"), "short_agree": ("rejected", "confirmed"),
            "refuse": ("confirmed", "rejected"), "pending": ("confirmed", "proposed")}  # fmt: skip
    want = next(v for k, v in want.items() if sc["variant"].endswith(k))
    if (by_start.get(old_t, {}).get("status"), by_start.get(new_t, {}).get("status")) != want:
        errs.append(f"time shift {sc['variant']} gold statuses")
    if want[0] == "confirmed" and by_start.get(old_t) != mine[0]:
        errs.append("the kept event differs from the calendar entry")
    return errs
