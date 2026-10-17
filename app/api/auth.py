import time

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_env
from app.db.models import Admin
from app.db.session import get_session

ALGORITHM = "HS256"
TOKEN_TTL = 24 * 3600


def create_token(admin: Admin) -> str:
    payload = {"sub": admin.username, "uid": admin.id, "exp": int(time.time()) + TOKEN_TTL}
    return jwt.encode(payload, get_env().secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, get_env().secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid token")


async def current_admin(request: Request,
                        session: AsyncSession = Depends(get_session)) -> Admin:
    auth = request.headers.get("Authorization", "")
    token = request.cookies.get("token") or auth.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="login required")
    payload = decode_token(token)
    admin = (await session.execute(select(Admin).where(Admin.username == payload["sub"]))).scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=401, detail="admin not found")
    return admin
