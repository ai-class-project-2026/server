"""Step 1 of plan 8.6: build the answer first, then ask an LLM to write a chat
that leads to it. The label is known by construction."""

import datetime as dt
import random

WEEKDAYS = "월화수목금토일"
NAMES = ["민준", "서연", "지호", "하은", "도윤", "서준", "지우", "예린", "현우", "수아"]
PLACES = ["디스코드", "학교 도서관", "카페", "스터디룸", "줌", None]
TITLES = ["팀 회의", "발표 연습", "자료 조사 회의", "최종 점검", "스터디"]
# type → weight. Matches the pilot mix (plan 8.6 ③) plus more of the hard ones.
TYPES = {
    "confirm": 3,
    "proposal_only": 2,
    "revise": 3,
    "cancel": 2,
    "partial_absent": 2,
    "not_schedule": 1,
}


def _expression(ref: dt.date, target: dt.date, rng: random.Random) -> str:
    """How people in the chat refer to `target`."""
    delta = (target - ref).days
    options = [f"{target.month}/{target.day}", f"{target.month}월 {target.day}일"]
    if delta <= 3:
        options.append(["오늘", "내일", "모레", "글피"][delta])
    if delta < 7:
        options.append(f"{WEEKDAYS[target.weekday()]}요일")
        options.append(WEEKDAYS[target.weekday()])
    elif target.isocalendar()[1] == (ref + dt.timedelta(7)).isocalendar()[1]:
        options.append(f"다음주 {WEEKDAYS[target.weekday()]}")
    return rng.choice(options)


def _when(ref, rng):
    d = ref + dt.timedelta(days=rng.randint(1, 12))
    hour = rng.choice([13, 15, 16, 17, 18, 19, 20, 21])
    return d, dt.time(hour, rng.choice([0, 0, 30]))


def _event(title, d, t, place, status, attendees, absent=(), note=None):
    return {
        "title": title,
        "date": d.isoformat(),
        "weekday": WEEKDAYS[d.weekday()],
        "start": t.strftime("%H:%M"),
        "time_text": None,
        "location": place,
        "status": status,
        "attendees": list(attendees),
        "absent": list(absent),
        "note": note,
    }


def make(rng: random.Random) -> dict:
    """One scenario: what the LLM must act out + the gold events."""
    ref = dt.date(2026, rng.randint(3, 12), rng.randint(1, 28))
    people = rng.sample(NAMES, rng.randint(3, 5))
    kind = rng.choices(list(TYPES), weights=TYPES.values())[0]
    title, place = rng.choice(TITLES), rng.choice(PLACES)
    d, t = _when(ref, rng)
    say = _expression(ref, d, rng)
    lead = people[0]
    where = f", 장소 {place}" if place else ""
    hhmm = f"{t.hour}시" + (" 반" if t.minute else "")

    if kind == "confirm":
        flow = f"{lead}가 {say} {hhmm}{where} {title} 제안 → 나머지 대부분 동의 → 확정"
        gold = [_event(title, d, t, place, "confirmed", people)]
    elif kind == "proposal_only":
        flow = f"{lead}가 {say} {hhmm} {title} 제안 → 한 명은 애매하게 답하고 나머지는 아직 답 없음 → 확정되지 않고 끝남"
        gold = [_event(title, d, t, place, "proposed", people)]
    elif kind == "revise":
        d2, t2 = _when(ref, rng)
        say2 = _expression(ref, d2, rng)
        hhmm2 = f"{t2.hour}시" + (" 반" if t2.minute else "")
        flow = (
            f"{lead}가 {say} {hhmm} {title} 제안 → {people[1]}가 안 된다고 함 → "
            f"{say2} {hhmm2}로 수정 제안{where} → 과반 동의로 확정"
        )
        gold = [_event(title, d2, t2, place, "confirmed", people)]
    elif kind == "cancel":
        flow = (
            f"{say} {hhmm} {title}가 이미 정해져 있었다는 듯이 시작 → "
            f"{lead}가 사정이 생겨 취소하자고 함 → 다들 동의 → 취소 확정"
        )
        gold = [_event(title, d, t, place, "cancelled", people)]
    elif kind == "partial_absent":
        absent = people[-1]
        flow = (
            f"{lead}가 {say} {hhmm}{where} {title} 제안 → {absent}는 학원 때문에 못 온다고 함 → "
            "나머지는 동의해서 확정"
        )
        gold = [_event(title, d, t, place, "confirmed", people[:-1], [absent])]
    else:  # not_schedule: dates are mentioned but nothing is planned
        flow = f"{say} 수행평가/시험 얘기, '담에 놀러가자' 같은 막연한 말만 오감 → 약속 없음"
        gold = []

    return {
        "type": kind,
        "ref_date": ref.isoformat(),
        "participants": people,
        "flow": flow,
        "events": gold,
    }
