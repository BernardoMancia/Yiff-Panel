from __future__ import annotations

import logging

from telegram import Bot
from telegram.error import TelegramError

from app.config import settings
from app.database import SessionLocal
from app.state_store import get_state, set_state

logger = logging.getLogger(__name__)

_bot_instance: Bot | None = None


def _get_bot() -> Bot | None:
    global _bot_instance
    if not settings.TELEGRAM_BOT_TOKEN:
        return None
    if _bot_instance is None:
        _bot_instance = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    return _bot_instance


async def poll_telegram_updates() -> None:
    bot = _get_bot()
    if bot is None:
        return

    db = SessionLocal()
    try:
        offset_str = get_state(db, "tg_update_offset")
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
        set_state(db, "tg_update_offset", str(last_id))

        reaction_updates = [u for u in updates if getattr(u, "message_reaction_count", None)]
        message_updates = [u for u in updates if u.message]

        if reaction_updates:
            from app.reaction_monitor import _process_reaction_updates
            await _process_reaction_updates(bot, db, reaction_updates)

        if message_updates and settings.TELEGRAM_INGEST_CHAT_ID:
            from app.ingest_listener import process_ingest_batch
            ingest_chat = str(settings.TELEGRAM_INGEST_CHAT_ID)
            all_messages = [u.message for u in message_updates]
            await process_ingest_batch(bot, all_messages, ingest_chat)

    except Exception as exc:
        logger.exception("Unified poller error: %s", exc)
    finally:
        db.close()
