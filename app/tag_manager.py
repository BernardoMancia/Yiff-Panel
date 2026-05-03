from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.database import AppState

logger = logging.getLogger(__name__)

_KEY_REQUIRED = "tag_required"
_KEY_OR = "tag_or"
_KEY_BLACKLIST = "tag_blacklist"


def _read(db: Session, key: str) -> list[str] | None:
    row = db.query(AppState).filter(AppState.key == key).first()
    if row and row.value:
        try:
            return json.loads(row.value)
        except Exception:
            pass
    return None


def _write(db: Session, key: str, tags: list[str]) -> None:
    unique = sorted(set(t.strip().lower() for t in tags if t.strip()))
    row = db.query(AppState).filter(AppState.key == key).first()
    if row:
        row.value = json.dumps(unique)
    else:
        db.add(AppState(key=key, value=json.dumps(unique)))
    db.commit()


def _defaults_from_settings() -> tuple[list[str], list[str], list[str]]:
    from app.config import settings
    tokens = settings.E621_TAGS.split()
    required = [t for t in tokens if not t.startswith(("~", "-")) and ":" not in t]
    or_tags = [t[1:] for t in tokens if t.startswith("~")]
    blacklist = [t[1:] for t in tokens if t.startswith("-")]
    return required, or_tags, blacklist


def init_tags(db: Session) -> None:
    required, or_tags, blacklist = _defaults_from_settings()
    for key, default in (
        (_KEY_REQUIRED, required),
        (_KEY_OR, or_tags),
        (_KEY_BLACKLIST, blacklist),
    ):
        if _read(db, key) is None:
            _write(db, key, default)
    logger.info("Tag manager initialized.")


def get_required_tags(db: Session) -> list[str]:
    return _read(db, _KEY_REQUIRED) or _defaults_from_settings()[0]


def get_or_tags(db: Session) -> list[str]:
    return _read(db, _KEY_OR) or _defaults_from_settings()[1]


def get_blacklist_tags(db: Session) -> list[str]:
    return _read(db, _KEY_BLACKLIST) or _defaults_from_settings()[2]


def build_query(db: Session, extra: str = "") -> str:
    parts = get_required_tags(db)
    parts += [f"~{t}" for t in get_or_tags(db)]
    parts += [f"-{t}" for t in get_blacklist_tags(db)]
    if extra:
        parts.append(extra)
    parts += ["order:random", "rating:e"]
    return " ".join(parts)


def add_tag(db: Session, tag_type: str, tag: str) -> None:
    key = {"required": _KEY_REQUIRED, "or": _KEY_OR, "blacklist": _KEY_BLACKLIST}.get(tag_type)
    if not key:
        raise ValueError(f"Invalid tag_type: {tag_type}")
    tag = tag.strip().lower().lstrip("~-")
    if not tag:
        raise ValueError("Empty tag")
    tags = _read(db, key) or []
    if tag not in tags:
        tags.append(tag)
        _write(db, key, tags)


def remove_tag(db: Session, tag_type: str, tag: str) -> None:
    key = {"required": _KEY_REQUIRED, "or": _KEY_OR, "blacklist": _KEY_BLACKLIST}.get(tag_type)
    if not key:
        raise ValueError(f"Invalid tag_type: {tag_type}")
    tag = tag.strip().lower().lstrip("~-")
    tags = _read(db, key) or []
    tags = [t for t in tags if t != tag]
    _write(db, key, tags)
