from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.database import AdminSession, AdminUser

_SESSION_DAYS = 30
_PBKDF2_ITERS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERS)
    return f"{salt}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, key_hex = stored.split("$", 1)
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERS)
        return hmac.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


def create_session(db: Session, username: str) -> str:
    token = secrets.token_hex(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=_SESSION_DAYS)
    db.add(AdminSession(token=token, username=username, expires_at=expires_at))
    db.commit()
    return token


def get_user_from_token(db: Session, token: str | None) -> AdminUser | None:
    if not token:
        return None
    sess = (
        db.query(AdminSession)
        .filter(
            AdminSession.token == token,
            AdminSession.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    if not sess:
        return None
    return db.query(AdminUser).filter(AdminUser.username == sess.username).first()


def invalidate_session(db: Session, token: str) -> None:
    db.query(AdminSession).filter(AdminSession.token == token).delete()
    db.commit()


def init_admin(db: Session) -> None:
    if not db.query(AdminUser).first():
        db.add(
            AdminUser(
                username="luke_arwolf",
                display_name="Luke Arwolf",
                password_hash=hash_password("123456789"),
                must_change_password=True,
            )
        )
        db.commit()
