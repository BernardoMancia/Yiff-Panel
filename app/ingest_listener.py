from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import datetime, timezone

from telegram import Bot
from telegram.error import TelegramError

from app.config import settings
from app.database import AppState, Post, SessionLocal

logger = logging.getLogger(__name__)

MEDIA_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "media_cache")

_SUPPORTED_MIME = frozenset({
    "image/jpeg", "image/png", "image/gif",
    "video/mp4", "video/webm",
    "animation/gif",
})

_EXT_MAP = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "animation/gif": "gif",
}


def _next_ingest_id(db) -> int:
    from sqlalchemy import func
    result = db.query(func.min(Post.e621_id)).scalar()
    if result is not None and result < 0:
        return result - 1
    return -1


def _prepend_to_queue_order(db, post_id: int) -> None:
    import json as _json
    row = db.query(AppState).filter(AppState.key == "queue_order").first()
    if row and row.value:
        try:
            ids = _json.loads(row.value)
            ids.insert(0, post_id)
            row.value = _json.dumps(ids)
        except Exception:
            row.value = _json.dumps([post_id])
    else:
        if row:
            row.value = _json.dumps([post_id])
        else:
            db.add(AppState(key="queue_order", value=_json.dumps([post_id])))
    db.commit()


async def _handle_media_message(bot: Bot, message) -> None:
    db = SessionLocal()
    try:
        file_id = None
        file_ext = None
        file_size = 0

        if message.photo:
            largest = max(message.photo, key=lambda p: p.file_size or 0)
            file_id = largest.file_id
            file_ext = "jpg"
            file_size = largest.file_size or 0

        elif message.video:
            file_id = message.video.file_id
            mime = (message.video.mime_type or "").lower()
            file_ext = _EXT_MAP.get(mime, "mp4")
            file_size = message.video.file_size or 0

        elif message.animation:
            file_id = message.animation.file_id
            file_ext = "gif"
            file_size = message.animation.file_size or 0

        elif message.document:
            mime = (message.document.mime_type or "").lower()
            if mime not in _SUPPORTED_MIME:
                return
            file_id = message.document.file_id
            file_ext = _EXT_MAP.get(mime, "bin")
            file_size = message.document.file_size or 0

        if not file_id:
            return

        try:
            tg_file = await bot.get_file(file_id)
        except TelegramError as exc:
            logger.error("Failed to get file %s: %s", file_id, exc)
            return

        file_bytes = await tg_file.download_as_bytearray()
        if not file_bytes:
            logger.warning("Downloaded 0 bytes for file_id %s", file_id)
            return

        os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)

        ingest_id = _next_ingest_id(db)
        filename = f"ingest_{abs(ingest_id)}.{file_ext}"
        filepath = os.path.join(MEDIA_CACHE_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(file_bytes)

        now = datetime.now(timezone.utc)
        post = Post(
            e621_id=ingest_id,
            file_url=filepath,
            sample_url=None,
            preview_url=None,
            file_ext=file_ext,
            file_size=len(file_bytes),
            score=0,
            fav_count=0,
            tags="[]",
            status="queued",
            queued_at=now,
            is_deleted=False,
            is_priority=True,
            source="ingest",
        )
        db.add(post)
        db.commit()
        db.refresh(post)

        _prepend_to_queue_order(db, post.id)

        cached_row = db.query(AppState).filter(AppState.key == "next_post_id").first()
        if cached_row:
            cached_row.value = str(post.id)
        else:
            db.add(AppState(key="next_post_id", value=str(post.id)))
        db.commit()

        priority_count = db.query(Post).filter(
            Post.status == "queued",
            Post.is_deleted == False,
            Post.is_priority == True,
        ).count()

        try:
            await bot.send_message(
                chat_id=settings.TELEGRAM_INGEST_CHAT_ID,
                text=f"✅ Mídia adicionada à fila prioritária (posição #{priority_count})",
                reply_to_message_id=message.message_id,
            )
        except TelegramError as exc:
            logger.warning("Could not send confirmation: %s", exc)

        from app.scheduler import _broadcast_sse
        _broadcast_sse({"event": "priority_added", "post_id": post.id})

        logger.info(
            "Ingest: saved %s (%d bytes) as post #%d (priority #%d)",
            filename, len(file_bytes), post.id, priority_count,
        )

    except Exception as exc:
        logger.exception("Ingest handler error: %s", exc)
    finally:
        db.close()


async def _process_ingest_message(bot: Bot, msg, ingest_chat: str) -> None:
    msg_chat_id = str(msg.chat_id)
    if msg_chat_id != ingest_chat:
        return

    has_media = msg.photo or msg.video or msg.animation or (
        msg.document and (msg.document.mime_type or "").lower() in _SUPPORTED_MIME
    )
    if not has_media:
        return

    await _handle_media_message(bot, msg)
