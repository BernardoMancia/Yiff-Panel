from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
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

    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_INGEST_CHAT_ID:
        try:
            from telegram import Bot
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            utc_minus_3 = timezone(timedelta(hours=-3))
            now_br = datetime.now(utc_minus_3)
            await bot.send_message(
                chat_id=settings.TELEGRAM_INGEST_CHAT_ID,
                text=(
                    "🟢 Auto-Yiff Online\n"
                    f"⏱ {now_br.strftime('%d/%m/%Y às %H:%M')}\n"
                    "📤 Envios automáticos ativados\n"
                    "📎 Envie mídias aqui para adicionar à fila prioritária"
                ),
            )
            logger.info("Online notification sent to ingest group")
        except Exception as exc:
            logger.warning("Could not send online notification: %s", exc)

    yield
    logger.info("Shutting down...")

    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_INGEST_CHAT_ID:
        try:
            from telegram import Bot
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            await bot.send_message(
                chat_id=settings.TELEGRAM_INGEST_CHAT_ID,
                text="🔴 Auto-Yiff Offline\n⏳ O bot será reiniciado em breve.",
            )
        except Exception:
            pass

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
