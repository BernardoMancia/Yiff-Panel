from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.database import AppState, Post
from app.send_cycle import (
    STATIC_EXTS,
    GIF_EXTS,
    VIDEO_EXTS,
    get_or_create_cycle,
)

logger = logging.getLogger(__name__)

_QUEUE_ORDER_KEY = "queue_order"


def analyze_queue_composition(db: Session) -> dict:
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

    images = sum(1 for (ext,) in posts if ext in STATIC_EXTS)
    gifs = sum(1 for (ext,) in posts if ext in GIF_EXTS)
    videos = sum(1 for (ext,) in posts if ext in VIDEO_EXTS)
    animated = gifs + videos

    image_ratio = images / total
    gif_ratio = gifs / total
    video_ratio = videos / total
    animated_ratio = animated / total

    balance_ok = (
        total < settings.BALANCE_MIN_QUEUE_SIZE
        or (
            image_ratio <= 0.30
            and video_ratio >= 0.40
            and gif_ratio >= 0.20
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


def get_ordered_queue(db: Session, limit: int = 10) -> list[Post]:
    row = db.query(AppState).filter(AppState.key == _QUEUE_ORDER_KEY).first()
    if row and row.value:
        try:
            ids: list[int] = json.loads(row.value)
            valid = {
                p.id: p for p in db.query(Post)
                .filter(Post.status == "queued", Post.is_deleted == False)
                .all()
            }
            ordered = [valid[pid] for pid in ids if pid in valid]
            if not ordered and valid:
                ordered = compute_queue_order(db)
                persist_queue_order(db, ordered)
            return ordered[:limit]
        except Exception:
            pass
    ordered = compute_queue_order(db)
    persist_queue_order(db, ordered)
    return ordered[:limit]


def compute_queue_order(db: Session) -> list[Post]:
    candidates = (
        db.query(Post)
        .filter(Post.status == "queued", Post.is_deleted == False)
        .all()
    )
    if not candidates:
        return []

    priority_posts = sorted(
        [p for p in candidates if p.is_priority],
        key=lambda p: p.queued_at or datetime.min.replace(tzinfo=timezone.utc),
    )
    normal_posts = [p for p in candidates if not p.is_priority]

    cycle, idx = get_or_create_cycle(db)
    images = [p for p in normal_posts if p.file_ext in STATIC_EXTS]
    videos = [p for p in normal_posts if p.file_ext in VIDEO_EXTS]
    gifs = [p for p in normal_posts if p.file_ext in GIF_EXTS]

    random.shuffle(images)
    random.shuffle(videos)
    random.shuffle(gifs)

    img_ptr = vid_ptr = gif_ptr = 0
    normal_ordered: list[Post] = []
    current_idx = idx
    total_normal = len(normal_posts)

    for _ in range(total_normal * 3):
        if len(normal_ordered) >= total_normal:
            break
        slot_type = cycle[current_idx % len(cycle)]
        current_idx += 1

        if slot_type == "image" and img_ptr < len(images):
            normal_ordered.append(images[img_ptr]); img_ptr += 1
        elif slot_type == "video" and vid_ptr < len(videos):
            normal_ordered.append(videos[vid_ptr]); vid_ptr += 1
        elif slot_type == "gif" and gif_ptr < len(gifs):
            normal_ordered.append(gifs[gif_ptr]); gif_ptr += 1
        else:
            continue

    used_ids = {p.id for p in normal_ordered}
    for pool in (images, videos, gifs):
        for p in pool:
            if p.id not in used_ids:
                normal_ordered.append(p)
                used_ids.add(p.id)

    return priority_posts + normal_ordered


def persist_queue_order(db: Session, posts: list[Post]) -> None:
    ids = [p.id for p in posts]
    row = db.query(AppState).filter(AppState.key == _QUEUE_ORDER_KEY).first()
    serialized = json.dumps(ids)
    if row:
        row.value = serialized
    else:
        db.add(AppState(key=_QUEUE_ORDER_KEY, value=serialized))
    db.commit()


def remove_from_queue_order(db: Session, post_id: int) -> None:
    row = db.query(AppState).filter(AppState.key == _QUEUE_ORDER_KEY).first()
    if not row or not row.value:
        return
    try:
        ids = json.loads(row.value)
        ids = [i for i in ids if i != post_id]
        row.value = json.dumps(ids)
        db.commit()
    except Exception:
        pass


def prepend_to_queue_order(db: Session, post_ids: list[int]) -> None:
    row = db.query(AppState).filter(AppState.key == _QUEUE_ORDER_KEY).first()
    if row and row.value:
        try:
            ids = json.loads(row.value)
            ids = post_ids + ids
            row.value = json.dumps(ids)
        except Exception:
            row.value = json.dumps(post_ids)
    else:
        if row:
            row.value = json.dumps(post_ids)
        else:
            db.add(AppState(key=_QUEUE_ORDER_KEY, value=json.dumps(post_ids)))
    db.commit()
