"""The one prompt format for the schedule model. Training (ml/schedule) imports
this too, so the fine-tuned model sees exactly what serving sends."""

import datetime as dt
import json

from app.schemas import ScheduleRequest
from dateparse_ko import parse

WEEKDAYS = "월화수목금토일"

SYSTEM = """너는 학생 팀 단톡에서 일정을 추적한다.
대화 전체를 보고 일정 후보마다 최종 상태를 판단해 JSON으로만 답한다.
- status: proposed(제안 중) / rejected(폐기) / confirmed(확정) / cancelled(취소)
- 확정: 마지막 수정안에 제안자 포함 과반이 동의하고 반대가 없거나, 명시적 확정 선언 + 1명 이상 동의
- 불참 선언자는 absent, 무응답자는 attendees에 포함, 늦은 합류는 note
- 날짜는 대화의 [날짜:] 주석을 그대로 쓴다. 시각이 숫자가 아니면 start=null, 원문은 time_text
- 외부 일정(시험 등), 과거 일정, 날짜 없는 막연한 약속은 넣지 않는다
출력: {"events": [{"title", "date": "YYYY-MM-DD", "weekday", "start": "HH:MM"|null,
"time_text", "location", "status", "attendees": [], "absent": [], "note"}]}"""


def _day(d: dt.date) -> str:
    return f"{d.isoformat()}({WEEKDAYS[d.weekday()]})"


def annotate(text: str, ref: dt.date) -> str:
    """Step ② of plan 8.4: append resolved dates so the LLM never does date math."""
    notes = [
        f"{text[m.start : m.end]}={_day(m.date)}" for m in parse(text, ref) if m.date
    ]
    return f"{text} [날짜: {', '.join(notes)}]" if notes else text


def build_messages(req: ScheduleRequest) -> list[dict]:
    ref = req.ref_datetime
    lines = [
        f"{t.speaker}: {annotate(t.text, (t.ts or ref).date())}" for t in req.dialogue
    ]
    existing = [e.model_dump(mode="json") for e in req.existing_events]
    user = (
        f"기준 시각: {ref.isoformat()} ({WEEKDAYS[ref.weekday()]})\n"
        f"참여자: {', '.join(req.participants)}\n"
        f"기존 일정: {json.dumps(existing, ensure_ascii=False)}\n"
        "대화:\n" + "\n".join(lines)
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
