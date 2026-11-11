from collections import defaultdict

from fastapi import WebSocket


class Hub:
    """channel_id → open sockets. ponytail: in-process, so run ONE server worker;
    move to Redis pub/sub if you ever scale out."""

    def __init__(self):
        self.rooms: dict[int, set[WebSocket]] = defaultdict(set)

    async def broadcast(self, channel_id: int, type_: str, data) -> None:
        for ws in list(self.rooms[channel_id]):
            try:
                await ws.send_json({"type": type_, "data": data})
            except Exception:  # noqa: BLE001 - any send failure means the socket is gone
                self.rooms[channel_id].discard(ws)


hub = Hub()
