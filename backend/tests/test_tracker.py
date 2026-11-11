import asyncio
import json

import httpx

from app.config import settings
from app.schedule import tracker
from app.schemas import ScheduleRequest

REQ = ScheduleRequest(
    ref_datetime="2026-10-12T21:10:00+09:00",
    participants=["a", "b"],
    dialogue=[{"speaker": "a", "text": "내일 7시 ㄱ"}],
)


def _run(monkeypatch, handler):
    monkeypatch.setattr(settings, "schedule_llm_url", "http://llm")
    monkeypatch.setattr(tracker, "_transport", httpx.MockTransport(handler))
    return asyncio.run(tracker.track(REQ))


def _reply(content: str):
    return lambda req: httpx.Response(
        200, json={"choices": [{"message": {"content": content}}]}
    )


def test_parses_openai_compatible_reply_and_drops_bad_events(monkeypatch):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        good = {
            "title": "회의",
            "date": "2026-10-13",
            "start": "19:00",
            "status": "confirmed",
        }
        return _reply(json.dumps({"events": [good, {"title": "x", "status": "nope"}]}))(
            req
        )

    events = _run(monkeypatch, handler)
    assert [e.title for e in events] == ["회의"]
    assert seen["body"]["messages"][1]["content"].endswith(
        "내일 7시 ㄱ [날짜: 내일=2026-10-13(화)]"
    )


def test_failures_return_empty(monkeypatch):
    assert _run(monkeypatch, _reply("not json")) == []
    assert _run(monkeypatch, lambda r: httpx.Response(500)) == []

    def boom(req):
        raise httpx.ReadTimeout("slow", request=req)

    assert _run(monkeypatch, boom) == []
