from __future__ import annotations

import io
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

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


def _prepend_to_queue_order(db, post_ids: list[int]) -> None:
    import json as _json
    row = db.query(AppState).filter(AppState.key == "queue_order").first()
    if row and row.value:
        try:
            ids = _json.loads(row.value)
            ids = post_ids + ids
            row.value = _json.dumps(ids)
        except Exception:
            row.value = _json.dumps(post_ids)
    else:
        if row:
            row.value = _json.dumps(post_ids)
        else:
            db.add(AppState(key="queue_order", value=_json.dumps(post_ids)))
    db.commit()


def _estimate_send_time(db, queue_position: int) -> str:
    from app.scheduler import _get_state
    next_run_str = _get_state(db, "next_run_at")
    avg_interval = (settings.MIN_INTERVAL_SECONDS + settings.MAX_INTERVAL_SECONDS) / 2

    if next_run_str:
        try:
            base = datetime.fromisoformat(next_run_str)
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
        except ValueError:
            base = datetime.now(timezone.utc) + timedelta(seconds=avg_interval)
    else:
        base = datetime.now(timezone.utc) + timedelta(seconds=avg_interval)

    estimated = base + timedelta(seconds=avg_interval * max(0, queue_position - 1))
    utc_minus_3 = timezone(timedelta(hours=-3))
    local_time = estimated.astimezone(utc_minus_3)
    return local_time.strftime("%d/%m às %H:%M")


def _extract_file_info(message) -> tuple[str | None, str | None, int]:
    if message.photo:
        largest = max(message.photo, key=lambda p: p.file_size or 0)
        return largest.file_id, "jpg", largest.file_size or 0

    if message.video:
        mime = (message.video.mime_type or "").lower()
        return message.video.file_id, _EXT_MAP.get(mime, "mp4"), message.video.file_size or 0

    if message.animation:
        return message.animation.file_id, "gif", message.animation.file_size or 0

    if message.document:
        mime = (message.document.mime_type or "").lower()
        if mime in _SUPPORTED_MIME:
            return message.document.file_id, _EXT_MAP.get(mime, "bin"), message.document.file_size or 0

    return None, None, 0


