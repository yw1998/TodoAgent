import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "testpass"

import pytest

from app.db.session import SessionLocal, init_db


@pytest.fixture
async def session():
    await init_db()
    async with SessionLocal() as s:
        yield s
