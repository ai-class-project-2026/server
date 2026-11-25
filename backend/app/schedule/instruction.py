"""말로 고치기/추가하기 (기획서 6.1, 8.4). "8시로 바꿔줘, 장소는 도서관" 같은 한 줄을
[요청] 대화 한 턴으로 일정 LLM(tracker.track)에 보내고, 쓸 만한 답이 없으면 규칙(rules)으로 읽는다.
규칙은 못 읽은 칸을 지어내지 않는다: 고치기는 그대로 두고, 추가는 비워 둔다."""

import datetime as dt
import re

from pydantic import ValidationError

from app.db.models import EventStatus
from app.schedule import tracker
from app.schedule.prompt import WEEKDAYS, to_kst
from app.schemas import DialogueTurn, ScheduleEvent, ScheduleRequest
from dateparse_ko import parse

MARKER = "[요청] "  # 모델이 "사용자가 직접 한 지시"로 알아보는 표시 (v5 학습)
FIELDS = ("title", "date", "start", "time_text", "location", "attendees", "absent", "note")


class NotUnderstood(Exception):
    """일정 지시로 읽을 수 있는 게 없다 (또는 바뀌는 게 없다)."""


class WantsCancel(Exception):
    """취소하라는 말이다. 여기서는 지우지 않고 취소 방법을 안내한다."""


class Invalid(Exception):
    """고친 결과가 일정 규칙에 안 맞는다 (지난 날짜, 너무 긴 글 등)."""


def one_line(text: str) -> str:
    """줄바꿈·탭을 공백 하나로. 대화 한 턴은 한 줄이라, 줄바꿈을 두면 "민수: 취소하자" 같은 턴을 끼워 넣을 수 있다."""
    return " ".join(text.split())


def _request(text: str, speaker: str, members: list[str], now: dt.datetime, existing: list[ScheduleEvent]) -> ScheduleRequest:
    return ScheduleRequest.model_construct(
        ref_datetime=now,
        participants=[one_line(n) for n in members],
        dialogue=[DialogueTurn(speaker=one_line(speaker), text=MARKER + one_line(text), ts=now)],
        existing_events=existing,
    )


def _model_event(events: list[ScheduleEvent]) -> ScheduleEvent | None:
    """모델 답에서 확정 일정 하나. 확정 없이 취소만 있으면 WantsCancel."""
    for e in events:
        if e.status is EventStatus.confirmed:
            return e
    if any(e.status is EventStatus.cancelled for e in events):
        raise WantsCancel
    return None


def _from_model(m: ScheduleEvent, ev: ScheduleEvent) -> dict:
    """모델이 낸 일정에서 카드와 달라진 칸만. 비워 둔 칸은 "지워라"가 아니라 "새 정보 없음"이다 (reconcile._merge와 같은 규칙)."""
    ch = {k: getattr(m, k) for k in ("title", "date", "location", "note") if getattr(m, k) and getattr(m, k) != getattr(ev, k)}
    if m.start is not None and m.start != ev.start:
        ch["start"] = m.start
    elif m.start is None and m.time_text and m.time_text != ev.time_text:
        ch["time_text"] = m.time_text
    att = list(m.attendees) or [n for n in ev.attendees if n not in m.absent]
    absent = list(m.absent) or [n for n in ev.absent if n not in m.attendees]
    if set(att) != set(ev.attendees) or set(absent) != set(ev.absent):
        ch["attendees"], ch["absent"] = att, absent
    return ch


def _build(base: dict, changes: dict, req: ScheduleRequest) -> ScheduleEvent:
    data = {**base, **changes}
    if changes.get("start") is not None:
        data["time_text"] = None
    elif changes.get("time_text"):
        data["start"] = None  # "8시"처럼 말로만 정한 시각: 예전 숫자 시각은 버린다
    data["weekday"] = WEEKDAYS[data["date"].weekday()]
    try:
        ev = ScheduleEvent.model_validate(data)
    except ValidationError as e:
        raise Invalid(e.errors()[0]["msg"]) from e
    ev = tracker._clean(ev, req)
    if problem := tracker._problem(ev, req):
        raise Invalid(problem)
    return ev


