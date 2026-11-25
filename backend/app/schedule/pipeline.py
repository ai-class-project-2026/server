"""Chat → schedule cards. Runs per channel after a debounce (plan 10.1): 10s after the last message.
대화가 이어지는 동안에는 돌리지 않는다: 협상 도중에 돌리면 아직 안 정해진 시간으로 카드가 먼저 뜬다."""

import asyncio
import datetime as dt
import logging

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import session as db
from app.db.models import (
    AISuggestion,
    Channel,
    Event,
    EventStatus,
    Member,
    Message,
    SuggestionAction,
    User,
    Workspace,
)
from app.realtime import hub
from app.schedule import reconcile, tracker, trigger
from app.schedule.prompt import KST, to_kst
from app.schemas import (
    DialogueTurn,
    ScheduleEvent,
    ScheduleRequest,
    SuggestionOut,
    SuggestionPayload,
    SuggestionResult,
)

log = logging.getLogger(__name__)
WINDOW = 30  # turns of history the model sees (at most)
GAP = dt.timedelta(hours=8)  # 대화가 이만큼 멈췄다가 다시 시작하면 새 대화로 본다
FLOOR = 0  # 지금 대화가 이보다 짧으면 마지막 FLOOR개를 보낸다 (0 = 안 늘림)
# GAP과 FLOOR는 3B 모델로 잰 값이다. 일정 모델을 바꾸면 다시 재서 맞춘다.
RECENT = 5  # 최근 이만큼의 메시지에 날짜·시간 말이 있을 때만 모델을 부른다

_pending: dict[int, asyncio.Task] = {}
# 채널마다 한 번에 하나만 실행한다: 겹쳐 돌면 둘 다 "아직 카드 없음"을 보고 같은 카드를 두 장 만든다.
# (서버는 워커 1개로 돌린다. 여러 워커로 늘리면 DB의 유일 제약으로 바꿔야 한다)
_locks: dict[int, asyncio.Lock] = {}


def notify(channel_id: int) -> None:
    """Call after each saved message (only when the workspace has AI on)."""
    if task := _pending.pop(channel_id, None):
        task.cancel()
    _pending[channel_id] = asyncio.create_task(_later(channel_id, settings.schedule_debounce_seconds))


async def _later(channel_id: int, delay: float) -> None:
    await asyncio.sleep(delay)
    _pending.pop(channel_id, None)
    try:
        async with db.SessionLocal() as session:
            await run(session, channel_id)
    except Exception:
        log.exception("schedule pipeline failed for channel %s", channel_id)


def suggestion_out(s: AISuggestion) -> SuggestionOut:
    return SuggestionOut(
        id=s.id,
        channel_id=s.channel_id,
        payload=SuggestionPayload.model_validate(s.payload_json),
        action=s.action,
        created_at=s.created_at,
    )


def _window(rows: list, answered_upto: int) -> list:
    """모델에 보낼 턴 = 지금 대화: 가장 늦은 경계 뒤의 메시지. 경계는 (1) 팀이 답한(수락·무시) 카드가 다룬
    마지막 메시지, (2) GAP 이상 멈췄던 곳. 이미 확정된 일정은 existing_events로 따로 가니, 끝난 대화를 다시
    보낼 필요가 없다. 보내면 모델이 옛 결론에 끌려 새 결정("8시로 변경")을 놓친다. rows는 오래된 것부터."""
    start = max(
        [0]
        + [i + 1 for i, r in enumerate(rows) if r.id <= answered_upto]
        + [i for i in range(1, len(rows)) if rows[i].created_at - rows[i - 1].created_at >= GAP]
    )
    return rows[-max(len(rows) - start, FLOOR, 1) :]


async def run(session: AsyncSession, channel_id: int) -> list[AISuggestion]:
    async with _locks.setdefault(channel_id, asyncio.Lock()):
        return await _run(session, channel_id)


