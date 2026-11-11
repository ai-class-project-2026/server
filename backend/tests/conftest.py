import os

os.environ.setdefault("JWT_SECRET", "test-secret-" * 4)

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.session import get_session, sqlite_pragmas
from app.main import app


@pytest.fixture
def client(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    # NullPool: TestClient runs the app on its own event loop.
    engine = create_async_engine(url, poolclass=NullPool)
    event.listen(engine.sync_engine, "connect", sqlite_pragmas)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()
