import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import Channel, Message, User, Workspace
from app.db.session import sqlite_pragmas


def test_migration_and_constraints(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")  # migration must match the models

    async def run():
        engine = create_async_engine(url)
        event.listen(engine.sync_engine, "connect", sqlite_pragmas)
        async with AsyncSession(engine) as s:
            u = User(name="민준", email="a@x.com")
            ws = Workspace(name="팀", invite_code="abc")
            s.add_all([u, ws])
            await s.flush()
            ch = Channel(workspace_id=ws.id, name="일반")
            s.add(ch)
            await s.flush()
            s.add(Message(channel_id=ch.id, user_id=u.id, text="hi"))
            await s.commit()

            await s.delete(ws)  # cascades to channel → message
            await s.commit()
            assert await s.scalar(select(func.count()).select_from(Message)) == 0

            s.add(Workspace(name="x", invite_code="def", filter_level=5))
            with pytest.raises(IntegrityError):
                await s.commit()
        await engine.dispose()

    asyncio.run(run())
