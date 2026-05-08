from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.database import AppState

logger = logging.getLogger(__name__)

_KEY_MANDATORY = "tag_mandatory"
_KEY_REQUIRED = "tag_required"
_KEY_OR = "tag_or"
_KEY_BLACKLIST = "tag_blacklist"

_DEFAULT_MANDATORY = ["male", "gay"]
_DEFAULT_REQUIRED = ["knotted_penis", "cum_inflation"]
_DEFAULT_OR = [
    "feral", "animal_genitalia", "animal_penis",
    "equine_penis", "equine_genitalia", "canine_genitalia",
    "femboy", "knot",
]
_DEFAULT_BLACKLIST = [
    "road", "machine", "car", "aircraft", "airplane", "radiation", "gore",
    "vore", "imminent_vore", "anal_vore", "soft_vore", "diaper",
]


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


def init_tags(db: Session) -> None:
    for key, default in (
        (_KEY_MANDATORY, _DEFAULT_MANDATORY),
        (_KEY_REQUIRED, _DEFAULT_REQUIRED),
        (_KEY_OR, _DEFAULT_OR),
        (_KEY_BLACKLIST, _DEFAULT_BLACKLIST),
    ):
        if _read(db, key) is None:
            _write(db, key, default)

    mand_set = set(_read(db, _KEY_MANDATORY) or [])
    req = _read(db, _KEY_REQUIRED) or []
    cleaned = [t for t in req if t not in mand_set]
    if len(cleaned) != len(req):
        _write(db, _KEY_REQUIRED, cleaned)

    logger.info("Tag manager initialized.")


def get_mandatory_tags(db: Session) -> list[str]:
    return _read(db, _KEY_MANDATORY) or _DEFAULT_MANDATORY


def get_required_tags(db: Session) -> list[str]:
    return _read(db, _KEY_REQUIRED) or _DEFAULT_REQUIRED


def get_or_tags(db: Session) -> list[str]:
    return _read(db, _KEY_OR) or _DEFAULT_OR


def get_blacklist_tags(db: Session) -> list[str]:
    return _read(db, _KEY_BLACKLIST) or _DEFAULT_BLACKLIST


def build_query(db: Session, extra: str = "") -> str:
    mandatory = get_mandatory_tags(db)
    required = get_required_tags(db)
    or_tags = get_or_tags(db)
    blacklist = get_blacklist_tags(db)

    parts: list[str] = []

    parts += mandatory
    parts += required
    parts += [f"~{t}" for t in or_tags]
    parts += [f"-{t}" for t in blacklist]
    if extra:
        parts.append(extra)
    parts += ["order:random", "rating:e"]
    return " ".join(parts)


def add_tag(db: Session, tag_type: str, tag: str) -> None:
    key = {
        "mandatory": _KEY_MANDATORY,
        "required": _KEY_REQUIRED,
        "or": _KEY_OR,
        "blacklist": _KEY_BLACKLIST,
    }.get(tag_type)
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
    key = {
        "mandatory": _KEY_MANDATORY,
        "required": _KEY_REQUIRED,
        "or": _KEY_OR,
        "blacklist": _KEY_BLACKLIST,
    }.get(tag_type)
    if not key:
        raise ValueError(f"Invalid tag_type: {tag_type}")
    tag = tag.strip().lower().lstrip("~-")
    tags = _read(db, key) or []
    tags = [t for t in tags if t != tag]
    _write(db, key, tags)
