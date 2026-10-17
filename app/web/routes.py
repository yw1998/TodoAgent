"""管理页面（服务端渲染 + 表单），cookie 中存 JWT（单人使用）。"""

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import create_token, decode_token
from app.config import get_env
from app.db.models import Admin, MessageLog, Todo
from app.db.session import get_session, verify_password
from app.services import setting_service, todo_service

router = APIRouter(prefix="/admin")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def localtime(dt: datetime | None) -> str:
    """naive UTC → 配置时区展示。"""
    if not dt:
        return "-"
    local = dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(get_env().scheduler_timezone))
    return local.strftime("%Y-%m-%d %H:%M")


def localmd(dt: datetime | None) -> str:
    if not dt:
        return "-"
    local = dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(get_env().scheduler_timezone))
    return local.strftime("%m-%d %H:%M:%S")


templates.env.filters["localtime"] = localtime
templates.env.filters["localmd"] = localmd
templates.env.globals["tz_label"] = lambda: get_env().scheduler_timezone


async def require_page(request: Request):
    token = request.cookies.get("token")
    if token:
        try:
            decode_token(token)
            return None
        except Exception:
            pass
    return RedirectResponse("/admin/login", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...),
                       session: AsyncSession = Depends(get_session)):
    admin = (await session.execute(select(Admin).where(Admin.username == username))).scalar_one_or_none()
    if not admin or not verify_password(password, admin.password_hash):
        return templates.TemplateResponse(request, "login.html",
                                          {"error": "用户名或密码错误"}, status_code=401)
    resp = RedirectResponse("/admin/todos", status_code=302)
    resp.set_cookie("token", create_token(admin), httponly=True, max_age=86400)
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/admin/login", status_code=302)
    resp.delete_cookie("token")
    return resp


@router.get("/", response_class=HTMLResponse)
@router.get("/todos", response_class=HTMLResponse)
async def todos_page(request: Request, status: str | None = None, q: str | None = None,
                     page: int = 1, session: AsyncSession = Depends(get_session)):
    if redirect := await require_page(request):
        return redirect
    rows = await todo_service.admin_query(session, status, q, page=page, size=20)
    return templates.TemplateResponse(request, "todos.html", {
        "todos": rows, "f_status": status or "", "f_q": q or "", "page": page,
    })


@router.post("/todos/{todo_id}/action")
async def todo_action(request: Request, todo_id: int, action: str = Form(...),
                      session: AsyncSession = Depends(get_session)):
    if redirect := await require_page(request):
        return redirect
    if action == "done":
        await todo_service.complete_todo(session, todo_id)
    elif action == "cancel":
        await todo_service.delete_todo(session, todo_id)
    return RedirectResponse("/admin/todos", status_code=302)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, saved: int = 0, session: AsyncSession = Depends(get_session)):
    if redirect := await require_page(request):
        return redirect
    db_vals = {r["key"]: r["value"] for r in await setting_service.get_settings_for_display(session)}
    items = []
    env = setting_service.get_env()
    for key in setting_service.KNOWN_KEYS:
        is_secret = key in setting_service.SECRET_KEYS
        if is_secret:
            current = "***" if db_vals.get(key) else "(env 已配置)" if getattr(env, key, "") else ""
        else:
            current = db_vals.get(key) or getattr(env, key, "")
        items.append({"key": key, "is_secret": is_secret, "current": current})
    return templates.TemplateResponse(request, "settings.html", {"items": items, "saved": saved})


@router.post("/settings")
async def settings_submit(request: Request, session: AsyncSession = Depends(get_session)):
    if redirect := await require_page(request):
        return redirect
    form = await request.form()
    values = {k: str(v) for k, v in form.items() if v != "***"}
    await setting_service.save_settings(session, values)
    return RedirectResponse("/admin/settings?saved=1", status_code=302)


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, page: int = 1, direction: str | None = None,
                    status: str | None = None, q: str | None = None,
                    session: AsyncSession = Depends(get_session)):
    if redirect := await require_page(request):
        return redirect
    sq = select(MessageLog).order_by(MessageLog.id.desc())
    if direction in ("in", "out"):
        sq = sq.where(MessageLog.direction == direction)
    if status in ("ok", "error"):
        sq = sq.where(MessageLog.status == status)
    if q:
        sq = sq.where(MessageLog.content.contains(q))
    rows = (await session.execute(sq.offset((page - 1) * 30).limit(30))).scalars().all()
    return templates.TemplateResponse(request, "logs.html", {
        "rows": rows, "page": page,
        "f_dir": direction or "", "f_status": status or "", "f_q": q or "",
    })
