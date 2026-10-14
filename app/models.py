from datetime import datetime, timezone
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

def now_utc(): return datetime.now(timezone.utc)

class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    language: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    artifact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    compile_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("player_a", "player_b", "event_day", name="uq_daily_pair"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    player_a: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    player_b: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    submission_a: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    submission_b: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    event_day: Mapped[str] = mapped_column(String(10), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    points_a: Mapped[float] = mapped_column(Float, default=0)
    points_b: Mapped[float] = mapped_column(Float, default=0)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class EventControl(Base):
    __tablename__ = "event_control"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    start_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    end_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
