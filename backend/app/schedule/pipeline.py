"""Chat → schedule cards. Runs per channel after a debounce (plan 10.1):
10s after the last message, or right away every 5th message."""

import asyncio
import datetime as dt
import logging

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
    channel = await session.get(Channel, channel_id)
    ws = await session.get(Workspace, channel.workspace_id)
    if not ws.ai_schedule_on:
        return []

    rows = (
        await session.execute(
            select(Message.id, Message.text, Message.created_at, User.name)
            .join(User)
            .where(Message.channel_id == channel_id)
            .order_by(Message.id.desc())
            .limit(WINDOW)
        )
    ).all()[::-1]
    if not rows or not trigger.should_run([r.text for r in rows[-EVERY_N:]]):
        return []

    names = await session.scalars(
        select(User.name).join(Member).where(Member.workspace_id == ws.id)
    )
    existing = list(
        await session.scalars(
            select(Event).where(
                Event.workspace_id == ws.id,
                Event.status == EventStatus.confirmed,
                Event.date >= dt.date.today(),
            )
        )
    )
    req = ScheduleRequest(
        ref_datetime=rows[-1].created_at,
        participants=list(names),
        dialogue=[
            DialogueTurn(speaker=r.name, text=r.text, ts=r.created_at) for r in rows
        ],
        existing_events=[
            ScheduleEvent.model_validate(e, from_attributes=True) for e in existing
        ],
    )
    proposals = reconcile.reconcile(await tracker.track(req), existing)

    # The same conclusion comes back on every run; don't re-show a card
    # the team already saw (pending or answered).
    seen = [
        SuggestionPayload.model_validate(p).model_dump(exclude={"message_ids"})
        for p in await session.scalars(
            select(AISuggestion.payload_json)
            .where(AISuggestion.channel_id == channel_id)
            .order_by(AISuggestion.id.desc())
            .limit(50)
        )
    ]
    created = []
    for p in proposals:
        if p.model_dump(exclude={"message_ids"}) in seen:
            continue
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
