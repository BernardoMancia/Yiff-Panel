from __future__ import annotations

import asyncio
import logging

from telegram import Bot
from telegram.error import TelegramError

from app.config import settings
from app.database import Post, SessionLocal

logger = logging.getLogger(__name__)

_THUMBS_DOWN = "\U0001f44e"


async def _check_reactions() -> None:
    """
    Polls Telegram for MessageReactionCount updates.
    If a post reaches DISLIKE_THRESHOLD 👎 reactions, it's deleted from the
    channel and soft-deleted in the database.
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
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
                allowed_updates=["message_reaction_count"],
            )
        except TelegramError as exc:
            logger.warning("Reaction poll failed: %s", exc)
            return

        if not updates:
            return

        last_id = updates[-1].update_id
        _set_state(db, "tg_update_offset", str(last_id))

        threshold = settings.DISLIKE_THRESHOLD
        chat_id = str(settings.TELEGRAM_CHAT_ID)

        for update in updates:
            mrc = getattr(update, "message_reaction_count", None)
            if not mrc:
                continue

            thumbs_down_count = 0
            for reaction_count in mrc.reactions:
                rt = reaction_count.type
                if hasattr(rt, "emoji") and rt.emoji == _THUMBS_DOWN:
                    thumbs_down_count = reaction_count.count

            if thumbs_down_count < threshold:
                continue

            msg_id = mrc.message_id
            post = (
                db.query(Post)
                .filter(
                    Post.message_id == msg_id,
                    Post.is_deleted == False,
                )
                .first()
            )

            if not post:
                continue

            logger.warning(
                "Post e621#%s (msg_id=%d) received %d 👎 — removing from channel.",
                post.e621_id, msg_id, thumbs_down_count,
            )

            try:
                await bot.delete_message(
                    chat_id=chat_id,
                    message_id=msg_id,
                )
            except TelegramError as exc:
                logger.error("Failed to delete msg %d: %s", msg_id, exc)

            post.is_deleted = True
            post.removed_by_reaction = True
            db.commit()

            logger.info("Post e621#%s soft-deleted by reaction vote.", post.e621_id)

    except Exception as exc:
        logger.exception("Unhandled error in reaction monitor: %s", exc)
    finally:
        db.close()