async def _run(session: AsyncSession, channel_id: int) -> list[AISuggestion]:
    channel = await session.get(Channel, channel_id)
    ws = await session.get(Workspace, channel.workspace_id)
    # 동의 범위: 꺼져 있으면 아무것도 보내지 않고, 켜진 뒤(ai_enabled_at 이후)의 메시지만 분석한다.
    if not ws.ai_schedule_on or ws.ai_enabled_at is None:
        return []

    rows = (
        await session.execute(
            select(Message.id, Message.text, Message.created_at, User.name)
            .join(User)
            .where(
                Message.channel_id == channel_id,
                Message.created_at >= ws.ai_enabled_at,
            )
            .order_by(Message.id.desc())
            .limit(WINDOW)
        )
    ).all()[::-1]
    if not rows or not trigger.should_run([r.text for r in rows[-RECENT:]]):
        return []

    # 이 채널의 최근 카드 (망가진 카드는 건너뛴다). 팀이 답한(수락·무시) 카드가 다룬 마지막 메시지까지는 이미 끝난 대화다.
    recent = []
    for s in await session.scalars(
        select(AISuggestion)
        .where(AISuggestion.channel_id == channel_id)
        .order_by(AISuggestion.id.desc())
        .limit(50)
    ):
        try:
            recent.append((s, SuggestionPayload.model_validate(s.payload_json)))
        except ValidationError:
            continue
    answered_upto = max([0, *(i for s, card in recent if s.action is not None for i in card.message_ids)])
    rows = _window(rows, answered_upto)

    names = await session.scalars(
        select(User.name).join(Member).where(Member.workspace_id == ws.id)
    )
    stored = list(
        await session.scalars(
            select(Event).where(
                Event.workspace_id == ws.id,
                Event.status == EventStatus.confirmed,
                Event.date >= dt.datetime.now(KST).date(),  # 한국 날짜 기준
            )
        )
    )
    existing, existing_out = [], []
    for e in stored:  # 예전에 저장된 값이 지금 제한에 안 맞아도 카드 만들기가 멈추면 안 된다
        try:
            existing_out.append(ScheduleEvent.model_validate(e, from_attributes=True))
            existing.append(e)
        except ValidationError:
            log.warning("skipping event %s in prompt: invalid", e.id)
    # 서버 안에서 DB 값으로 만드는 요청이라 model_construct로 만든다. 외부 요청용 크기 제한
    # (참여자 200명, 기존 일정 200개 등)에 걸려 큰 캘린더에서 카드 만들기가 멈추면 안 된다.
    req = ScheduleRequest.model_construct(
        ref_datetime=rows[-1].created_at,
        participants=list(names),
        dialogue=[
            DialogueTurn(speaker=r.name, text=r.text, ts=r.created_at) for r in rows
        ],
        existing_events=existing_out,
    )
    events = await tracker.track(req)
    proposals = reconcile.reconcile(
        events,
        existing,
        [(r.text, to_kst(r.created_at).date()) for r in rows],  # 날짜 표현은 쓴 날 기준으로 푼다
    )

    # 대기 카드인데 이번 대화 읽기가 그 칸(날짜·시각)을 폐기/취소라고 하면 카드를 거둔다: 너무 일찍 뜬 "6시" 카드가
    # 협상이 "7시"로 끝난 뒤에도 남지 않게. 캘린더에서 답할 때 의미 없어진 카드를 닫는 것과 같은 방법이다
    # ("무시"로 남기고 suggestion_resolved를 보낸다). 답한 카드는 action IS NULL 조건에 걸리지 않아 바뀌지 않는다.
    gone = [s.id for s, card in recent if s.action is None and reconcile.contradicted(card, events)]
    withdrawn = []
    if gone:
        withdrawn = list(
            await session.scalars(
                update(AISuggestion)
                .where(AISuggestion.id.in_(gone), AISuggestion.action.is_(None))
                .values(action=SuggestionAction.ignored)
                .returning(AISuggestion)
                .execution_options(synchronize_session=False)
            )
        )
        for s in withdrawn:
            s.action = SuggestionAction.ignored  # 메모리의 값도 맞춰 둔다 (DB에는 위에서 이미 반영)

    # The same conclusion comes back on every run; don't re-show a card
    # the team already saw (seen: pending or answered). taken: 대기 중이거나 수락된 카드가 차지한 일정 칸.
    # 모델은 실행마다 제목·참석자를 조금씩 바꿔 써서 identity만으로는 같은 일정 카드가 또 나온다.
    # 무시한(거둔) 카드는 칸을 막지 않는다: 너무 일찍 떠서 무시한 일정이 나중에 확정되면 다시 보여 준다.
    seen = [reconcile.identity(card) for _, card in recent]
    taken = {reconcile.slot(card) for s, card in recent if s.action != SuggestionAction.ignored}
    created = []
    for p in proposals:
        slot = reconcile.slot(p)
        if reconcile.identity(p) in seen or (slot and slot in taken):
            continue
        seen.append(reconcile.identity(p))  # 같은 응답 안의 중복도 막는다
        taken.add(slot)
        p.message_ids = [r.id for r in rows]
        s = AISuggestion(
            channel_id=channel_id,
            payload_json=p.model_dump(mode="json"),
            model_version=settings.schedule_model_version,
        )
        session.add(s)
        created.append(s)
    await session.commit()
    for s in withdrawn:  # 다른 팀원 화면에서도 답한 카드처럼 사라진다
        out = SuggestionResult(suggestion=suggestion_out(s), event=None)
        await hub.broadcast(channel_id, "suggestion_resolved", out.model_dump(mode="json"))
    for s in created:
        await session.refresh(s)
        await hub.broadcast(
            channel_id, "suggestion", suggestion_out(s).model_dump(mode="json")
        )
    return created


