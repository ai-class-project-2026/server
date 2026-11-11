"""Step ③ of plan 8.4: call the schedule LLM, validate its JSON."""

import json
import logging

import httpx
from pydantic import ValidationError

from app.config import settings
from app.schedule.prompt import build_messages
from app.schemas import ScheduleEvent, ScheduleRequest

log = logging.getLogger(__name__)
_transport = None  # tests swap in httpx.MockTransport


async def track(req: ScheduleRequest) -> list[ScheduleEvent]:
    """[] on any failure: a missed card is fine, a crashed chat is not."""
    if not settings.schedule_llm_url:
        return []
    try:
        async with httpx.AsyncClient(
            timeout=settings.schedule_llm_timeout, transport=_transport
        ) as client:
            r = await client.post(
                f"{settings.schedule_llm_url.rstrip('/')}/v1/chat/completions",
                json={
                    "model": settings.schedule_llm_model,
                    "messages": build_messages(req),
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        events = json.loads(content)["events"]
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
        log.warning("schedule LLM failed: %r", e)  # %r: timeouts have empty str()
        return []
    out = []
    for e in events:  # keep the valid ones even if one is malformed
        try:
            out.append(ScheduleEvent.model_validate(e))
        except ValidationError as err:
            log.warning("dropping invalid event %s: %s", e, err)
    return out
