from __future__ import annotations

import logging
import random

from telegram import Bot, ReactionTypeEmoji
from telegram.error import TelegramError

from app.config import settings
from app.database import Post

logger = logging.getLogger(__name__)

_MAX_DIRECT_SIZE = 50 * 1024 * 1024
_VIDEO_EXTS = {"webm", "mp4"}
_ANIM_EXTS = {"gif"}
_PHOTO_EXTS = {"jpg", "jpeg", "png"}

_REACTION_POOL = ["❤", "🔥", "🥰", "👍", "🐾"]


class TelegramSender:
    def __init__(self) -> None:
        self._bot: Bot | None = None

    def _get_bot(self) -> Bot:
        if self._bot is None:
            self._bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        return self._bot

    async def send_media(self, post: Post) -> tuple[bool, int | None]:
        ext = (post.file_ext or "").lower()
        url = post.file_url
        if post.file_size and post.file_size > _MAX_DIRECT_SIZE and post.sample_url:
            url = post.sample_url
        if not url:
            logger.error("Post %s has no URL", post.e621_id)
            return False, None
        bot = self._get_bot()
        chat_id = settings.TELEGRAM_CHAT_ID
        thumb = post.sample_url or None
        try:
            if ext == "mp4":
                msg = await bot.send_video(
                    chat_id=chat_id,
                    video=url,
                    thumbnail=thumb,
                    supports_streaming=True,
                    caption=None,
                    read_timeout=90,
                    write_timeout=90,
                    connect_timeout=30,
                )
            elif ext in _VIDEO_EXTS or ext in _ANIM_EXTS:
                # WebM e GIF: send_animation mostra inline com autoplay/loop
                msg = await bot.send_animation(
                    chat_id=chat_id,
                    animation=url,
                    thumbnail=thumb,
                    caption=None,
                    read_timeout=90,
                    write_timeout=90,
                    connect_timeout=30,
                )
            else:
                msg = await bot.send_photo(
                    chat_id=chat_id,
                    photo=url,
                    caption=None,
                    read_timeout=60,
                    write_timeout=60,
                    connect_timeout=30,
                )
            logger.info("Sent post e621#%s (%s) to Telegram — msg_id=%d", post.e621_id, ext, msg.message_id)
            await _react_to_message(bot, chat_id, msg.message_id)
            return True, msg.message_id
        except TelegramError as exc:
            err = str(exc).lower()
            # Fallback 1: arquivo muito grande → tenta sample como photo
            if ("too big" in err or "file_size" in err) and post.sample_url and url != post.sample_url:
                logger.warning("File too big, retrying with sample_url for e621#%s", post.e621_id)
                try:
                    msg = await bot.send_photo(
                        chat_id=chat_id,
                        photo=post.sample_url,
                        caption=None,
                        read_timeout=60,
                        write_timeout=60,
                        connect_timeout=30,
                    )
                    await _react_to_message(bot, chat_id, msg.message_id)
                    return True, msg.message_id
                except TelegramError as exc2:
                    logger.error("Retry also failed for e621#%s: %s", post.e621_id, exc2)
                    return False, None
            # Fallback 2: send_animation falhou → tenta send_video
            if ext in _VIDEO_EXTS and "animation" not in err:
                logger.warning("send_animation failed, trying send_video for e621#%s: %s", post.e621_id, exc)
                try:
                    msg = await bot.send_video(
                        chat_id=chat_id,
                        video=url,
                        supports_streaming=True,
                        caption=None,
                        read_timeout=90,
                        write_timeout=90,
                        connect_timeout=30,
                    )
                    await _react_to_message(bot, chat_id, msg.message_id)
                    return True, msg.message_id
                except TelegramError as exc2:
                    logger.error("send_video fallback also failed for e621#%s: %s", post.e621_id, exc2)
                    return False, None
            logger.error("TelegramError for e621#%s: %s", post.e621_id, exc)
            return False, None
        except Exception as exc:
            logger.exception("Unexpected error sending e621#%s: %s", post.e621_id, exc)
            return False, None


telegram_sender = TelegramSender()


async def _react_to_message(bot: Bot, chat_id: str, message_id: int) -> None:
    emoji = random.choice(_REACTION_POOL)
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
            is_big=True,
        )
        logger.info("Reacted to msg %d with %s", message_id, emoji)
    except TelegramError as exc:
        logger.warning("Could not set reaction on msg %d: %s", message_id, exc)
