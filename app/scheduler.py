from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from app.config import settings
from app.database import AppState, Post, ScheduleLog, SessionLocal
from app.e621_client import e621_client
from app.reaction_monitor import _check_reactions
from app.telegram_sender import telegram_sender

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()
_sse_subscribers: list[asyncio.Queue] = []

_STATIC_EXTS = frozenset({"jpg", "jpeg", "png"})
_GIF_EXTS = frozenset({"gif"})
_VIDEO_EXTS = frozenset({"webm", "mp4"})


def get_scheduler() -> AsyncIOScheduler:
    return _scheduler


def subscribe_sse() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_subscribers.append(q)
    return q


def unsubscribe_sse(q: asyncio.Queue) -> None:
    try:
        _sse_subscribers.remove(q)
    except ValueError:
        pass


def _broadcast_sse(event: dict) -> None:
    dead = []
    for q in _sse_subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        unsubscribe_sse(q)


def _set_state(db: Session, key: str, value: str) -> None:
    row = db.query(AppState).filter(AppState.key == key).first()
    if row:
        row.value = value
    else:
        db.add(AppState(key=key, value=value))
    db.commit()


def _get_state(db: Session, key: str) -> str | None:
    row = db.query(AppState).filter(AppState.key == key).first()
    return row.value if row else None


def _next_interval() -> int:
    return random.randint(settings.MIN_INTERVAL_SECONDS, settings.MAX_INTERVAL_SECONDS)


def analyze_queue_composition(db: Session) -> dict:
    """Analisa a composição de tipos de mídia na fila atual."""
    posts = (
        db.query(Post.file_ext)
        .filter(Post.status == "queued", Post.is_deleted == False)
        .all()
    )
    total = len(posts)
    if total == 0:
        return {
            "total": 0,
            "images": 0, "gifs": 0, "videos": 0,
            "image_ratio": 0.0, "gif_ratio": 0.0, "video_ratio": 0.0,
            "animated_ratio": 0.0, "balance_ok": True, "mode": "normal",
        }

    images = sum(1 for (ext,) in posts if ext in _STATIC_EXTS)
    gifs = sum(1 for (ext,) in posts if ext in _GIF_EXTS)
    videos = sum(1 for (ext,) in posts if ext in _VIDEO_EXTS)
    animated = gifs + videos

    image_ratio = images / total
    gif_ratio = gifs / total
    video_ratio = videos / total
    animated_ratio = animated / total

    balance_ok = (
        total < settings.BALANCE_MIN_QUEUE_SIZE
        or image_ratio <= settings.BALANCE_IMAGE_THRESHOLD
    )

    return {
        "total": total,
        "images": images,
        "gifs": gifs,
        "videos": videos,
        "image_ratio": round(image_ratio, 3),
        "gif_ratio": round(gif_ratio, 3),
        "video_ratio": round(video_ratio, 3),
        "animated_ratio": round(animated_ratio, 3),
        "balance_ok": balance_ok,
        "mode": "normal" if balance_ok else "animated_boost",
    }


async def _insert_posts(db: Session, posts_data: list[dict]) -> int:
    """Insere posts únicos no banco, retorna quantidade adicionada."""
    existing_ids = {
        row[0]
        for row in db.query(Post.e621_id).filter(
            Post.status.in_(["queued", "sent"])
        ).all()
    }
    count = 0
    for raw in posts_data:
        normalized = e621_client.normalize(raw)
        if normalized["e621_id"] in existing_ids:
            continue
        db.add(Post(**normalized))
        count += 1
    if count:
        db.commit()
    return count


async def _refill_queue(db: Session) -> int:
    """Reabastece a fila com inteligência de balanceamento de tipos de mídia."""
    composition = analyze_queue_composition(db)

    if not composition["balance_ok"]:
        img_pct = composition["image_ratio"] * 100
        logger.info(
            "Queue is image-heavy (%.0f%% static). Triggering animated boost...",
            img_pct,
        )
        _broadcast_sse({"event": "balance_boost", "image_ratio": composition["image_ratio"]})
        return await _refill_animated(db)
    else:
        return await _refill_normal(db)


async def _refill_normal(db: Session) -> int:
    logger.info("Refilling queue from e621 (normal mode)...")
    try:
        posts_data = await e621_client.fetch_random_posts(limit=settings.E621_LIMIT)
    except Exception as exc:
        logger.error("Failed to fetch from e621: %s", exc)
        return 0
    count = await _insert_posts(db, posts_data)
    logger.info("Normal refill: added %d posts", count)
    return count