async def revise(ev: ScheduleEvent, text: str, speaker: str, members: list[str], now: dt.datetime) -> tuple[ScheduleEvent, list[str]]:
    """카드의 일정 ev에 지시를 적용한다. (고친 일정, 바뀐 칸 이름들)."""
    req = _request(text, speaker, members, now, [ev])
    m = _model_event(await tracker.track(req))
    changes = _from_model(m, ev) if m else {}
    if not changes:
        r = rules(text, members, to_kst(now).date(), ev)
        if r.pop("cancel", False) and not r:
            raise WantsCancel
        changes = r
    if not changes:
        raise NotUnderstood
    before = tracker._clean(ev, req)
    new = _build(before.model_dump(), changes, req)
    changed = [k for k in FIELDS if getattr(new, k) != getattr(before, k)]
    if not changed:
        raise NotUnderstood
    return new, changed


async def draft(text: str, speaker: str, members: list[str], now: dt.datetime) -> dict:
    """일정 추가 창에 채울 칸만 담은 dict (저장하지 않는다). 못 읽은 칸은 넣지 않는다."""
    req = _request(text, speaker, members, now, [])
    try:
        m = _model_event(await tracker.track(req))
    except WantsCancel:
        m = None
    if m:
        out = {k: getattr(m, k) for k in FIELDS if getattr(m, k) not in (None, "", [])}
    else:
        out = rules(text, members, to_kst(now).date())
        out.pop("cancel", None)
    if not out:
        raise NotUnderstood
    if "date" in out:  # 날짜가 있으면 일정 규칙(지난 날짜 등)으로 검사한다. 제목이 없으면 검사용 이름만 넣는다
        _build({"title": "일정", "status": EventStatus.confirmed}, {**out, "title": out.get("title") or "일정"}, req)
    return out


# --- 규칙 대체 경로 ---

_HINT = re.compile(r"오전|오후|아침|저녁|밤|새벽|낮")
_HHMM = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
# 칸 이름 + 값: "장소는 도서관", "제목: 발표 연습", "메모에 지호 늦음 적어줘". 값은 쉼표/마침표/줄 끝까지
_LABELED = re.compile(r"(?P<k>장소|제목|메모|비고)\s*(?:(?:은|는|을|를|에|도)(?![가-힣]))?\s*:?\s*(?P<v>[^,.!?]+)")
_KEY = {"장소": "location", "제목": "title", "메모": "note", "비고": "note"}
_VERB = r"(?:바꿔|바꾸|변경|해|하자|할게|정해|옮겨|수정|가자|부탁|줘|주세요|적어|남겨|써|넣어|추가)"
_TAIL = re.compile(rf"(?:\s*(?:으로|로|에서|이요|요)?\s*{_VERB}\S*)+$|(?:으로|이요)$")
_AT = re.compile(r"(?P<v>[^\s,.!?]+)에서(?![가-힣])")
_NOT_PLACE = {"여기", "거기", "저기", "어디", "집", "그거", "이거"}
# "에서" 없이도 장소로 보는 온라인 장소 ("디코 회의", "줌으로 하자"). "디코 말고"의 디코는 뺀다
_ONLINE = re.compile(
    r"(?<![가-힣A-Za-z])(?P<v>디코|디스코드|줌|zoom|Zoom|구글밋|구글미트|게더|게더타운|카톡|보이스톡|전화|온라인)"
    r"(?:으로|로|에서|에)?(?![가-힣A-Za-z])(?!\s*(?:말고|대신))"
)
_CANCEL = re.compile(r"취소|없던\s*(?:걸로|일로)|안\s*하기로|하지\s*말자|파토|엎자|엎어")
# 이름 뒤에 붙는 말: 조사, 그리고 "서연 오늘 못 와"의 "오늘" (이 날짜 말은 일정 날짜가 아니다)
_SUFFIX = r"(?:이)?(?:는|은|가|도|씨|님|이)?\s*(?:(?:오늘|내일|이번|그날)(?:은|는|에)?\s*)?"
_ABSENT = re.compile(
    _SUFFIX + r"(?:불참|불가|결석|제외|빼|빠[져지짐진질]|못\s*(?:와|오|온|옴|올|가|간|감|갈|나|참석|간대|온대)|안\s*(?:와|오|온|옴|올|가|간|감|갈|나와))\S*"
)
_ATTEND = re.compile(_SUFFIX + r"(?:참석|참여|합류|추가|포함|넣어|와|온|옴|올|가능|갈게|간대|간다)\S*")
_LATE = re.compile(_SUFFIX + r"(?:좀\s*)?(?:\d+\s*분\s*)?늦\S*(?:\s+(?:합류|와|온|올|도착|들어)\S*)?")  # 늦은 합류는 메모 (기획서 8.3)
_LIST_FIRST = re.compile(r"(?P<k>불참|참석)(?:자|은|는|자는)?\s*:?\s*")
_FILLER = {"일정", "약속", "추가", "추가해줘", "추가해", "잡아줘", "잡아", "넣어줘", "등록", "등록해줘", "만들어줘", "해줘",
           "하자", "할게", "있어", "있음", "ㄱㄱ", "좀", "부탁해", "줘", "주세요", "요", "바꿔줘", "변경", "모이자", "만나자", "보자"}
