import asyncio
import datetime as dt
import json
import time

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    WebSocket,
    status,
)
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError

from app import auth, moderation_client
from app.auth import CurrentUser
from app.db import session as db
from app.db.models import Channel, Message, Report, User, Workspace
from app.deps import Session, get_owned, require_member
from app.realtime import Conn, hub
from app.schedule import pipeline
from app.schemas import ChannelIn, ChannelOut, MessageIn, MessageOut, ReportIn

router = APIRouter(tags=["chat"])
AUTH_TIMEOUT = 5  # 소켓을 연 뒤 첫 인증 프레임을 기다리는 시간(초)


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
        client_id=m.client_id,
    )


async def _same_client_id(session, channel_id: int, user_id: int, client_id: str):
    return await session.scalar(
        select(Message).where(
            Message.channel_id == channel_id,
            Message.user_id == user_id,
            Message.client_id == client_id,
        )
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
    user_id, user_name = user.id, user.name  # rollback 뒤에는 user 값이 만료되어 읽을 수 없으니 미리 꺼내 둔다
    if body.client_id:  # 응답을 못 받고 다시 보낸 경우: 기존 메시지를 그대로 돌려준다 (방송/AI 없음)
        if prior := await _same_client_id(session, channel_id, user_id, body.client_id):
            return _out(prior, user_name)
    ws = await session.get(Workspace, channel.workspace_id)
    context = list(
        await session.scalars(
            select(Message.text)
            .where(Message.channel_id == channel_id)
            .order_by(Message.id.desc())
            .limit(3)
        )
    )[::-1]
    # Moderate before saving (plan 10.1): 모델 서버가 있으면 거기서, 없거나 답이 없으면 이 서버 안에서.
    res = await moderation_client.check(body.text, context, ws.filter_level)
    msg = Message(
        channel_id=channel_id,
        user_id=user_id,
        text=body.text,
        # AI 동의 시각(ai_enabled_at)과 정확히 비교하려고 DB 기본값(초 단위) 대신 직접 넣는다
        created_at=dt.datetime.now(dt.UTC),
        client_id=body.client_id,
        is_profane=res.is_profane,
        profanity_score=res.score,
        profanity_spans=res.spans,
        model_version=res.model_version,
    )
    session.add(msg)
    try:
        await session.commit()
    except IntegrityError:
        # 같은 client_id가 동시에 들어와 다른 요청이 먼저 저장했다
        await session.rollback()
        prior = body.client_id and await _same_client_id(
            session, channel_id, user_id, body.client_id
        )
        if not prior:
            raise
        return _out(prior, user_name)
    await session.refresh(msg)
    out = _out(msg, user_name)
    await hub.broadcast(channel_id, "message", out.model_dump(mode="json"))
    if ws.ai_schedule_on:
        pipeline.notify(channel_id)
    return out


async def _ws_close(websocket: WebSocket) -> None:
    try:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
    except Exception:  # noqa: BLE001 - 이미 끊긴 소켓
        pass


async def _ws_auth(websocket: WebSocket, channel_id: int) -> tuple[int, float] | None:
    """첫 프레임 {"type":"auth","token":...}을 기다려 검사한다. 통과하면 (사용자 id, 만료 시각)."""
    try:
        msg = await asyncio.wait_for(websocket.receive(), AUTH_TIMEOUT)
        first = json.loads(msg["text"])
        if first["type"] != "auth":
            return None
        user_id, exp = auth.token_claims(first["token"])
        async with db.SessionLocal() as session:
            if (user := await session.get(User, user_id)) is None:  # 토큰은 맞지만 지워진 계정
                return None
            await get_owned(session, Channel, channel_id, user)  # 없는 채널/비멤버는 둘 다 실패
    except (
        TimeoutError,
        HTTPException,
        KeyError,
        TypeError,
        ValueError,  # JSON 오류도 여기 포함
        OverflowError,  # 2^63 이상의 채널 번호·사용자 번호는 SQLite가 못 받는다 (HTTP는 main.py가 422로 바꾼다)
    ):
        return None
    return user_id, exp


@router.websocket("/ws/channels/{channel_id}")
async def channel_socket(websocket: WebSocket, channel_id: int):
    """Receive-only: server pushes {"type": "message" | "suggestion" |
    "suggestion_resolved" | "member_joined" |
    "workspace_updated" | "events_changed", "data": ...}. Send messages with POST.
    URL에 토큰을 넣으면 접속 로그에 남아서, 연결한 뒤 첫 프레임으로 인증한다:
    client → {"type": "auth", "token": "<JWT>"} (5초 안에), server → {"type": "ready"}.
    실패하면 연결을 받은 뒤 1008로 닫는다(네트워크 오류와 구분하려고). 토큰이 만료되면 1008로 닫는다."""
    await websocket.accept()
    claims = await _ws_auth(websocket, channel_id)
    if claims is None:
        await _ws_close(websocket)
        return
    user_id, exp = claims
    conn = Conn(websocket, user_id, exp)
    try:
        await websocket.send_json({"type": "ready"})
        hub.add(channel_id, conn)
        while True:
            remaining = exp - time.time()
            if remaining <= 0:
                await _ws_close(websocket)
                return
            try:
                msg = await asyncio.wait_for(websocket.receive(), remaining)
            except TimeoutError:
                continue  # 만료 시각이 됐다: 위에서 닫는다
            if msg["type"] == "websocket.disconnect":
                return
    except Exception:  # noqa: BLE001 - 보내기/받기 중 끊김
        pass
    finally:
        hub.discard(channel_id, conn)


@router.post("/reports", status_code=201)
async def report(body: ReportIn, user: CurrentUser, session: Session) -> dict[str, int]:
    """False positive / missed profanity → retraining data (plan 9)."""
    message = await get_owned(session, Message, body.message_id, user)
    # 같은 사람이 같은 메시지에 같은 종류로 또 신고하면 새로 만들지 않고 기존 신고를 돌려준다
    await session.execute(
        insert(Report)
        .values(message_id=body.message_id, user_id=user.id, type=body.type, text=message.text)
        .on_conflict_do_nothing()
    )
    await session.commit()
    rid = await session.scalar(
        select(Report.id).where(
            Report.message_id == body.message_id,
            Report.user_id == user.id,
            Report.type == body.type,
        )
    )
    return {"id": rid}
