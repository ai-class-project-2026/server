import asyncio
import logging
import time
from dataclasses import dataclass

from fastapi import WebSocket, status
from sqlalchemy import select

from app.db import session as db
from app.db.models import Channel, Member

log = logging.getLogger(__name__)
SEND_TIMEOUT = 2.0  # 소켓 하나가 이 시간 안에 못 받으면 멈춘 것으로 보고 끊는다


@dataclass(eq=False)
class Conn:
    """열려 있는 소켓 하나. 누구의 소켓인지와 토큰 만료 시각(epoch 초)을 같이 들고 있다."""

    ws: WebSocket
    user_id: int
    exp: float


class Hub:
    """channel_id → open sockets. ponytail: in-process, so run ONE server worker;
    move to Redis pub/sub if you ever scale out."""

    def __init__(self):
        self.rooms: dict[int, set[Conn]] = {}
        self._closing: set[asyncio.Task] = set()  # 끊는 작업이 중간에 사라지지 않게 붙잡아 둔다

    def add(self, channel_id: int, conn: Conn) -> None:
        self.rooms.setdefault(channel_id, set()).add(conn)

    def discard(self, channel_id: int, conn: Conn) -> None:
        room = self.rooms.get(channel_id)
        if room is not None:
            room.discard(conn)
            if not room:
                del self.rooms[channel_id]

    def _close_later(self, conn: Conn, code: int) -> None:
        """멈춘 소켓은 close도 오래 걸릴 수 있어서, 보내는 쪽(요청)을 기다리게 하지 않고 따로 끊는다."""

        async def close() -> None:
            try:
                await asyncio.wait_for(conn.ws.close(code), SEND_TIMEOUT)
            except Exception:  # noqa: BLE001 - 이미 끊어졌거나 멈춘 소켓: 더 할 일이 없다
                pass

        task = asyncio.create_task(close())
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    async def _member_ids(self, channel_id: int) -> set[int] | None:
        """이 채널 워크스페이스의 멤버 id들. 방송 한 번에 쿼리 한 번(소켓마다 하지 않는다)."""
        try:
            async with db.SessionLocal() as session:
                rows = await session.scalars(
                    select(Member.user_id)
                    .join(Channel, Channel.workspace_id == Member.workspace_id)
                    .where(Channel.id == channel_id)
                )
                return set(rows)
        except Exception:  # noqa: BLE001 - DB가 잠깐 안 되어도 채팅은 계속 간다
            log.exception("member check failed for channel %s", channel_id)
            return None

    async def broadcast(self, channel_id: int, type_: str, data) -> None:
        conns = list(self.rooms.get(channel_id, ()))
        if not conns:
            return
        now = time.time()
        members = await self._member_ids(channel_id)
        targets = []
        for c in conns:
            if c.exp <= now:  # 토큰이 만료됨
                self.discard(channel_id, c)
                self._close_later(c, status.WS_1008_POLICY_VIOLATION)
            elif members is not None and c.user_id not in members:  # 더는 멤버가 아님
                self.discard(channel_id, c)
                self._close_later(c, status.WS_1008_POLICY_VIOLATION)
            else:
                targets.append(c)
        payload = {"type": type_, "data": data}

        async def send(c: Conn) -> None:
            try:
                await asyncio.wait_for(c.ws.send_json(payload), SEND_TIMEOUT)
            except Exception:  # noqa: BLE001 - 시간 초과든 끊김이든 이 소켓은 버린다
                self.discard(channel_id, c)
                self._close_later(c, status.WS_1011_INTERNAL_ERROR)

        await asyncio.gather(*(send(c) for c in targets))


hub = Hub()
