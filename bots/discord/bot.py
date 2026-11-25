"""Plan B demo: a Discord bot that calls this server's AI APIs (part 4).

    uv run --group bot --env-file .env python bots/discord/bot.py

.env needs DISCORD_TOKEN (bot token, never in code), AI_API_KEY (same as the
server), and optionally SERVER_URL (default http://localhost:8000).
In the Discord developer portal, turn on "Message Content Intent" for the bot.
"""

import datetime as dt
import logging
import os
import re
import sys

import httpx

log = logging.getLogger("bot")

SERVER = os.environ.get("SERVER_URL", "http://localhost:8000")
HEADERS = {"X-API-Key": os.environ.get("AI_API_KEY", "")}

KST = dt.timezone(dt.timedelta(hours=9))  # 서버는 한국 시각(+09:00)으로 날짜를 계산한다
WD = "월화수목금토일"
LIMIT = 2000  # Discord 메시지 길이 제한
COMMAND = "!일정"
STATUSES = {"proposed", "rejected", "confirmed", "cancelled"}


def to_kst(t: dt.datetime) -> dt.datetime:
    """Discord 시각은 UTC다. 시간대가 없으면 UTC로 보고 한국 시각으로 바꾼다."""
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(KST)


def build_request(history: list, now: dt.datetime | None = None) -> dict:
    """채널 기록 → /schedule 요청. 봇이 쓴 메시지와 명령어는 대화에서 뺀다."""
    humans = [m for m in history if not m.author.bot and m.content and m.content != COMMAND]
    return {
        "ref_datetime": to_kst(now or dt.datetime.now(dt.timezone.utc)).isoformat(),
        "participants": sorted({m.author.display_name for m in humans}),
        "dialogue": [
            {
                "speaker": m.author.display_name,
                "text": m.content,
                "ts": to_kst(m.created_at).isoformat(),
            }
            for m in humans
        ],
    }


def clip(text: str, limit: int = LIMIT) -> str:
    """Discord는 2,000자를 넘으면 보내 주지 않는다. 넘으면 자르고 …을 붙인다."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _str_or_none(v, name: str) -> None:
    if v is not None and not isinstance(v, str):
        raise ValueError(f"{name}는 문자열 또는 null")


def check_events(res) -> list[dict]:
    """/schedule 응답이 약속한 모양인지 검사한다. 틀리면 ValueError (호출한 쪽이 짧은 답장으로 바꾼다)."""
    events = res.get("events") if isinstance(res, dict) else None
    if not isinstance(events, list):
        raise ValueError("events가 목록이 아님")
    for e in events:
        if not isinstance(e, dict):
            raise ValueError("일정이 객체가 아님")
        if not (isinstance(e.get("title"), str) and e["title"]):
            raise ValueError("title이 문자열이 아님")
        if not (isinstance(e.get("date"), str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", e["date"])):
            raise ValueError("date 형식이 틀림")
        dt.date.fromisoformat(e["date"])
        if e.get("status") not in STATUSES:  # 서버(API)가 쓰는 4가지 상태만 받는다
            raise ValueError("status가 올바른 값이 아님")
        if e.get("start") is not None:
            # API와 같은 시각 형식: HH:MM, HH:MM:SS(.소수). 시/분/초 범위(29:70 등)도 확인한다
            if not (isinstance(e["start"], str) and re.fullmatch(r"[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?", e["start"])):
                raise ValueError("start 형식이 틀림")
            dt.time.fromisoformat(e["start"])  # 범위가 틀리면 ValueError
        for name in ("time_text", "location", "note"):
            _str_or_none(e.get(name), name)
        for name in ("attendees", "absent"):
            names = e.get(name) or []
            if not (isinstance(names, list) and all(isinstance(x, str) for x in names)):
                raise ValueError(f"{name}가 이름 목록이 아님")
    return events


def is_profane(res) -> bool:
    """/moderate 응답에서 욕설 여부. 진짜 true일 때만 True다 ("false" 문자열이나 1은 경고하지 않는다)."""
    if not isinstance(res, dict) or not isinstance(res.get("is_profane"), bool):
        raise ValueError("is_profane가 true/false가 아님")
    return res["is_profane"]


def render_card(e: dict) -> str:
    day = dt.date.fromisoformat(e["date"])
    when = e.get("start") or e.get("time_text") or "시간 미정"
    lines = [f"📅 **{e['title']}**", f"🕒 {day:%m/%d}({WD[day.weekday()]}) {when[:5] if e.get('start') else when}"]
    if e.get("location"):
        lines.append(f"📍 {e['location']}")
    if e.get("attendees"):
        lines.append("👥 " + ", ".join(e["attendees"]))
    if e.get("absent"):
        lines.append("🚫 불참 " + ", ".join(e["absent"]))
    if e.get("note"):
        lines.append(f"📝 {e['note']}")
    return "\n".join(lines)


def render_events(events: list[dict]) -> str:
    """확정된 일정만 카드로 보여 준다. 없으면 '확정된 일정 없음'."""
    cards = [render_card(e) for e in events if e.get("status") == "confirmed"]
    return clip("\n\n".join(cards)) if cards else "확정된 일정 없음"


async def call(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(base_url=SERVER, headers=HEADERS, timeout=60) as c:
        r = await c.post(path, json=body)
        r.raise_for_status()
        return r.json()


def main():
    import discord  # 순수 함수(위)는 discord 없이도 테스트할 수 있게 여기서 가져온다

    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        sys.exit("DISCORD_TOKEN이 없어요 (.env 확인)")
    logging.basicConfig(level=logging.INFO)

    intents = discord.Intents.default()
    intents.message_content = True
    # 일정 제목/장소처럼 사용자가 쓴 글에 @everyone, <@id>가 있어도 봇이 멘션을 보내지 않게 한다
    no_mentions = discord.AllowedMentions.none()
    bot = discord.Client(intents=intents, allowed_mentions=no_mentions)

    async def safe_reply(msg, text: str):
        try:
            await msg.reply(clip(text), allowed_mentions=no_mentions)
        except discord.DiscordException as e:  # 권한 없음 등. 토큰이 나올 수 있는 e 전체는 찍지 않는다
            log.warning("reply failed: %s", type(e).__name__)

    @bot.event
    async def on_message(msg):
        if msg.author.bot:
            return

        if msg.content == COMMAND:
            try:
                history = [m async for m in msg.channel.history(limit=30, before=msg)][::-1]
                # 응답 검사와 카드 만들기까지 같은 try 안에서 한다 (모양이 틀린 응답도 짧은 답장으로 처리)
                text = render_events(check_events(await call("/schedule", build_request(history))))
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                log.warning("/schedule failed: %s", type(e).__name__)
                return await safe_reply(msg, "AI 서버에서 제대로 된 답을 못 받았어요. 잠시 뒤에 다시 해 주세요.")
            except discord.DiscordException as e:
                log.warning("history failed: %s", type(e).__name__)
                return await safe_reply(msg, "대화 기록을 읽지 못했어요. 봇 권한을 확인해 주세요.")
            return await safe_reply(msg, text)

        try:
            profane = is_profane(await call("/moderate", {"text": msg.content}))
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            log.warning("/moderate failed: %s", type(e).__name__)  # 모든 메시지마다 답하면 시끄러워서 기록만
            return
        if profane:
            # TODO(part 4): 경고만 할지, 메시지를 지우고 알릴지 (지우려면 봇에 메시지 관리 권한 필요)
            await safe_reply(msg, "⚠️ 욕설이 감지됐어요")

    bot.run(token)


if __name__ == "__main__":
    main()
