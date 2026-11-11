"""Demo data: 4 accounts (password demo1234), one workspace, a chat that
negotiates a meeting, a couple of events and todos.

    make seed        # run after make migrate; safe to run twice
"""

import asyncio
import datetime as dt

from sqlalchemy import select

from app.auth import hash_password
from app.db.models import Channel, Event, Member, Message, Role, Todo, User, Workspace
from app.db.session import SessionLocal

NAMES = {"민준": "minjun", "서연": "seoyeon", "지호": "jiho", "하은": "haeun"}
CHAT = [
    ("민준", "이번주 회의 화 어때?"),
    ("서연", "화는 학원ㅠ"),
    ("지호", "그럼 목 7시?"),
    ("하은", "ㅇㅋ"),
    ("서연", "목 저녁 가능"),
]


async def main():
    async with SessionLocal() as s:
        if await s.scalar(select(User).where(User.email == "minjun@demo.com")):
            print("already seeded")
            return
        pw = hash_password("demo1234")
        users = {
            n: User(name=n, email=f"{e}@demo.com", password_hash=pw)
            for n, e in NAMES.items()
        }
        ws = Workspace(name="팀플 데모", invite_code="demo", ai_schedule_on=True)
        s.add_all([*users.values(), ws])
        await s.flush()
        ch = Channel(workspace_id=ws.id, name="일반")
        s.add(ch)
        for i, u in enumerate(users.values()):
            s.add(
                Member(
                    workspace_id=ws.id,
                    user_id=u.id,
                    role=Role.owner if i == 0 else Role.member,
                )
            )
        await s.flush()
        for name, text in CHAT:
            s.add(Message(channel_id=ch.id, user_id=users[name].id, text=text))
        today = dt.date.today()
        s.add_all(
            [
                Event(
                    workspace_id=ws.id,
                    title="중간 발표",
                    date=today + dt.timedelta(days=9),
                    start=dt.time(10),
                    location="301호",
                ),
                Event(
                    workspace_id=ws.id,
                    title="자료 조사 마감",
                    date=today + dt.timedelta(days=4),
                ),
                Todo(
                    workspace_id=ws.id,
                    title="PPT 초안",
                    assignee_id=users["서연"].id,
                    due_date=today + dt.timedelta(days=5),
                ),
                Todo(
                    workspace_id=ws.id,
                    title="설문 문항 만들기",
                    assignee_id=users["지호"].id,
                ),
            ]
        )
        await s.commit()
        print("seeded: minjun@demo.com … haeun@demo.com / demo1234, invite code 'demo'")
        print("demo: log in as 민준 and send '그럼 목 7시 디코로 확정' → AI card")


if __name__ == "__main__":
    asyncio.run(main())
