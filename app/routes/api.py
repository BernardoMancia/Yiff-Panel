from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import AppState, Post, ScheduleLog, get_db
from app.scheduler import analyze_queue_composition, subscribe_sse, unsubscribe_sse, get_ordered_queue

router = APIRouter()
logger = logging.getLogger(__name__)


def _require_admin(request: Request, db: Session = Depends(get_db)):
    from app.auth import get_user_from_token
    token = request.headers.get("X-Admin-Token") or request.cookies.get("admin_token")
    user = get_user_from_token(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Admin access required")
    return user


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
        "is_priority": getattr(p, "is_priority", False),
        "source": getattr(p, "source", "e621"),
        "media_group_id": getattr(p, "media_group_id", None),
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
    try:
        posts = get_ordered_queue(db, limit=limit)
        return [_serialize_post(p) for p in posts]
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).error("get_ordered_queue failed, falling back to FIFO: %s", exc)
        posts = (
            db.query(Post)
            .filter(Post.status == "queued", Post.is_deleted == False)
            .order_by(Post.queued_at.asc())
            .limit(limit)
            .all()
        )
        return [_serialize_post(p) for p in posts]


@router.get("/post/{post_id}")
def get_post_by_id(post_id: int, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return _serialize_post(post)


@router.get("/next")
def get_next(db: Session = Depends(get_db)):
    next_post = None

    # Tenta usar o ID pré-selecionado pelo ciclo
    cached_id_row = db.query(AppState).filter(AppState.key == "next_post_id").first()
    if cached_id_row and cached_id_row.value:
        try:
            cached_id = int(cached_id_row.value)
            next_post = db.query(Post).filter(
                Post.id == cached_id,
                Post.status == "queued",
                Post.is_deleted == False,
            ).first()
        except (ValueError, Exception):
            pass

    # Fallback: primeiro da fila se cache inválido
    if next_post is None:
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

    from app.config import settings as _s
    return {
        "next_post": _serialize_post(next_post) if next_post else None,
        "next_run_at": next_run_at,
        "seconds_remaining": seconds_remaining,
        "interval_min": _s.MIN_INTERVAL_SECONDS,
        "interval_max": _s.MAX_INTERVAL_SECONDS,
    }


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    from datetime import datetime, timezone
    from app.database import SentRegistry
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    monthly = db.query(SentRegistry).filter(SentRegistry.sent_at >= month_start).all()
    monthly_images = sum(1 for r in monthly if r.file_ext in ("jpg", "jpeg", "png"))
    monthly_videos = sum(1 for r in monthly if r.file_ext in ("webm", "mp4"))
    monthly_gifs = sum(1 for r in monthly if r.file_ext == "gif")
    monthly_total = len(monthly)

    total_ever = db.query(SentRegistry).count()
    total_queued = db.query(Post).filter(Post.status == "queued", Post.is_deleted == False).count()
    total_failed = db.query(Post).filter(Post.status == "failed").count()
    last_log = db.query(ScheduleLog).order_by(ScheduleLog.id.desc()).first()
    composition = analyze_queue_composition(db)
    return {
        "total_sent": total_ever,
        "total_queued": total_queued,
        "total_failed": total_failed,
        "monthly": {
            "month": now.strftime("%B %Y"),
            "total": monthly_total,
            "images": monthly_images,
            "videos": monthly_videos,
            "gifs": monthly_gifs,
        },
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
async def trigger_now(request: Request, db: Session = Depends(get_db), _=Depends(_require_admin)):
    from app.scheduler import _schedule_next
    _schedule_next(3)
    return {"status": "triggered", "message": "Job will run in ~3 seconds"}


@router.get("/config")
def get_config(request: Request, db: Session = Depends(get_db), _=Depends(_require_admin)):
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
def update_tags(body: dict, request: Request, db: Session = Depends(get_db), _=Depends(_require_admin)):
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


_refill_running = False
_refill_result: dict | None = None


async def _do_refill_after_reset() -> None:
    global _refill_running, _refill_result
    _refill_running = True
    _refill_result = None
    db = None
    try:
        from app.database import SessionLocal
        from app.scheduler import _refill_queue
        db = SessionLocal()
        added = await _refill_queue(db)
        _refill_result = {"ok": True, "added": added}
        logger.info("Post-reset refill complete: %d posts added", added)
    except Exception as exc:
        _refill_result = {"ok": False, "error": str(exc)}
        logger.error("Post-reset refill failed: %s", exc)
    finally:
        _refill_running = False
        if db:
            db.close()


@router.post("/admin/reset-queue")
async def reset_queue(
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(_require_admin),
):
    global _refill_running, _refill_result
    deleted = (
        db.query(Post)
        .filter(Post.status == "queued", Post.is_deleted == False)
        .count()
    )
    db.query(Post).filter(Post.status == "queued", Post.is_deleted == False).update(
        {"is_deleted": True, "status": "reset"},
        synchronize_session=False,
    )
    db.commit()
    logger.warning("Admin reset queue: %d posts marked as reset.", deleted)
    _refill_running = True
    _refill_result = None
    background_tasks.add_task(_do_refill_after_reset)
    return {"ok": True, "removed_from_queue": deleted, "refilling": True}


@router.get("/admin/refill-status")
def refill_status(request: Request, db: Session = Depends(get_db), _=Depends(_require_admin)):
    return {
        "running": _refill_running,
        "result": _refill_result,
        "queue_count": db.query(Post).filter(Post.status == "queued", Post.is_deleted == False).count(),
    }


@router.post("/suggestions")
def submit_suggestion(body: dict, db: Session = Depends(get_db)):
    from app.database import TagSuggestion
    tag = (body.get("tag") or "").strip().lower().lstrip("~-")
    if not tag or len(tag) < 2:
        return {"ok": False, "error": "Tag inválida (mín. 2 caracteres)"}
    if len(tag) > 80:
        return {"ok": False, "error": "Tag muito longa"}
    existing = db.query(TagSuggestion).filter(
        TagSuggestion.tag == tag,
        TagSuggestion.status == "pending",
    ).first()
    if existing:
        return {"ok": False, "error": "Essa tag já foi sugerida e está aguardando revisão"}
    db.add(TagSuggestion(tag=tag))
    db.commit()
    return {"ok": True}


@router.get("/suggestions")
def list_suggestions(request: Request, db: Session = Depends(get_db), _=Depends(_require_admin)):
    from app.database import TagSuggestion
    rows = db.query(TagSuggestion).filter(TagSuggestion.status == "pending").order_by(TagSuggestion.id.desc()).all()
    return [{"id": r.id, "tag": r.tag, "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


@router.post("/suggestions/{suggestion_id}/accept")
def accept_suggestion(suggestion_id: int, request: Request, db: Session = Depends(get_db), _=Depends(_require_admin)):
    from app.database import TagSuggestion
    from app.tag_manager import add_tag
    from datetime import datetime, timezone
    row = db.query(TagSuggestion).filter(TagSuggestion.id == suggestion_id).first()
    if not row:
        return {"ok": False, "error": "Sugestão não encontrada"}
    add_tag(db, "or", row.tag)
    row.status = "accepted"
    row.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "tag": row.tag}


@router.post("/suggestions/{suggestion_id}/reject")
def reject_suggestion(suggestion_id: int, request: Request, db: Session = Depends(get_db), _=Depends(_require_admin)):
    from app.database import TagSuggestion
    from datetime import datetime, timezone
    row = db.query(TagSuggestion).filter(TagSuggestion.id == suggestion_id).first()
    if not row:
        return {"ok": False, "error": "Sugestão não encontrada"}
    row.status = "rejected"
    row.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}
