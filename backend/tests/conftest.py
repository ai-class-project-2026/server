import os

os.environ.setdefault("JWT_SECRET", "test-secret-" * 4)

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import session as db
from app.db.session import get_session, sqlite_pragmas
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
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
    monkeypatch.setattr(db, "SessionLocal", maker)  # background schedule pipeline
    with TestClient(app) as c:  # one event loop for requests + websockets
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def login(client):
    def _login(email: str) -> dict[str, str]:
        r = client.post(
            "/auth/signup",
            json={"name": email.split("@")[0], "email": email, "password": "password1"},
        )
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return _login