async def _refill_animated(db: Session) -> int:
    """Busca GIFs e vídeos para corrigir o excesso de imagens estáticas."""
    total_added = 0

    # Busca GIFs
    try:
        gif_posts = await e621_client.fetch_by_type("gif", limit=50)
        added = await _insert_posts(db, gif_posts)
        logger.info("Animated boost (GIF): added %d posts", added)
        total_added += added
    except Exception as exc:
        logger.warning("GIF fetch failed: %s", exc)

    # Busca vídeos WebM (pausa de 1s entre requests)
    await asyncio.sleep(1.2)
    try:
        vid_posts = await e621_client.fetch_by_type("webm", limit=50)
        added = await _insert_posts(db, vid_posts)
        logger.info("Animated boost (WebM): added %d posts", added)
        total_added += added
    except Exception as exc:
        logger.warning("WebM fetch failed: %s", exc)

    if total_added == 0:
        logger.warning("Animated boost yielded 0 posts — falling back to normal refill")
        return await _refill_normal(db)

    return total_added


async def _run_send_job() -> None:
    db: Session = SessionLocal()
    try:
        next_post = (
            db.query(Post)
            .filter(Post.status == "queued", Post.is_deleted == False)
            .order_by(Post.queued_at.asc())
            .first()
        )

        if next_post is None:
            added = await _refill_queue(db)
            if added == 0:
                logger.warning("No posts available even after refill. Retrying in 5 minutes.")
                _schedule_next(300)
                return
            next_post = (
                db.query(Post)
                .filter(Post.status == "queued", Post.is_deleted == False)
                .order_by(Post.queued_at.asc())
                .first()
            )

        if next_post is None:
            logger.error("Still no posts after refill. Retrying in 5 minutes.")
            _schedule_next(300)
            return

        # Analisa fila ANTES de enviar — reabastece em background se necessário
        composition = analyze_queue_composition(db)
        remaining_after_send = composition["total"] - 1
        if remaining_after_send <= 5:
            logger.info("Queue running low (%d left). Pre-fetching...", remaining_after_send)
            asyncio.create_task(_background_refill())

        success, message_id = await telegram_sender.send_media(next_post)
        now = datetime.now(timezone.utc)
        if success:
            next_post.status = "sent"
            next_post.sent_at = now
            if message_id:
                next_post.message_id = message_id
        else:
            next_post.status = "failed"
            next_post.is_deleted = True

        interval = _next_interval()
        next_run = now + timedelta(seconds=interval)

        log = ScheduleLog(
            triggered_at=now,
            next_run_at=next_run,
            post_id=next_post.id,
            success=success,
        )
        db.add(log)
        _set_state(db, "next_run_at", next_run.isoformat())
        _set_state(db, "last_post_id", str(next_post.id) if success else "")
        db.commit()

        _broadcast_sse(
            {
                "event": "post_sent",
                "post_id": next_post.id,
                "e621_id": next_post.e621_id,
                "file_ext": next_post.file_ext,
                "success": success,
                "next_run_at": next_run.isoformat(),
                "interval_seconds": interval,
                "queue_composition": composition,
            }
        )

        _schedule_next(interval)
        logger.info(
            "Job done. Next send in %ds (at %s) | Queue: %d total, %.0f%% static, %.0f%% animated",
            interval,
            next_run.strftime("%Y-%m-%d %H:%M:%S UTC"),
            composition["total"],
            composition["image_ratio"] * 100,
            composition["animated_ratio"] * 100,
        )
    except Exception as exc:
        logger.exception("Unhandled error in send job: %s", exc)
        _schedule_next(600)
    finally:
        db.close()


async def _background_refill() -> None:
    """Reabastece a fila em background sem bloquear o job principal."""
    db: Session = SessionLocal()
    try:
        await _refill_queue(db)
    except Exception as exc:
        logger.error("Background refill error: %s", exc)
    finally:
        db.close()


def _schedule_next(seconds: int) -> None:
    run_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    _scheduler.add_job(
        _run_send_job,
        "date",
        run_date=run_at,
        id="send_next_post",
        replace_existing=True,
        misfire_grace_time=300,
    )


def start_scheduler() -> None:
    db: Session = SessionLocal()
    try:
        next_run_str = _get_state(db, "next_run_at")
        now = datetime.now(timezone.utc)
        if next_run_str:
            try:
                next_run = datetime.fromisoformat(next_run_str)
                if next_run.tzinfo is None:
                    next_run = next_run.replace(tzinfo=timezone.utc)
                delay = (next_run - now).total_seconds()
                if delay <= 0:
                    delay = 10
            except ValueError:
                delay = 10
        else:
            delay = 10

        logger.info("Scheduler starting — first job in %.0f seconds", delay)
        _schedule_next(int(delay))

        # Verificador de reações a cada 5 minutos
        _scheduler.add_job(
            _check_reactions,
            "interval",
            minutes=5,
            id="reaction_monitor",
            replace_existing=True,
            max_instances=1,
        )

        _scheduler.start()
    finally:
        db.close()


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
