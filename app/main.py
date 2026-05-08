from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.database import SessionLocal, create_tables
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database...")
    create_tables()
    db = SessionLocal()
    try:
        from app.tag_manager import init_tags
        from app.auth import init_admin
        init_tags(db)
        init_admin(db)
    finally:
        db.close()

    os.makedirs("media_cache", exist_ok=True)

    logger.info("Starting scheduler...")
    start_scheduler()
    yield
    logger.info("Shutting down...")
    stop_scheduler()

    from app.e621_client import e621_client
    await e621_client.close()


app = FastAPI(
    title="Auto-Yiff",
    description="Bot Telegram + Dashboard para e621.net",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

from app.routes.api import router as api_router
from app.routes.auth_routes import router as auth_router
from app.routes.dashboard import router as dashboard_router

app.include_router(api_router, prefix="/api")
app.include_router(auth_router, prefix="/api/auth")
app.include_router(dashboard_router)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/media_cache", StaticFiles(directory="media_cache"), name="media_cache")