if __name__ == "__main__":  # PYTHONPATH=backend:ml/common uv run python -m app.schedule.pipeline
    from types import SimpleNamespace

    t0 = dt.datetime(2026, 12, 1, 9, tzinfo=dt.UTC)
    at = lambda i: t0 + dt.timedelta(minutes=i) + (dt.timedelta(hours=9) if i >= 121 else dt.timedelta())
    rows = [SimpleNamespace(id=i, created_at=at(i)) for i in range(101, 131)]  # 30턴, 120과 121 사이 9시간 멈춤
    ids = lambda answered_upto, rs=rows: [r.id for r in _window(rs, answered_upto)]
    flat = [SimpleNamespace(id=i, created_at=t0 + dt.timedelta(minutes=i)) for i in range(101, 131)]
    assert ids(0, flat) == list(range(101, 131))  # 경계가 없으면 30턴 그대로
    assert ids(110, flat) == list(range(111, 131))  # 답한 카드가 다룬 메시지 뒤부터
    assert ids(128, flat) == [129, 130] and ids(130, flat) == ids(999, flat) == [130]  # 짧아도 늘리지 않는다(FLOOR=0)
    assert ids(0) == ids(110) == list(range(121, 131))  # GAP 이상 멈춘 뒤부터 (더 늦은 경계가 이긴다)
    assert ids(125) == list(range(126, 131))
    seven = [SimpleNamespace(id=r.id, created_at=r.created_at - (dt.timedelta(hours=2) if r.id >= 121 else dt.timedelta())) for r in rows]
    assert ids(0, seven) == list(range(101, 131))  # 7시간 멈춤은 경계가 아니다
    ev = dict(date="2026-12-02", status="confirmed")
    card = lambda title, start, kind="add", eid=None: SuggestionPayload(
        kind=kind, event_id=eid, event=ScheduleEvent(title=title, start=start, **ev)
    )
    assert reconcile.slot(card("중간 점검", "18:30")) == reconcile.slot(card("자료 조사 중간 점검", "18:30"))
    assert reconcile.slot(card("중간 점검", "18:30")) != reconcile.slot(card("중간 점검", "19:00"))
    assert reconcile.slot(card("a", None, "cancel", 1)) == reconcile.slot(card("b", "15:00", "cancel", 1))
    assert reconcile.slot(card("점심 회의", None)) is None  # 숫자 시각이 없으면 identity로만 비교
    said = lambda start, status: [ScheduleEvent(title="딴 제목", date="2026-12-02", start=start, status=status)]
    assert reconcile.contradicted(card("중간 점검", "16:30"), said("16:30", "rejected"))  # 그 칸을 폐기로 읽음: 거둔다
    assert not reconcile.contradicted(card("중간 점검", "16:30"), said("18:30", "rejected"))  # 다른 칸
    assert not reconcile.contradicted(card("중간 점검", "16:30"), said("16:30", "proposed") + said("18:30", "confirmed"))
    assert not reconcile.contradicted(card("a", "16:30", "cancel", 1), said("16:30", "rejected"))  # 취소 카드는 그대로
    assert not reconcile.contradicted(card("중간 점검", "16:30"), said("16:30", "rejected") + said("16:30", "confirmed"))  # 같은 칸을 확정으로도 읽음
    assert not reconcile.contradicted(card("중간 점검", "16:30"), [])  # 모델 실패(빈 출력)로는 거두지 않는다
    print("ok")
