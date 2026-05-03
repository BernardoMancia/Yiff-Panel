from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from app.config import settings
from app.database import AppState, Post, ScheduleLog, SentRegistry, SessionLocal
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
        or (
            image_ratio <= 0.70
            and video_ratio >= 0.15
            and gif_ratio >= 0.05
        )
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
    from datetime import datetime, timezone
    sent_ids = {
        row[0] for row in db.query(SentRegistry.e621_id).all()
    }
    existing = {
        row[0]: row[1]
        for row in db.query(Post.e621_id, Post.status).all()
    }
    count = 0
    reactivated = 0
    now = datetime.now(timezone.utc)
    for raw in posts_data:
        normalized = e621_client.normalize(raw)
        eid = normalized["e621_id"]
        if eid in sent_ids:
            continue
        if eid in existing:
            status = existing[eid]
            if status in ("queued", "sent"):
                continue
            db.query(Post).filter(Post.e621_id == eid).update(
                {"status": "queued", "is_deleted": False, "queued_at": now},
                synchronize_session=False,
            )
            reactivated += 1
            count += 1
        else:
            db.add(Post(**normalized))
            count += 1
    if count:
        db.commit()
    if reactivated:
        logger.info("Reactivated %d soft-deleted posts during refill", reactivated)
    return count


async def _refill_queue(db: Session) -> int:
    """Reabastece a fila com proporções-alvo: 60% imagens, 30% vídeos, 10% GIFs."""
    from app.tag_manager import build_query, get_mandatory_tags, get_blacklist_tags
    from datetime import datetime, timezone
    custom_tags = build_query(db)
    comp = analyze_queue_composition(db)
    logger.info(
        "Queue composition: %d total | images=%.0f%% videos=%.0f%% gifs=%.0f%%",
        comp["total"], comp["image_ratio"] * 100, comp["video_ratio"] * 100, comp["gif_ratio"] * 100,
    )

    _T_IMG = 0.60
    _T_VID = 0.30
    _T_GIF = 0.10
    _BATCH = 100

    target_img = int(_BATCH * _T_IMG)
    target_vid = int(_BATCH * _T_VID)
    target_gif = _BATCH - target_img - target_vid

    # Remover excesso de imagens para abrir espaço ao rebalancear
    if comp["total"] >= 15 and comp["images"] > target_img:
        excess = comp["images"] - target_img
        excess_posts = (
            db.query(Post)
            .filter(Post.status == "queued", Post.is_deleted == False, Post.file_ext.in_(list(_STATIC_EXTS)))
            .order_by(Post.queued_at.asc())
            .limit(excess)
            .all()
        )
        for p in excess_posts:
            p.status = "reset"
            p.is_deleted = True
        if excess_posts:
            db.commit()
            logger.info("Removed %d excess image posts for rebalancing", len(excess_posts))
            comp = analyze_queue_composition(db)

    need_img = max(0, target_img - comp["images"])
    need_vid = max(0, target_vid - comp["videos"])
    need_gif = max(0, target_gif - comp["gifs"])

    logger.info("Targets — images: need %d, videos: need %d, gifs: need %d", need_img, need_vid, need_gif)
    total_added = 0

    # Query simplificada para vídeo/GIF: só OR+blacklist, sem AND restritivos
    mandatory = get_mandatory_tags(db)
    blacklist = get_blacklist_tags(db)
    simple_tags = " ".join(
        [f"~{t}" for t in mandatory]
        + [f"-{t}" for t in blacklist]
        + ["order:random", "rating:e"]
    )

    # ── Imagens ──
    if need_img > 0:
        raw_posts = await _fetch_with_retry(custom_tags, limit=_BATCH)
        static_only = [p for p in raw_posts if p.get("file", {}).get("ext", "").lower() in _STATIC_EXTS]
        added = await _insert_posts(db, static_only[:need_img])
        logger.info("Images: inserted %d", added)
        total_added += added

    # ── Vídeos (WebM) ──
    if need_vid > 0:
        await asyncio.sleep(1.2)
        vid_posts = await _fetch_by_type_retry("webm", simple_tags, limit=max(need_vid * 3, 60))
        added = await _insert_posts(db, vid_posts[:need_vid])
        logger.info("Videos: inserted %d", added)
        total_added += added

    # ── GIFs ──
    if need_gif > 0:
        await asyncio.sleep(1.2)
        gif_posts = await _fetch_by_type_retry("gif", simple_tags, limit=max(need_gif * 3, 30))
        added = await _insert_posts(db, gif_posts[:need_gif])
        logger.info("GIFs: inserted %d", added)
        total_added += added

    if total_added == 0:
        logger.warning("Balanced refill inserted 0 posts — trying unrestricted image fetch")
        raw_posts = await _fetch_with_retry(custom_tags, limit=_BATCH)
        total_added = await _insert_posts(db, raw_posts)

    logger.info("Balanced refill complete: %d posts added", total_added)
    _broadcast_sse({"event": "refill_done", "added": total_added})
    return total_added


