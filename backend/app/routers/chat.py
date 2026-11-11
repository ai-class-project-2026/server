from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from app import auth
from app.auth import CurrentUser
from app.db.models import Channel, Message, Report, User, Workspace
from app.deps import Session, get_owned, require_member
from app.moderation import moderate
from app.realtime import hub
from app.schedule import pipeline
from app.schemas import ChannelIn, ChannelOut, MessageIn, MessageOut, ReportIn

router = APIRouter(tags=["chat"])


@router.get("/workspaces/{workspace_id}/channels", response_model=list[ChannelOut])
async def list_channels(workspace_id: int, user: CurrentUser, session: Session):
    await require_member(session, workspace_id, user)
    return await session.scalars(
        select(Channel).where(Channel.workspace_id == workspace_id).order_by(Channel.id)
    )


@router.post(
    "/workspaces/{workspace_id}/channels", response_model=ChannelOut, status_code=201
)
async def create_channel(
    workspace_id: int, body: ChannelIn, user: CurrentUser, session: Session
):
    await require_member(session, workspace_id, user)
    ch = Channel(workspace_id=workspace_id, name=body.name)
    session.add(ch)
    await session.commit()
    return ch


def _out(m: Message, user_name: str) -> MessageOut:
    return MessageOut(
        id=m.id,
        channel_id=m.channel_id,
        user_id=m.user_id,
        user_name=user_name,
        text=m.text,
        created_at=m.created_at,
        is_profane=m.is_profane,
        profanity_spans=m.profanity_spans,
    )


@router.get("/channels/{channel_id}/messages", response_model=list[MessageOut])
async def list_messages(
    channel_id: int,
    user: CurrentUser,
    session: Session,
    before_id: int | None = None,
    limit: int = Query(50, ge=1, le=100),
):
    """Newest `limit` messages (older than before_id), oldest first."""
    await get_owned(session, Channel, channel_id, user)
    q = select(Message, User.name).join(User).where(Message.channel_id == channel_id)
    if before_id is not None:
        q = q.where(Message.id < before_id)
    rows = (await session.execute(q.order_by(Message.id.desc()).limit(limit))).all()
    return [_out(m, name) for m, name in reversed(rows)]


@router.post(
    "/channels/{channel_id}/messages", response_model=MessageOut, status_code=201
)
async def send_message(
    channel_id: int, body: MessageIn, user: CurrentUser, session: Session
):
    channel = await get_owned(session, Channel, channel_id, user)
    ws = await session.get(Workspace, channel.workspace_id)
    context = list(
        await session.scalars(
            select(Message.text)
            .where(Message.channel_id == channel_id)
            .order_by(Message.id.desc())
            .limit(3)
        )
    )[::-1]
    # Moderate before saving (plan 10.1); CPU-bound model → thread pool.
    res = await run_in_threadpool(moderate, body.text, context, ws.filter_level)
    msg = Message(
        channel_id=channel_id,
        user_id=user.id,
        text=body.text,
        is_profane=res.is_profane,
        profanity_score=res.score,
        profanity_spans=res.spans,
        model_version=res.model_version,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    out = _out(msg, user.name)
    await hub.broadcast(channel_id, "message", out.model_dump(mode="json"))
    if ws.ai_schedule_on:
        pipeline.notify(channel_id)
    return out


@router.websocket("/ws/channels/{channel_id}")
async def channel_socket(
    websocket: WebSocket, channel_id: int, token: str, session: Session
):
    """Receive-only: server pushes {"type": "message" | "suggestion" |
    "suggestion_resolved", "data": ...}. Send messages with POST.
    Browsers can't set headers on WebSocket, so the JWT comes as ?token=."""
    try:
        user = await auth.user_from_token(token, session)
        await get_owned(session, Channel, channel_id, user)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    finally:
        await session.close()  # don't hold a DB connection for the socket's lifetime
    await websocket.accept()
    hub.rooms[channel_id].add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.rooms[channel_id].discard(websocket)


@router.post("/reports", status_code=201)
async def report(body: ReportIn, user: CurrentUser, session: Session) -> dict[str, int]:
    """False positive / missed profanity → retraining data (plan 9)."""
    await get_owned(session, Message, body.message_id, user)
    r = Report(message_id=body.message_id, user_id=user.id, type=body.type)
    session.add(r)
    await session.commit()
    return {"id": r.id}
