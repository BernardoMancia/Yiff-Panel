from __future__ import annotations

import io
import logging
import random

import aiohttp
from telegram import Bot, InputFile, ReactionTypeEmoji
from telegram.error import TelegramError

from app.config import settings
from app.database import Post

logger = logging.getLogger(__name__)

_MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
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
        file_size = post.file_size or 0

        if file_size > _MAX_DOWNLOAD_SIZE and post.sample_url:
            url = post.sample_url
            file_size = 0

        if not url:
            logger.error("Post %s has no URL", post.e621_id)
            return False, None

        bot = self._get_bot()
        chat_id = settings.TELEGRAM_CHAT_ID
        thumb = post.sample_url or None

        try:
            if ext in _VIDEO_EXTS:
                # Baixa e envia como bytes — único modo que o Telegram exibe player inline para WebM
                video_bytes = await _download_file(url)
                if video_bytes:
                    input_file = InputFile(io.BytesIO(video_bytes), filename="video.mp4")
                    msg = await bot.send_video(
                        chat_id=chat_id,
                        video=input_file,
                        thumbnail=thumb,
                        supports_streaming=True,
                        caption=None,
                        read_timeout=120,
                        write_timeout=120,
                        connect_timeout=30,
                    )
                else:
                    raise TelegramError("Download failed, falling back")

            elif ext in _ANIM_EXTS:
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

            logger.info("Sent post e621#%s (%s) — msg_id=%d", post.e621_id, ext, msg.message_id)
            await _react_to_message(bot, chat_id, msg.message_id)
            return True, msg.message_id

        except TelegramError as exc:
            err = str(exc).lower()
            # Fallback: arquivo grande ou erro → envia sample como foto
            if post.sample_url and url != post.sample_url:
                logger.warning("Primary send failed (%s), retrying with sample_url for e621#%s", exc, post.e621_id)
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
                    logger.error("Sample fallback failed for e621#%s: %s", post.e621_id, exc2)
            logger.error("TelegramError for e621#%s: %s", post.e621_id, exc)
            return False, None
        except Exception as exc:
            logger.exception("Unexpected error sending e621#%s: %s", post.e621_id, exc)
            return False, None


telegram_sender = TelegramSender()


async def _download_file(url: str) -> bytes | None:
    """Baixa arquivo em memória com timeout generoso."""
    try:
        timeout = aiohttp.ClientTimeout(total=90, connect=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers={"User-Agent": "auto-yiff-bot/1.0"}) as resp:
                if resp.status != 200:
                    logger.warning("Download failed with status %d for %s", resp.status, url)
                    return None
                data = await resp.read()
                logger.info("Downloaded %d bytes from %s", len(data), url)
                return data
    except Exception as exc:
        logger.error("Download error for %s: %s", url, exc)
        return None


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