async def _fetch_with_retry(custom_tags: str, limit: int = 100) -> list[dict]:
    for attempt, page in enumerate(random.sample(range(1, 6), 5), start=1):
        try:
            posts = await e621_client.fetch_posts(page=page, limit=limit, custom_tags=custom_tags)
            logger.info("fetch_with_retry attempt %d (page %d): %d posts", attempt, page, len(posts))
            if posts:
                return posts
            if attempt < 4:
                await asyncio.sleep(1.2)
        except Exception as exc:
            logger.error("fetch_with_retry attempt %d failed: %s", attempt, exc)
            if attempt < 4:
                await asyncio.sleep(2)
    return []


async def _fetch_by_type_retry(file_type: str, custom_tags: str, limit: int = 50) -> list[dict]:
    for attempt in range(1, 4):
        try:
            posts = await e621_client.fetch_by_type(file_type, limit=limit, custom_tags=custom_tags)
            logger.info("fetch_by_type(%s) attempt %d: %d posts", file_type, attempt, len(posts))
            if posts:
                return posts
            if attempt < 3:
                await asyncio.sleep(1.2)
        except Exception as exc:
            logger.error("fetch_by_type(%s) attempt %d failed: %s", file_type, attempt, exc)
            if attempt < 3:
                await asyncio.sleep(2)
    return []


_SEND_CYCLE_KEY = "send_cycle"
_SEND_CYCLE_IDX_KEY = "send_cycle_idx"

_BASE_CYCLE = ["image"] * 6 + ["video"] * 3 + ["gif"] * 1


def _get_or_create_cycle(db: Session) -> tuple[list[str], int]:
    cycle_row = db.query(AppState).filter(AppState.key == _SEND_CYCLE_KEY).first()
    idx_row = db.query(AppState).filter(AppState.key == _SEND_CYCLE_IDX_KEY).first()

    idx = int(idx_row.value) if idx_row and idx_row.value else 0
    if cycle_row and cycle_row.value:
        try:
            import json as _json
            cycle = _json.loads(cycle_row.value)
            if len(cycle) == 10:
                return cycle, idx
        except Exception:
            pass

    cycle = _BASE_CYCLE[:]
    random.shuffle(cycle)
    serialized = str(cycle).replace("'", '"')
    if cycle_row:
        cycle_row.value = serialized
    else:
        db.add(AppState(key=_SEND_CYCLE_KEY, value=serialized))
    if not idx_row:
        db.add(AppState(key=_SEND_CYCLE_IDX_KEY, value="0"))
    db.commit()
    return cycle, 0


def _advance_cycle(db: Session, cycle: list[str], idx: int) -> None:
    next_idx = (idx + 1) % len(cycle)
    idx_row = db.query(AppState).filter(AppState.key == _SEND_CYCLE_IDX_KEY).first()
    if next_idx == 0:
        new_cycle = _BASE_CYCLE[:]
        random.shuffle(new_cycle)
        import json as _json
        cycle_row = db.query(AppState).filter(AppState.key == _SEND_CYCLE_KEY).first()
        if cycle_row:
            cycle_row.value = str(new_cycle).replace("'", '"')
    if idx_row:
        idx_row.value = str(next_idx)
    else:
        db.add(AppState(key=_SEND_CYCLE_IDX_KEY, value=str(next_idx)))
    db.commit()


def _pick_next_post(db: Session) -> Post | None:
    candidates = (
        db.query(Post)
        .filter(Post.status == "queued", Post.is_deleted == False)
        .all()
    )
    if not candidates:
        return None

    images = [p for p in candidates if p.file_ext in _STATIC_EXTS]
    videos = [p for p in candidates if p.file_ext in _VIDEO_EXTS]
    gifs = [p for p in candidates if p.file_ext in _GIF_EXTS]

    cycle, idx = _get_or_create_cycle(db)
    slot_type = cycle[idx]

    if slot_type == "image" and images:
        return random.choice(images)
    if slot_type == "video" and videos:
        return random.choice(videos)
    if slot_type == "gif" and gifs:
        return random.choice(gifs)

    # Fallback se o tipo do slot não tem post disponível
    for pool in (images, videos, gifs):
        if pool:
            return random.choice(pool)
    return random.choice(candidates)


async def _run_send_job() -> None:
    db: Session = SessionLocal()
    try:
        next_post = _pick_next_post(db)

        if next_post is None:
            added = await _refill_queue(db)
            if added == 0:
                logger.warning("No posts available even after refill. Retrying in 5 minutes.")
                _schedule_next(300)
                return
            next_post = _pick_next_post(db)

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

        if success:
            try:
                existing_reg = db.query(SentRegistry).filter(SentRegistry.e621_id == next_post.e621_id).first()
                if not existing_reg:
                    db.add(SentRegistry(
                        e621_id=next_post.e621_id,
                        file_url=next_post.file_url,
                        file_ext=next_post.file_ext,
                        sent_at=now,
                    ))
                    db.commit()
            except Exception as reg_exc:
                logger.warning("Failed to register in SentRegistry: %s", reg_exc)
                db.rollback()

        if success:
            cycle, idx = _get_or_create_cycle(db)
            _advance_cycle(db, cycle, idx)

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