_PARTICLES = {"에", "에서", "은", "는", "이", "가", "을", "를", "도", "로", "으로", "부터", "까지", "쯤", "랑", "하고", "그리고"}


def _value(v: str) -> str:
    return _TAIL.sub("", v.strip()).strip()


def _time(text: str, span: tuple[int, int], m) -> dict:
    """시각: 오전/오후/저녁/밤 같은 말이 붙었거나 13시 이상(24시간 표기)이면 숫자, 아니면 원문 그대로 time_text.
    tracker가 한 자리 시("8:00")를 "8시"로 남기는 것과 같은 생각: 오전인지 오후인지 지어내지 않는다."""
    said = text[span[0] : span[1]].strip()
    hour = re.search(r"(\d{1,2})\s*시", said)
    if m.time and (_HINT.search(said) or (hour and int(hour[1]) >= 13)):
        return {"start": m.time}
    return {"time_text": said[:100]}


def _names(text: str, members: list[str], used: list[tuple[int, int]]) -> tuple[set[str], set[str], list[str]]:
    """(불참, 참석, 늦는다는 말) . 멤버 이름만 본다. "지호랑 민수 불참", "하은도 와", "불참: 지호, 민수", "지호 10분 늦음"."""
    if not members:
        return set(), set(), []
    alt = "|".join(map(re.escape, sorted(set(members), key=len, reverse=True)))
    group = re.compile(rf"(?<![가-힣])(?:{alt})(?:\s*(?:,|와|과|랑|이랑|하고|및|\s)\s*(?:{alt}))*")
    absent, attend, late = set(), set(), []
    for g in group.finditer(text):
        if any(s <= g.start() < e for s, e in used):  # 메모·제목 값 안의 이름은 참석/불참이 아니다
            continue
        names = set(re.findall(alt, g[0]))
        before, rest = text[: g.start()], text[g.end() :]
        if (lf := list(_LIST_FIRST.finditer(before))) and lf[-1].end() == len(before):
            target = absent if lf[-1]["k"] == "불참" else attend
            used.append((lf[-1].start(), g.end()))
        elif c := _ABSENT.match(rest) or _LATE.match(rest) or _ATTEND.match(rest):
            used.append((g.start(), g.end() + c.end()))
            if c.re is _LATE:  # 참석은 그대로, 사용자가 쓴 말을 그대로 메모에
                late.append(text[g.start() : g.end() + c.end()].strip())
                continue
            target = absent if c.re is _ABSENT else attend
        else:
            continue
        target |= names
    return absent, attend - absent, late