async def _download_and_save(bot: Bot, file_id: str, file_ext: str, ingest_id: int) -> tuple[str, str, int] | None:
    try:
        tg_file = await bot.get_file(file_id)
    except TelegramError as exc:
        logger.error("Failed to get file %s: %s", file_id, exc)
        return None

    file_bytes = await tg_file.download_as_bytearray()
    if not file_bytes:
        logger.warning("Downloaded 0 bytes for file_id %s", file_id)
        return None

    os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)
    filename = f"ingest_{abs(ingest_id)}.{file_ext}"
    filepath = os.path.join(MEDIA_CACHE_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(file_bytes)

    return filepath, filename, len(file_bytes)


async def process_ingest_batch(bot: Bot, messages: list, ingest_chat: str) -> None:
    media_messages = []
    for msg in messages:
        if str(msg.chat_id) != ingest_chat:
            continue
        file_id, _, _ = _extract_file_info(msg)
        if file_id:
            media_messages.append(msg)

    if not media_messages:
        return

    groups: dict[str, list] = defaultdict(list)
    singles: list = []

    for msg in media_messages:
        if msg.media_group_id:
            groups[msg.media_group_id].append(msg)
        else:
            singles.append(msg)

    for msg in singles:
        await _process_single(bot, msg)

    for group_id, group_msgs in groups.items():
        await _process_group(bot, group_id, group_msgs)


async def _process_single(bot: Bot, message) -> None:
    db = SessionLocal()
    try:
        file_id, file_ext, file_size = _extract_file_info(message)
        if not file_id or not file_ext:
            return

        ingest_id = _next_ingest_id(db)
        result = await _download_and_save(bot, file_id, file_ext, ingest_id)
        if not result:
            return
        filepath, filename, byte_count = result

        preview_url = f"/media_cache/{filename}"
        now = datetime.now(timezone.utc)
        post = Post(
            e621_id=ingest_id,
            file_url=filepath,
            sample_url=preview_url,
            preview_url=preview_url,
            file_ext=file_ext,
            file_size=byte_count,
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

        _prepend_to_queue_order(db, [post.id])

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

        estimated = _estimate_send_time(db, priority_count)

        try:
            await bot.send_message(
                chat_id=settings.TELEGRAM_INGEST_CHAT_ID,
                text=(
                    f"✅ Mídia adicionada à fila prioritária\n"
                    f"📍 Posição: #{priority_count}\n"
                    f"🕐 Envio estimado: {estimated}"
                ),
                reply_to_message_id=message.message_id,
            )
        except TelegramError as exc:
            logger.warning("Could not send confirmation: %s", exc)

        from app.scheduler import _broadcast_sse
        _broadcast_sse({"event": "priority_added", "post_id": post.id})

        logger.info("Ingest single: %s (%d bytes) as post #%d", filename, byte_count, post.id)

    except Exception as exc:
        logger.exception("Ingest single handler error: %s", exc)
    finally:
        db.close()


async def _process_group(bot: Bot, group_id: str, messages: list) -> None:
    db = SessionLocal()
    try:
        created_ids = []
        filenames = []
        now = datetime.now(timezone.utc)

        for msg in messages:
            file_id, file_ext, file_size = _extract_file_info(msg)
            if not file_id or not file_ext:
                continue

            ingest_id = _next_ingest_id(db)
            result = await _download_and_save(bot, file_id, file_ext, ingest_id)
            if not result:
                continue
            filepath, filename, byte_count = result

            preview_url = f"/media_cache/{filename}"
            post = Post(
                e621_id=ingest_id,
                file_url=filepath,
                sample_url=preview_url,
                preview_url=preview_url,
                file_ext=file_ext,
                file_size=byte_count,
                score=0,
                fav_count=0,
                tags="[]",
                status="queued",
                queued_at=now,
                is_deleted=False,
                is_priority=True,
                source="ingest",
                media_group_id=group_id,
            )
            db.add(post)
            db.commit()
            db.refresh(post)
            created_ids.append(post.id)
            filenames.append(filename)

        if not created_ids:
            return

        _prepend_to_queue_order(db, created_ids)

        cached_row = db.query(AppState).filter(AppState.key == "next_post_id").first()
        if cached_row:
            cached_row.value = str(created_ids[0])
        else:
            db.add(AppState(key="next_post_id", value=str(created_ids[0])))
        db.commit()

        priority_count = db.query(Post).filter(
            Post.status == "queued",
            Post.is_deleted == False,
            Post.is_priority == True,
        ).count()

        estimated = _estimate_send_time(db, priority_count - len(created_ids) + 1)

        try:
            first_msg = messages[0]
            await bot.send_message(
                chat_id=settings.TELEGRAM_INGEST_CHAT_ID,
                text=(
                    f"✅ Álbum de {len(created_ids)} mídias adicionado à fila prioritária\n"
                    f"📍 Posição: #{priority_count - len(created_ids) + 1}\n"
                    f"🕐 Envio estimado: {estimated}\n"
                    f"📎 Serão enviadas juntas como álbum"
                ),
                reply_to_message_id=first_msg.message_id,
            )
        except TelegramError as exc:
            logger.warning("Could not send group confirmation: %s", exc)

        from app.scheduler import _broadcast_sse
        _broadcast_sse({"event": "priority_added", "post_id": created_ids[0], "group_size": len(created_ids)})

        logger.info(
            "Ingest group '%s': %d files saved as posts %s",
            group_id, len(created_ids), created_ids,
        )

    except Exception as exc:
        logger.exception("Ingest group handler error: %s", exc)
    finally:
        db.close()
