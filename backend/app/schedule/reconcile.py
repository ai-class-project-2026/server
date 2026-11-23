"""Step ④ of plan 8.4: compare model output with the calendar → card proposals."""

import datetime as dt
import re
from collections.abc import Sequence
from difflib import SequenceMatcher

from dateparse_ko import parse

from app.db.models import Event, EventStatus
from app.schedule.prompt import KST
from app.schemas import ScheduleEvent, SuggestionKind, SuggestionPayload

SIMILAR = 0.8  # 제목이 이 정도 이상 비슷하면 같은 일정으로 본다


def _key(title: str) -> str:
    return "".join(title.split()).lower()


# "일정을 바꾸자"는 뜻의 말. 이 말만으로는 아무것도 바꾸지 못한다: 같은 대화 턴에 그 일정의 제목이나
# 그 일정의 옛 날짜/새 날짜가 같이 있어야 "이 일정을 바꾸는 말"로 인정한다 (_revises).
_REVISION = re.compile(r"변경|바꾸|바꿔|바꿀|바뀌|미루|미뤄|미뤘|옮기|옮겨|옮길|연기|앞당|당기자|대신|말고|수정")

Turn = str | tuple[str, dt.date]  # 대화 한 턴: 글만, 또는 (글, 그 턴을 쓴 날짜 KST)


def _similar(ev: ScheduleEvent, existing: list[Event]) -> list[tuple[float, Event]]:
    key = _key(ev.title)
    scored = [(SequenceMatcher(None, key, _key(e.title)).ratio(), e) for e in existing]
    return [(s, e) for s, e in scored if s >= SIMILAR]


def _revises(ev: ScheduleEvent, e: Event, turns: Sequence[Turn]) -> bool:
    """한 턴 안에 (바꾸는 말) + (이 일정의 제목, 또는 옛/새 날짜로 풀리는 날짜 표현)이 같이 있나.
    날짜 표현은 dateparse_ko로 푼다(한 글자 요일처럼 불확실한 후보도 포함)."""
    titles = {_key(ev.title), _key(e.title)} - {""}
    dates = {e.date, ev.date}
    for turn in turns:
        text, ref = (turn, dt.datetime.now(KST).date()) if isinstance(turn, str) else turn
        if not _REVISION.search(text):
            continue
        if any(title in _key(text) for title in titles):
            return True
        if any(m.date in dates for m in parse(text, ref)):
            return True
    return False


def _only(found: list[Event]) -> Event | None:
    """후보가 정확히 하나일 때만 그 일정. 여럿이면 어느 것인지 모르는 것이니 None(= 기존 일정을 건드리지 않는다)."""
    return found[0] if len(found) == 1 else None


def _match(
    ev: ScheduleEvent, existing: list[Event], turns: Sequence[Turn] = ()
) -> Event | None:
    """같은 일정만 찾는다. 확실하지 않으면 None: 수정/취소 카드는 확실한 증거가 있는 일정에만 붙인다.
    1) 같은 날짜 + 제목이 같음 + 시작 시각이 같음  2) 같은 날짜의 비슷한 제목 중 가장 닮은 것(시작 시각이 같으면 우선).
    같은 조건이 여럿이면 어느 것인지 모르므로 None. 제목이 다르면 날짜·시간이 같아도 다른 일정이다.
    날짜가 다른 같은 제목은 그 일정에 대해 "바꾸자"는 증거(_revises)가 있고 그런 후보가 하나일 때만 같은 일정."""
    similar = _similar(ev, existing)
    same_day = [(s, e) for s, e in similar if e.date == ev.date]
    if same_day:
        if ev.start is not None:
            exact = [e for s, e in same_day if s > 0.999 and e.start == ev.start]
            if exact:
                return _only(exact)
        best = max(s for s, _ in same_day)
        group = [e for s, e in same_day if s >= best - 1e-9]
        if ev.start is not None and (at_start := [e for e in group if e.start == ev.start]):
            return _only(at_start)
        return _only(group)
    return _only([e for _, e in similar if _revises(ev, e, turns)])


