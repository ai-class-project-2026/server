"""Chat → schedule cards. Runs per channel after a debounce (plan 10.1):
10s after the last message, or right away every 5th message."""

import asyncio
import datetime as dt
import logging

from pydantic import ValidationError
from sqlalchemy import select
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
)

log = logging.getLogger(__name__)
WINDOW = 30  # turns of history the model sees
EVERY_N = 5

_pending: dict[int, asyncio.Task] = {}
_counts: dict[int, int] = {}
# 채널마다 한 번에 하나만 실행한다: 겹쳐 돌면 둘 다 "아직 카드 없음"을 보고 같은 카드를 두 장 만든다.
# (서버는 워커 1개로 돌린다. 여러 워커로 늘리면 DB의 유일 제약으로 바꿔야 한다)
_locks: dict[int, asyncio.Lock] = {}


def notify(channel_id: int) -> None:
    """Call after each saved message (only when the workspace has AI on)."""
    _counts[channel_id] = _counts.get(channel_id, 0) + 1
    if task := _pending.pop(channel_id, None):
        task.cancel()
    delay = 0 if _counts[channel_id] >= EVERY_N else settings.schedule_debounce_seconds
    _pending[channel_id] = asyncio.create_task(_later(channel_id, delay))


async def _later(channel_id: int, delay: float) -> None:
    await asyncio.sleep(delay)
    _pending.pop(channel_id, None)
    _counts[channel_id] = 0
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
    if not rows or not trigger.should_run([r.text for r in rows[-EVERY_N:]]):
        return []

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
    proposals = reconcile.reconcile(
        await tracker.track(req),
        existing,
        [(r.text, to_kst(r.created_at).date()) for r in rows],  # 날짜 표현은 쓴 날 기준으로 푼다
    )

    # The same conclusion comes back on every run; don't re-show a card
    # the team already saw (pending or answered).
    seen = []
    for raw in await session.scalars(
        select(AISuggestion.payload_json)
        .where(AISuggestion.channel_id == channel_id)
        .order_by(AISuggestion.id.desc())
        .limit(50)
    ):
        try:
            seen.append(reconcile.identity(SuggestionPayload.model_validate(raw)))
        except ValidationError:  # 망가진 카드는 건너뛴다
            continue
    created = []
    for p in proposals:
        if reconcile.identity(p) in seen:
            continue
        seen.append(reconcile.identity(p))  # 같은 응답 안의 중복도 막는다
        p.message_ids = [r.id for r in rows]
        s = AISuggestion(
            channel_id=channel_id,
            payload_json=p.model_dump(mode="json"),
            model_version=settings.schedule_model_version,
        )
        session.add(s)
        created.append(s)
    await session.commit()
    for s in created:
        await session.refresh(s)
        await hub.broadcast(
            channel_id, "suggestion", suggestion_out(s).model_dump(mode="json")
        )
    return created
