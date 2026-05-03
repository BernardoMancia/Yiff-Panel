from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import AppState, Post, ScheduleLog, get_db
from app.scheduler import analyze_queue_composition, subscribe_sse, unsubscribe_sse

router = APIRouter()
logger = logging.getLogger(__name__)


def _serialize_post(p: Post) -> dict:
    try:
        tags_list = json.loads(p.tags) if p.tags else []
    except Exception:
        tags_list = []
    return {
        "id": p.id,
        "e621_id": p.e621_id,
        "file_url": p.file_url,
        "sample_url": p.sample_url,
        "preview_url": p.preview_url,
        "file_ext": p.file_ext,
        "file_size": p.file_size,
        "score": p.score,
        "fav_count": p.fav_count,
        "status": p.status,
        "tags": tags_list,
        "queued_at": p.queued_at.isoformat() if p.queued_at else None,
        "sent_at": p.sent_at.isoformat() if p.sent_at else None,
    }


@router.get("/history")
def get_history(limit: int = 20, db: Session = Depends(get_db)):
    posts = (
        db.query(Post)
        .filter(Post.status == "sent")
        .order_by(Post.sent_at.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_post(p) for p in posts]


@router.get("/queue")
def get_queue(limit: int = 10, db: Session = Depends(get_db)):
    posts = (
        db.query(Post)
        .filter(Post.status == "queued", Post.is_deleted == False)
        .order_by(Post.queued_at.asc())
        .limit(limit)
        .all()
    )
    return [_serialize_post(p) for p in posts]


@router.get("/next")
def get_next(db: Session = Depends(get_db)):
    next_post = (
        db.query(Post)
        .filter(Post.status == "queued", Post.is_deleted == False)
        .order_by(Post.queued_at.asc())
        .first()
    )
    state = db.query(AppState).filter(AppState.key == "next_run_at").first()
    next_run_at = state.value if state else None

    seconds_remaining = None
    if next_run_at:
        try:
            target = datetime.fromisoformat(next_run_at)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            diff = (target - datetime.now(timezone.utc)).total_seconds()
            seconds_remaining = max(0, int(diff))
        except ValueError:
            pass

    return {
        "next_post": _serialize_post(next_post) if next_post else None,
        "next_run_at": next_run_at,
        "seconds_remaining": seconds_remaining,
    }


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    total_sent = db.query(Post).filter(Post.status == "sent").count()
    total_queued = db.query(Post).filter(Post.status == "queued", Post.is_deleted == False).count()
    total_failed = db.query(Post).filter(Post.status == "failed").count()
    last_log = db.query(ScheduleLog).order_by(ScheduleLog.id.desc()).first()
    composition = analyze_queue_composition(db)
    return {
        "total_sent": total_sent,
        "total_queued": total_queued,
        "total_failed": total_failed,
        "last_triggered_at": last_log.triggered_at.isoformat() if last_log and last_log.triggered_at else None,
        "queue_composition": composition,
    }


@router.get("/composition")
def get_composition(db: Session = Depends(get_db)):
    return analyze_queue_composition(db)


@router.get("/stream")
async def sse_stream():
    queue = subscribe_sse()

    async def event_generator():
        try:
            yield "data: {\"event\": \"connected\"}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            unsubscribe_sse(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/trigger")
async def trigger_now(db: Session = Depends(get_db)):
    from app.scheduler import _schedule_next
    _schedule_next(3)
    return {"status": "triggered", "message": "Job will run in ~3 seconds"}


@router.get("/config")
def get_config(db: Session = Depends(get_db)):
    from app.config import settings
    from app.tag_manager import get_mandatory_tags, get_required_tags, get_or_tags, get_blacklist_tags
    return {
        "mandatory_tags": get_mandatory_tags(db),
        "required_tags": get_required_tags(db),
        "or_tags": get_or_tags(db),
        "blacklist": get_blacklist_tags(db),
        "interval": f"{settings.MIN_INTERVAL_SECONDS // 60}min – {settings.MAX_INTERVAL_SECONDS // 60}min",
        "balance_threshold": f"{int(settings.BALANCE_IMAGE_THRESHOLD * 100)}%",
    }


@router.post("/config/tags")
def update_tags(body: dict, db: Session = Depends(get_db)):
    from app.tag_manager import add_tag, remove_tag
    action = body.get("action")
    tag_type = body.get("type")
    tag = (body.get("tag") or "").strip().lower().lstrip("~-")
    if not tag:
        return {"ok": False, "error": "tag vazia"}
    if action not in ("add", "remove"):
        return {"ok": False, "error": "action inválida"}
    if tag_type not in ("mandatory", "required", "or", "blacklist"):
        return {"ok": False, "error": "type inválido"}
    try:
        if action == "add":
            add_tag(db, tag_type, tag)
        else:
            remove_tag(db, tag_type, tag)
        return {"ok": True}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