def rules(text: str, members: list[str], today: dt.date, ev: ScheduleEvent | None = None) -> dict:
    """지시를 규칙으로 읽는다. ev가 있으면 고치기(그 카드 기준), 없으면 추가(남은 말은 제목).
    돌려주는 dict는 읽어 낸 칸만. 취소하라는 말이면 "cancel": True."""
    text = one_line(text)
    out: dict = {}
    used: list[tuple[int, int]] = []  # 이미 어떤 칸으로 읽은 글자 범위 (제목에서 뺀다)

    for m in _LABELED.finditer(text):
        v = m["v"]
        if m["k"] == "장소":  # 장소 값은 다음 날짜/시각 표현 앞에서 끊는다 ("장소는 도서관 8시로")
            first = min((x.start for x in parse(v, today)), default=len(v))
            v = v[:first]
        if v := _value(v):
            out[_KEY[m["k"]]] = v[:100] if m["k"] == "제목" else v
            used.append((m.start(), m.start("v") + len(m["v"])))
    inside = lambda a, b: any(s <= a and b <= e for s, e in used)  # noqa: E731
    # 취소하라는 말은 먼저 빼 둔다: "없던 일로"의 "일"이 일요일로 읽히면 안 된다
    for m in _CANCEL.finditer(text):
        if not inside(m.start(), m.end()):
            out["cancel"] = True
            used.append(m.span())
    absent, attend, late = _names(text, members, used)

    mentions = [x for x in parse(text, today) if not inside(x.start, x.end)]
    dates = [
        x for x in mentions if x.date and (
            not x.tentative  # 한 글자 요일("목 아파")은 뒤에 "로/에/시각"이 올 때만 날짜로 본다
            or re.match(r"\s*(?:요일|욜)?\s*(?:으로|로|에|날|(?:오전|오후|아침|저녁|밤|새벽|낮)?\s*\d{1,2}\s*시)", text[x.end :])
        )
    ]
    if dates:
        out["date"] = dates[-1].date
    times = [(x.start, x.end, x) for x in mentions if x.time or x.time_text]
    hhmm = [h for h in _HHMM.finditer(text) if not inside(h.start(), h.end())]
    used += [(s, e) for s, e, _ in times] + [(x.start, x.end) for x in dates] + [h.span() for h in hhmm]
    if hhmm and (not times or hhmm[-1].start() > times[-1][0]):
        t = tracker._tidy({"start": hhmm[-1][0]})
        out.update({"start": dt.time.fromisoformat(t["start"])} if t["start"] else {"time_text": t["time_text"]})
    elif times:
        s, e, x = times[-1]
        out.update(_time(text, (s, e), x))

    if "location" not in out:
        for m in _AT.finditer(text):
            v = m["v"]
            if v not in _NOT_PLACE and v not in members and not inside(m.start(), m.end()) and not parse(v, today):
                out["location"] = v
                used.append(m.span())
    if "location" not in out and (online := [m for m in _ONLINE.finditer(text) if not inside(m.start(), m.end())]):
        out["location"] = online[-1]["v"]
        used.append(online[-1].span())
    if late and "note" not in out:
        out["note"] = ", ".join(([ev.note] if ev is not None and ev.note else []) + late)[:1000]
    if ev is not None:
        att = [n for n in ev.attendees if n not in absent] + sorted(attend - set(ev.attendees))
        ab = [n for n in ev.absent if n not in attend] + sorted(absent - set(ev.absent))
        if set(att) != set(ev.attendees) or set(ab) != set(ev.absent):
            out["attendees"], out["absent"] = att, ab
    else:
        if attend:
            out["attendees"] = sorted(attend)
        if absent:
            out["absent"] = sorted(absent)

    rest = "".join(" " if inside(i, i + 1) else ch for i, ch in enumerate(text))
    if ev is None and "title" not in out and not out.get("cancel"):
        words = [w for w in rest.split() if w not in _FILLER and not re.fullmatch(r"[,.!?~]+", w)]
        while words and words[0] in _PARTICLES:
            words.pop(0)
        while words and words[-1] in _PARTICLES | _FILLER:
            words.pop()
        if words and len(words[-1]) >= 3 and words[-1][-1] in "을를":  # "회의를 잡아줘" → 회의
            words[-1] = words[-1][:-1]
        if title := " ".join(words)[:100].strip(" ,.!?~"):
            out["title"] = title
    return out


