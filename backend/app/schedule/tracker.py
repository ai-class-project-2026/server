"""Step ③ of plan 8.4: call the schedule LLM, validate its JSON."""

import asyncio
import json
import logging
import re

import httpx
from pydantic import ValidationError

from app.config import settings
from app.schedule.prompt import WEEKDAYS, build_messages, to_kst
from app.schemas import ScheduleEvent, ScheduleRequest

log = logging.getLogger(__name__)
# 답 길이 상한. 모델이 같은 일정을 끝없이 되풀이하면 타임아웃까지 GPU를 붙잡으므로 여기서 끊는다 (잘린 답은 JSON이 깨져 카드 없음).
# ponytail: 고정값. 정상 답은 일정 4~5개, 340토큰 안팎이었다. 한 번에 일정 8개 넘게 뽑아야 하면 올릴 것.
MAX_TOKENS = 700
_transport = None  # tests swap in httpx.MockTransport


async def track(req: ScheduleRequest, dropped: list | None = None) -> list[ScheduleEvent]:
    """[] on any failure: a missed card is fine, a crashed chat is not.
    dropped: 리스트를 넘기면 _problem으로 버린 일정을 (일정, 사유)로 담아 준다 (말로 고치기가 "지난 날짜로 답함"을 알아보려고)."""
    if not settings.schedule_llm_url:
        return []
    try:
        # 날짜 주석 만들기(build_messages)는 CPU 일이라 이벤트 루프 밖 스레드에서 한다.
        messages = await asyncio.to_thread(build_messages, req)
        async with httpx.AsyncClient(
            timeout=settings.schedule_llm_timeout, transport=_transport
        ) as client:
            r = await client.post(
                f"{settings.schedule_llm_url.rstrip('/')}/v1/chat/completions",
                json={
                    "model": settings.schedule_llm_model,
                    "messages": messages,
                    "temperature": 0,
                    "max_tokens": MAX_TOKENS,
                    "response_format": {"type": "json_object"},
                },
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        events = json.loads(content)["events"]
        if not isinstance(events, list):  # {"events": null}, 7, "..." 같은 껍데기는 실패로 본다
            raise TypeError(f"events is {type(events).__name__}, not a list")
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
        log.warning("schedule LLM failed: %r", e)  # %r: timeouts have empty str()
        return []
    out = []
    for e in events:  # keep the valid ones even if one is malformed
        try:
            ev = ScheduleEvent.model_validate(_tidy(e))
        except ValidationError as err:
            log.warning("dropping invalid event %s: %s", e, err)
            continue
        if problem := _problem(ev, req):
            log.warning("dropping event %r: %s", ev.title, problem)
            if dropped is not None:
                dropped.append((ev, problem))
            continue
        out.append(_clean(ev, req))
    return out


def _tidy(e):
    """검증에서 통째로 버려지거나 엉뚱한 시각이 되는 모양만 검증 전에 고친다. 시각을 지어내지는 않는다.
    모델은 24시간 두 자리("20:00")로 쓴다. 한 자리 시("8:00")는 오전인지 오후인지 알 수 없어서 "8시"로 남기고,
    시:분으로 못 읽는 값("8시", "24:00", 숫자 8)은 원문을 time_text에 남긴다. 둘 다 start는 비운다
    (기획서 8.2, 8.5). 숫자를 그대로 두면 8이 00:00:08이 된다."""
    if not isinstance(e, dict):
        return e
    e = {**e, **{k: [] for k in ("attendees", "absent") if k in e and e[k] is None}}
    raw = e.get("start")
    if raw is None:
        return e
    text = str(raw).strip()
    if m := isinstance(raw, str) and re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", text, re.ASCII):
        if int(m[1]) < 24 and int(m[2]) < 60:
            if len(m[1]) == 2:
                return {**e, "start": f"{m[1]}:{m[2]}"}
            text = f"{m[1]}시" + (f" {int(m[2])}분" if int(m[2]) else "")
    said = None if text.lower() in ("", "null", "none") else text[:100]
    return {**e, "start": None, "time_text": e.get("time_text") or said}


def _clean(ev: ScheduleEvent, req: ScheduleRequest) -> ScheduleEvent:
    """버리지 않고 고칠 수 있는 것만 고친다: 참여자가 아닌 이름만 빼고(참여자 목록이 없으면 그대로),
    숫자 시각(start)이 있으면 time_text는 비운다."""
    changes = {}
    if req.participants:
        known = set(req.participants)
        changes["attendees"] = [n for n in ev.attendees if n in known]
        changes["absent"] = [n for n in ev.absent if n in known]
    if ev.start is not None and ev.time_text is not None:
        changes["time_text"] = None
    return ev.model_copy(update=changes)


def _problem(ev: ScheduleEvent, req: ScheduleRequest) -> str | None:
    """기획서 8.2/8.3의 규칙을 어긴 후보는 사유를 돌려준다 (None이면 통과). 후보 하나씩 버린다."""
    if ev.date < to_kst(req.ref_datetime).date():  # 과거 일정은 넣지 않는다
        return "date is before the reference date"
    if ev.weekday and ev.weekday[0] != WEEKDAYS[ev.date.weekday()]:
        return "weekday does not match date"
    if set(ev.attendees) & set(ev.absent):
        return "same person in attendees and absent"
    return None


if __name__ == "__main__":  # PYTHONPATH=backend:ml/common uv run python -m app.schedule.tracker
    for raw, want in [
        ("8:00", (None, "8시")), ("8:30:00", (None, "8시 30분")), ("19:30:00", ("19:30", None)), ("08:00", ("08:00", None)), ("19:00", ("19:00", None)),
        ("8시", (None, "8시")), ("24:00", (None, "24:00")), ("7:60", (None, "7:60")), (8, (None, "8")), ("", (None, None)),
        ("null", (None, None)), (None, (None, None)),
    ]:
        base = {"title": "회의", "date": "2026-11-30", "status": "confirmed", "attendees": None}
        ev = ScheduleEvent.model_validate(_tidy({**base, "start": raw}))
        got = (ev.start and ev.start.strftime("%H:%M"), ev.time_text)
        assert got == want, (raw, got, want)
    assert _tidy({"start": "8시", "time_text": "저녁"})["time_text"] == "저녁" and _tidy([1]) == [1] and _tidy(None) is None
    import datetime as dt

    old, new = ({"title": "회의", "date": d, "status": "confirmed"} for d in ("2026-11-01", "2026-11-30"))
    answer = json.dumps({"events": [old, new]})
    _transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": answer}}]}))
    settings.schedule_llm_url = "http://model.invalid"
    req = ScheduleRequest.model_construct(ref_datetime=dt.datetime(2026, 11, 26, 12), participants=[], dialogue=[], existing_events=[])
    got, dropped = asyncio.run(track(req, out := [])), out
    assert [e.date for e in got] == [dt.date(2026, 11, 30)] == [e.date for e in asyncio.run(track(req))], got  # 넘기지 않으면 지금과 같다
    assert [(e.date, why) for e, why in dropped] == [(dt.date(2026, 11, 1), "date is before the reference date")], dropped
    # 반복 폭주 막기: 요청에 출력 상한이 실리고, 상한에 걸려 잘린 답은 카드 없음이 된다
    sent = []
    cut = json.dumps({"events": [new] * 3})[:-20]
    _transport = httpx.MockTransport(lambda r: (sent.append(json.loads(r.content)), httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": cut}}]}))[1])
    assert asyncio.run(track(req)) == [] and sent[0]["max_tokens"] == MAX_TOKENS, sent
    print("ok")
