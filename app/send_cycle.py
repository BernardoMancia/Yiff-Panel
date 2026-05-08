from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.database import AppState, Post

logger = logging.getLogger(__name__)

STATIC_EXTS = frozenset({"jpg", "jpeg", "png"})
GIF_EXTS = frozenset({"gif"})
VIDEO_EXTS = frozenset({"webm", "mp4"})

_SEND_CYCLE_KEY = "send_cycle"
_SEND_CYCLE_IDX_KEY = "send_cycle_idx"
_BASE_CYCLE = ["image"] * 2 + ["video"] * 5 + ["gif"] * 3


def get_or_create_cycle(db: Session) -> tuple[list[str], int]:
    cycle_row = db.query(AppState).filter(AppState.key == _SEND_CYCLE_KEY).first()
    idx_row = db.query(AppState).filter(AppState.key == _SEND_CYCLE_IDX_KEY).first()

    idx = int(idx_row.value) if idx_row and idx_row.value else 0
    if cycle_row and cycle_row.value:
        try:
            cycle = json.loads(cycle_row.value)
            if len(cycle) == 10:
                return cycle, idx
        except Exception:
            pass

    cycle = _BASE_CYCLE[:]
    random.shuffle(cycle)
    serialized = json.dumps(cycle)
    if cycle_row:
        cycle_row.value = serialized
    else:
        db.add(AppState(key=_SEND_CYCLE_KEY, value=serialized))
    if not idx_row:
        db.add(AppState(key=_SEND_CYCLE_IDX_KEY, value="0"))
    db.commit()
    return cycle, 0


def advance_cycle(db: Session, cycle: list[str], idx: int) -> None:
    next_idx = (idx + 1) % len(cycle)
    idx_row = db.query(AppState).filter(AppState.key == _SEND_CYCLE_IDX_KEY).first()
    if next_idx == 0:
        new_cycle = _BASE_CYCLE[:]
        random.shuffle(new_cycle)
        cycle_row = db.query(AppState).filter(AppState.key == _SEND_CYCLE_KEY).first()
        if cycle_row:
            cycle_row.value = json.dumps(new_cycle)
    if idx_row:
        idx_row.value = str(next_idx)
    else:
        db.add(AppState(key=_SEND_CYCLE_IDX_KEY, value=str(next_idx)))
    db.commit()


def get_cached_next_post(db: Session) -> Post | None:
    row = db.query(AppState).filter(AppState.key == "next_post_id").first()
    if not row or not row.value:
        return None
    try:
        post_id = int(row.value)
        return db.query(Post).filter(
            Post.id == post_id,
            Post.status == "queued",
            Post.is_deleted == False,
        ).first()
    except (ValueError, Exception):
        return None


def cache_next_post_id(db: Session) -> None:
    post = preview_next_post(db)
    val = str(post.id) if post else ""
    row = db.query(AppState).filter(AppState.key == "next_post_id").first()
    if row:
        row.value = val
    else:
        db.add(AppState(key="next_post_id", value=val))
    db.commit()


def preview_next_post(db: Session) -> Post | None:
    candidates = (
        db.query(Post)
        .filter(Post.status == "queued", Post.is_deleted == False)
        .all()
    )
    if not candidates:
        return None
    images = [p for p in candidates if p.file_ext in STATIC_EXTS]
    videos = [p for p in candidates if p.file_ext in VIDEO_EXTS]
    gifs = [p for p in candidates if p.file_ext in GIF_EXTS]
    cycle, idx = get_or_create_cycle(db)
    slot_type = cycle[idx]
    if slot_type == "image" and images:
        return random.choice(images)
    if slot_type == "video" and videos:
        return random.choice(videos)
    if slot_type == "gif" and gifs:
        return random.choice(gifs)
    for pool in (images, videos, gifs):
        if pool:
            return random.choice(pool)
    return random.choice(candidates)


def pick_next_post(db: Session) -> Post | None:
    priority = (
        db.query(Post)
        .filter(
            Post.status == "queued",
            Post.is_deleted == False,
            Post.is_priority == True,
        )
        .order_by(Post.queued_at.asc())
        .first()
    )
    if priority:
        return priority

    candidates = (
        db.query(Post)
        .filter(Post.status == "queued", Post.is_deleted == False)
        .all()
    )
    if not candidates:
        return None

    images = [p for p in candidates if p.file_ext in STATIC_EXTS]
    videos = [p for p in candidates if p.file_ext in VIDEO_EXTS]
    gifs = [p for p in candidates if p.file_ext in GIF_EXTS]

    cycle, idx = get_or_create_cycle(db)
    slot_type = cycle[idx]

    if slot_type == "image" and images:
        return random.choice(images)
    if slot_type == "video" and videos:
        return random.choice(videos)
    if slot_type == "gif" and gifs:
        return random.choice(gifs)

    for pool in (images, videos, gifs):
        if pool:
            return random.choice(pool)
    return random.choice(candidates)