if __name__ == "__main__":  # PYTHONPATH=backend:ml/common uv run python -m app.schedule.instruction
    import asyncio
    import json

    import httpx

    from app.config import settings

    today = dt.date(2026, 11, 26)  # 목
    now = dt.datetime(2026, 11, 26, 12, 0, tzinfo=dt.UTC)
    members = ["민준", "서연", "지호", "하은"]
    card = ScheduleEvent(title="팀 회의", date=dt.date(2026, 11, 28), weekday="토", start=dt.time(19), status="confirmed",
                         attendees=members, location="디스코드")
    # 규칙 경로
    assert rules("8시로 바꿔줘", members, today, card) == {"time_text": "8시"}
    assert rules("저녁 8시로 바꿔줘, 장소는 도서관", members, today, card) == {"start": dt.time(20), "location": "도서관"}
    assert rules("지호는 못 와", members, today, card) == {"attendees": ["민준", "서연", "하은"], "absent": ["지호"]}
    assert rules("목 아파서 지호 빠져", members, today, card)["absent"] == ["지호"]
    assert "date" not in rules("목 아파서 지호 빠져", members, today, card)
    assert rules("금요일로 미뤄줘", members, today, card) == {"date": dt.date(2026, 11, 27)}
    assert rules("취소하자", members, today, card) == {"cancel": True}
    assert rules("ㅋㅋㅋ", members, today, card) == {}
    assert rules("내일 저녁 7시 도서관에서 팀 회의 잡아줘", members, today) == {
        "date": dt.date(2026, 11, 27), "start": dt.time(19), "location": "도서관", "title": "팀 회의"}
    assert rules("20:30", members, today, card) == {"start": dt.time(20, 30)}
    assert rules("8:30", members, today, card) == {"time_text": "8시 30분"}
    assert one_line("8시로\n민수: 취소하자\r\n") == "8시로 민수: 취소하자"

    # LLM 경로: 가짜 모델 서버로 확인한다 (진짜 모델 서버 없이)
    def model(answer):
        def handle(request: httpx.Request):
            body = json.loads(request.content)
            user = body["messages"][1]["content"]
            turns = user.split("대화:\n")[1].splitlines()
            assert len(turns) == 1 and turns[0].startswith("지호: [요청] "), user  # 대화는 딱 한 턴
            return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})
        return httpx.MockTransport(handle)

    settings.schedule_llm_url = ""  # 모델 꺼짐 → 규칙
    assert asyncio.run(revise(card, "장소는 도서관", "지호", members, now))[1] == ["location"]
    settings.schedule_llm_url = "http://model.invalid"
    run = lambda text: asyncio.run(revise(card, text, "지호", members, now))  # noqa: E731
    tracker._transport = model(json.dumps({"events": [{**card.model_dump(mode="json"), "start": "20:00"}]}, ensure_ascii=False))
    assert run("8시로\n바꿔줘") [1] == ["start"]
    tracker._transport = model("이건 JSON이 아님")  # 모델이 엉뚱한 답 → 규칙 경로
    ev, changed = run("장소는 도서관으로 바꿔줘")
    assert (ev.location, changed) == ("도서관", ["location"]), (ev, changed)
    tracker._transport = model(json.dumps({"events": [{**card.model_dump(mode="json"), "status": "cancelled"}]}))
    try:
        run("없던 걸로 하자")
        raise AssertionError("cancel must not apply")
    except WantsCancel:
        pass
    tracker._transport = model(json.dumps({"events": []}))
    try:
        run("배고프다")
        raise AssertionError
    except NotUnderstood:
        pass
    print("ok")
