from __future__ import annotations

import logging

from telegram import Bot
from telegram.error import TelegramError

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)


async def poll_telegram_updates() -> None:
    if not settings.TELEGRAM_BOT_TOKEN:
        return

    db = SessionLocal()
    try:
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

        from app.scheduler import _get_state, _set_state

        offset_str = _get_state(db, "tg_update_offset")
        offset = int(offset_str) + 1 if offset_str else None

        try:
            updates = await bot.get_updates(
                offset=offset,
                limit=100,
                timeout=0,
                allowed_updates=["message", "message_reaction_count"],
            )
        except TelegramError as exc:
            logger.warning("Unified poll failed: %s", exc)
            return

        if not updates:
            return

        last_id = updates[-1].update_id
        _set_state(db, "tg_update_offset", str(last_id))

        reaction_updates = [u for u in updates if getattr(u, "message_reaction_count", None)]
        message_updates = [u for u in updates if u.message]

        if reaction_updates:
            from app.reaction_monitor import _process_reaction_updates
            await _process_reaction_updates(bot, db, reaction_updates)

        if message_updates and settings.TELEGRAM_INGEST_CHAT_ID:
            from app.ingest_listener import _process_ingest_message
            ingest_chat = str(settings.TELEGRAM_INGEST_CHAT_ID)
            for update in message_updates:
                await _process_ingest_message(bot, update.message, ingest_chat)

    except Exception as exc:
        logger.exception("Unified poller error: %s", exc)
    finally:
        db.close()
