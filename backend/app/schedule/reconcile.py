"""Step ④ of plan 8.4: compare model output with the calendar → card proposals."""

from app.db.models import Event, EventStatus
from app.schemas import ScheduleEvent, SuggestionKind, SuggestionPayload


def _key(title: str) -> str:
    return "".join(title.split()).lower()


def _match(ev: ScheduleEvent, existing: list[Event]) -> Event | None:
    """ponytail: same title, else same date. Upgrade to fuzzy title match if
    teams name the same meeting differently ("회의" vs "팀 회의")."""
    by_title = [e for e in existing if _key(e.title) == _key(ev.title)]
    return (by_title or [e for e in existing if e.date == ev.date] or [None])[0]


def _same(ev: ScheduleEvent, e: Event) -> bool:
    return (ev.date, ev.start, ev.location) == (e.date, e.start, e.location)


def reconcile(
    events: list[ScheduleEvent], existing: list[Event]
) -> list[SuggestionPayload]:
    """existing = the workspace's upcoming confirmed events."""
    out = []
    for ev in events:
        match = _match(ev, existing)
        if ev.status == EventStatus.confirmed:
            if match is None:
                out.append(SuggestionPayload(kind=SuggestionKind.add, event=ev))
            elif not _same(ev, match):
                out.append(
                    SuggestionPayload(
                        kind=SuggestionKind.update, event=ev, event_id=match.id
                    )
                )
        elif ev.status == EventStatus.cancelled and match is not None:
            out.append(
                SuggestionPayload(
                    kind=SuggestionKind.cancel, event=ev, event_id=match.id
                )
            )
    return out
