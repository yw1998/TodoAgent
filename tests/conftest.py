import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_INIT_USERNAME"] = "admin"
os.environ["ADMIN_INIT_PASSWORD"] = "testpass"
# 真实环境变量优先于 .env 文件，隔离开发机的真实配置
os.environ["FEISHU_VERIFICATION_TOKEN"] = ""
os.environ["FEISHU_ENCRYPT_KEY"] = ""

import pytest

from app.db.session import SessionLocal, init_db


@pytest.fixture
async def session():
    await init_db()
    async with SessionLocal() as s:
        yield s
