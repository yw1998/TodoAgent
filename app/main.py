from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import admin, webhook
from app.config import get_env
from app.db.session import init_db
from app.scheduler import reminder
from app.web.routes import router as web_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    reminder.start()
    await reminder.restore_jobs()
    workers = await webhook.worker_loop(get_env().worker_concurrency)
    yield
    for t in workers:
        t.cancel()
    reminder.shutdown()


app = FastAPI(title="TodoAgent", lifespan=lifespan)
app.include_router(webhook.router)
app.include_router(admin.router)
app.include_router(web_router)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
