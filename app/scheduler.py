from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Post, ScheduleLog, SentRegistry, SessionLocal
from app.e621_client import e621_client
from app.queue_manager import (
    STATIC_EXTS,
    GIF_EXTS,
    VIDEO_EXTS,
    analyze_queue_composition,
    compute_queue_order,
    persist_queue_order,
    remove_from_queue_order,
)
from app.send_cycle import (
    advance_cycle,
    cache_next_post_id,
    get_cached_next_post,
    get_or_create_cycle,
    pick_next_post,
)
from app.state_store import broadcast_sse, get_state, set_state
from app.telegram_sender import telegram_sender

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()


def get_scheduler() -> AsyncIOScheduler:
    return _scheduler


def _next_interval() -> int:
    return random.randint(settings.MIN_INTERVAL_SECONDS, settings.MAX_INTERVAL_SECONDS)


async def _insert_posts(db: Session, posts_data: list[dict]) -> int:
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
    from app.tag_manager import build_query, get_mandatory_tags, get_blacklist_tags

    custom_tags = build_query(db)
    comp = analyze_queue_composition(db)
    logger.info(
        "Queue composition: %d total | images=%.0f%% videos=%.0f%% gifs=%.0f%%",
        comp["total"], comp["image_ratio"] * 100, comp["video_ratio"] * 100, comp["gif_ratio"] * 100,
    )

    _T_IMG = 0.20
    _T_VID = 0.50
    _T_GIF = 0.30
    _BATCH = 100

    target_img = int(_BATCH * _T_IMG)
    target_vid = int(_BATCH * _T_VID)
    target_gif = _BATCH - target_img - target_vid

    if comp["total"] >= 15 and comp["images"] > target_img:
        excess = comp["images"] - target_img
        excess_posts = (
            db.query(Post)
            .filter(Post.status == "queued", Post.is_deleted == False, Post.file_ext.in_(list(STATIC_EXTS)))
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

    mandatory = get_mandatory_tags(db)
    blacklist = get_blacklist_tags(db)
    simple_tags = " ".join(
        [f"~{t}" for t in mandatory]
        + [f"-{t}" for t in blacklist]
        + ["order:random", "rating:e"]
    )

    if need_img > 0:
        raw_posts = await _fetch_with_retry(custom_tags, limit=_BATCH)
        static_only = [p for p in raw_posts if p.get("file", {}).get("ext", "").lower() in STATIC_EXTS]
        added = await _insert_posts(db, static_only[:need_img])
        logger.info("Images: inserted %d", added)
        total_added += added

    if need_vid > 0:
        await asyncio.sleep(1.2)
        vid_posts = await _fetch_by_type_retry("webm", simple_tags, limit=max(need_vid * 3, 60))
        added = await _insert_posts(db, vid_posts[:need_vid])
        logger.info("Videos: inserted %d", added)
        total_added += added

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
    if total_added > 0:
        ordered = compute_queue_order(db)
        persist_queue_order(db, ordered)
    broadcast_sse({"event": "refill_done", "added": total_added})
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


async def _run_send_job() -> None:
    db: Session = SessionLocal()
    try:
        next_post = get_cached_next_post(db)

        if next_post is None:
            next_post = pick_next_post(db)

        if next_post is None:
            added = await _refill_queue(db)
            if added == 0:
                logger.warning("No posts available even after refill. Retrying in 5 minutes.")
                _schedule_next(300)
                return
            next_post = pick_next_post(db)

        if next_post is None:
            logger.error("Still no posts after refill. Retrying in 5 minutes.")
            _schedule_next(300)
            return

        composition = analyze_queue_composition(db)
        remaining_after_send = composition["total"] - 1
        if remaining_after_send <= 5:
            logger.info("Queue running low (%d left). Pre-fetching...", remaining_after_send)
            asyncio.create_task(_background_refill())

        is_group = bool(getattr(next_post, "media_group_id", None))
        is_ingest = getattr(next_post, "source", "e621") == "ingest"

        if is_group:
            group_posts = (
                db.query(Post)
                .filter(
                    Post.media_group_id == next_post.media_group_id,
                    Post.status == "queued",
                    Post.is_deleted == False,
                )
                .order_by(Post.id.asc())
                .all()
            )
            success, message_ids = await telegram_sender.send_media_group_posts(group_posts)
            now = datetime.now(timezone.utc)
            for i, p in enumerate(group_posts):
                if success:
                    p.status = "sent"
                    p.sent_at = now
                    if i < len(message_ids):
                        p.message_id = message_ids[i]
                else:
                    p.status = "failed"
                    p.is_deleted = True

            message_id = message_ids[0] if message_ids else None
            sent_post = next_post
        else:
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
            sent_post = next_post

        interval = _next_interval()
        next_run = now + timedelta(seconds=interval)

        log = ScheduleLog(
            triggered_at=now,
            next_run_at=next_run,
            post_id=sent_post.id,
            success=success,
        )
        db.add(log)
        set_state(db, "next_run_at", next_run.isoformat())
        set_state(db, "last_post_id", str(sent_post.id) if success else "")
        db.commit()

        if success and not is_ingest:
            try:
                existing_reg = db.query(SentRegistry).filter(SentRegistry.e621_id == sent_post.e621_id).first()
                if not existing_reg:
                    db.add(SentRegistry(
                        e621_id=sent_post.e621_id,
                        file_url=sent_post.file_url,
                        file_ext=sent_post.file_ext,
                        sent_at=now,
                    ))
                    db.commit()
            except Exception as reg_exc:
                logger.warning("Failed to register in SentRegistry: %s", reg_exc)
                db.rollback()

        if success:
            if not is_ingest:
                cycle, idx = get_or_create_cycle(db)
                advance_cycle(db, cycle, idx)

            if is_group:
                for p in group_posts:
                    remove_from_queue_order(db, p.id)
            else:
                remove_from_queue_order(db, sent_post.id)

            cache_next_post_id(db)

        broadcast_sse(
            {
                "event": "post_sent",
                "post_id": sent_post.id,
                "e621_id": sent_post.e621_id,
                "file_ext": sent_post.file_ext,
                "success": success,
                "next_run_at": next_run.isoformat(),
                "interval_seconds": interval,
                "queue_composition": composition,
                "is_group": is_group,
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
        next_run_str = get_state(db, "next_run_at")
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

        from app.unified_poller import poll_telegram_updates
        _scheduler.add_job(
            poll_telegram_updates,
            "interval",
            seconds=5,
            id="unified_poller",
            replace_existing=True,
            max_instances=1,
        )

        _scheduler.start()
    finally:
        db.close()


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
