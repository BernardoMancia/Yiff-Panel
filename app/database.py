from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    e621_id = Column(Integer, unique=True, nullable=False, index=True)
    file_url = Column(Text, nullable=True)
    sample_url = Column(Text, nullable=True)
    preview_url = Column(Text, nullable=True)
    file_ext = Column(String(10), nullable=True)
    file_size = Column(Integer, nullable=True)
    score = Column(Integer, default=0)
    fav_count = Column(Integer, default=0)
    tags = Column(Text, nullable=True)
    status = Column(String(20), default="queued", index=True)
    queued_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    sent_at = Column(DateTime, nullable=True)
    is_deleted = Column(Boolean, default=False)
    is_priority = Column(Boolean, default=False, index=True)
    source = Column(String(20), default="e621")
    media_group_id = Column(String(100), nullable=True, index=True)
    message_id = Column(Integer, nullable=True)
    removed_by_reaction = Column(Boolean, default=False)
    logs = relationship("ScheduleLog", back_populates="post")


class ScheduleLog(Base):
    __tablename__ = "schedule_logs"

    id = Column(Integer, primary_key=True, index=True)
    triggered_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    next_run_at = Column(DateTime, nullable=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=True)
    success = Column(Boolean, default=False)
    error_message = Column(Text, nullable=True)
    post = relationship("Post", back_populates="logs")


class AppState(Base):
    __tablename__ = "app_state"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=True)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    display_name = Column(String(100), nullable=False)
    password_hash = Column(String(300), nullable=False)
    must_change_password = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    token = Column(String(64), primary_key=True)
    username = Column(String(50), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TagSuggestion(Base):
    __tablename__ = "tag_suggestions"

    id = Column(Integer, primary_key=True)
    tag = Column(String(100), nullable=False)
    status = Column(String(20), default="pending")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    reviewed_at = Column(DateTime, nullable=True)


class SentRegistry(Base):
    __tablename__ = "sent_registry"

    id = Column(Integer, primary_key=True)
    e621_id = Column(Integer, unique=True, nullable=False, index=True)
    file_url = Column(String, nullable=True)
    file_ext = Column(String(10), nullable=True)
    sent_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
