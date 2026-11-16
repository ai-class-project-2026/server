"""Plan B demo: a Discord bot that calls this server's AI APIs (part 4).

    uv run --group bot --env-file .env python bots/discord/bot.py

.env needs DISCORD_TOKEN (bot token, never in code), AI_API_KEY (same as the
server), and optionally SERVER_URL (default http://localhost:8000).
In the Discord developer portal, turn on "Message Content Intent" for the bot.
"""

import datetime as dt
import os

import discord
import httpx

SERVER = os.environ.get("SERVER_URL", "http://localhost:8000")
HEADERS = {"X-API-Key": os.environ.get("AI_API_KEY", "")}

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


async def call(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(base_url=SERVER, headers=HEADERS, timeout=60) as c:
        r = await c.post(path, json=body)
        r.raise_for_status()
        return r.json()


@bot.event
async def on_message(msg: discord.Message):
    if msg.author.bot:
        return

    if msg.content == "!일정":
        history = [m async for m in msg.channel.history(limit=30, before=msg)][::-1]
        req = {
            "ref_datetime": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(),
            "participants": sorted({m.author.display_name for m in history}),
            "dialogue": [
                {"speaker": m.author.display_name, "text": m.content, "ts": m.created_at.isoformat()}
                for m in history
            ],
        }
        res = await call("/schedule", req)
        # TODO(part 4): 일정 카드처럼 보기 좋게 (날짜, 시간, 장소, 참석자). 일정이 없으면 "확정된 일정 없음"
        await msg.reply(str(res["events"]))
        return

    res = await call("/moderate", {"text": msg.content})
    if res["is_profane"]:
        # TODO(part 4): 경고만 할지, 메시지를 지우고 알릴지 (지우려면 봇에 메시지 관리 권한 필요)
        await msg.reply("⚠️ 욕설이 감지됐어요")


bot.run(os.environ["DISCORD_TOKEN"])