def _merge(ev: ScheduleEvent, e: Event) -> ScheduleEvent:
    """모델이 비워 둔 칸(빈 목록, null/빈 글)은 "새 정보 없음"이지 "지워라"가 아니다: 저장된 값을 그대로 둔다.
    카드를 수락하면 이 값이 그대로 저장되므로, 여기서 채우지 않으면 저장된 이름·장소·메모가 지워진다.
    한쪽 이름 목록이 비었을 때 저장된 값을 가져오되, 다른 쪽 목록으로 옮겨 간 사람은 뺀다(참석과 불참이 겹치면 안 된다)."""
    attendees = list(ev.attendees) or [n for n in e.attendees or [] if n not in set(ev.absent)]
    absent = list(ev.absent) or [n for n in e.absent or [] if n not in set(ev.attendees)]
    return ev.model_copy(
        update={
            "attendees": attendees,
            "absent": absent,
            "location": ev.location or e.location,
            "note": ev.note or e.note,
            # 숫자 시각(start)이 새로 정해졌으면 예전 말 시각(time_text)은 버린다 (둘이 같이 있으면 안 된다)
            "time_text": ev.time_text or (e.time_text if ev.start is None else None),
        }
    )


def _names(xs) -> set[str]:
    return {x.strip() for x in xs or []}


def _same(ev: ScheduleEvent, e: Event) -> bool:
    """카드를 만들 만한 차이가 없나. 저장되는 일정 칸을 모두 비교한다(이름 목록은 순서·중복 무시, 빈 메모 = 메모 없음)."""
    return (
        ev.date == e.date
        and ev.start == e.start
        and ev.location == e.location
        and ev.time_text == e.time_text
        and _names(ev.attendees) == _names(e.attendees)
        and _names(ev.absent) == _names(e.absent)
        and (ev.note or None) == (e.note or None)
    )


def reconcile(
    events: list[ScheduleEvent], existing: list[Event], dialogue: Sequence[Turn] = ()
) -> list[SuggestionPayload]:
    """existing = the workspace's upcoming confirmed events.
    dialogue = 분석한 대화의 턴들((글, 쓴 날짜) 또는 글만). 날짜가 바뀐 "수정"인지 새 일정인지 가를 때만 쓴다."""
    out = []
    for ev in events:
        if ev.status == EventStatus.cancelled:  # 취소는 같은 날짜의 일정만 대상으로 한다(애매하면 건드리지 않는다)
            match = _match(ev, existing)
        else:
            match = _match(ev, existing, dialogue)
        if ev.status == EventStatus.confirmed:
            if match is None:
                out.append(SuggestionPayload(kind=SuggestionKind.add, event=ev))
            elif not _same(merged := _merge(ev, match), match):
                out.append(
                    SuggestionPayload(
                        kind=SuggestionKind.update, event=merged, event_id=match.id
                    )
                )
        elif ev.status == EventStatus.cancelled and match is not None:
            out.append(
                SuggestionPayload(
                    kind=SuggestionKind.cancel, event=ev, event_id=match.id
                )
            )
    return unique(out)


def identity(p: SuggestionPayload) -> dict:
    """카드의 정체: 같은 결론인지 비교하는 기준. 어떤 메시지에서 나왔는지(message_ids)는 뺀다.
    이름 목록은 순서·중복을 없애고 글은 앞뒤 공백을 지운다. 새 카드도, 이미 저장된 카드도 이 함수로 비교한다."""
    d = p.model_dump(exclude={"message_ids"})
    ev = d["event"]
    ev["title"] = ev["title"].strip()
    for k in ("weekday", "time_text", "location", "note"):
        ev[k] = (ev[k] or "").strip() or None
    for k in ("attendees", "absent"):
        ev[k] = sorted({n.strip() for n in ev[k]} - {""})
    return d


def unique(proposals: list[SuggestionPayload]) -> list[SuggestionPayload]:
    """모델이 같은 결론을 두 번 내놔도 카드는 한 장만."""
    out, seen = [], []
    for p in proposals:
        if (key := identity(p)) not in seen:
            seen.append(key)
            out.append(p)
    return out
