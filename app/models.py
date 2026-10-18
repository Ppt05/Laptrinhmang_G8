from datetime import datetime, timezone
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Numeric
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
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class Match(Base):
    __tablename__ = "matches"
    id: Mapped[int] = mapped_column(primary_key=True)
    encounter_id: Mapped[int | None] = mapped_column(ForeignKey("tournament_encounters.id"), nullable=True, index=True)
    game_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (UniqueConstraint("encounter_id", "game_number", name="uq_encounter_game"),)
    player_a: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    player_b: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    submission_a: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    submission_b: Mapped[int] = mapped_column(ForeignKey("submissions.id"))
    event_day: Mapped[str] = mapped_column(String(10), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    replay_of: Mapped[int | None] = mapped_column(ForeignKey("matches.id"), nullable=True, unique=True)
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

class WorkerNode(Base):
    __tablename__ = "worker_nodes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    instance_token: Mapped[str] = mapped_column(String(64))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Tournament(Base):
    __tablename__ = "tournaments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="registration", index=True)
    max_group_size: Mapped[int] = mapped_column(default=4)
    qualifiers: Mapped[int] = mapped_column(default=2)
    registration_open: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    registration_close: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    incident_policy: Mapped[str] = mapped_column(Text)
    appeal_contact: Mapped[str] = mapped_column(Text)
    auto_run: Mapped[bool] = mapped_column(default=True,server_default="false")
    auto_submission_minutes: Mapped[int] = mapped_column(default=1440,server_default="1440")
    auto_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules_digest: Mapped[str] = mapped_column(String(64))
    rules_text: Mapped[str] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(Text)
    allocation: Mapped[str | None] = mapped_column(Text, nullable=True)
    bracket: Mapped[str | None] = mapped_column(Text, nullable=True)
    champion_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Enrollment(Base):
    __tablename__ = "tournament_enrollments"
    __table_args__ = (UniqueConstraint("tournament_id", "player_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    status: Mapped[str] = mapped_column(String(30), default="enrolled")
    frozen_elo: Mapped[float | None] = mapped_column(Float, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class TournamentGroup(Base):
    __tablename__ = "tournament_groups"
    __table_args__ = (UniqueConstraint("tournament_id", "number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    final_order: Mapped[str | None] = mapped_column(Text, nullable=True)


class GroupMember(Base):
    __tablename__ = "tournament_group_members"
    __table_args__ = (UniqueConstraint("tournament_id", "player_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("tournament_groups.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))


class TournamentRound(Base):
    __tablename__ = "tournament_rounds"
    __table_args__ = (UniqueConstraint("tournament_id", "level"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), index=True)
    level: Mapped[int] = mapped_column(Integer)  # 0: groups including playoffs
    status: Mapped[str] = mapped_column(String(30), default="planned")
    opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rules_digest: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(Text)
    qualification: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    appeal_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RoundSubmission(Base):
    __tablename__ = "tournament_submissions"
    __table_args__ = (UniqueConstraint("round_id", "player_id", "attempt"),
                      UniqueConstraint("round_id", "player_id", "request_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("tournament_rounds.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), unique=True)
    attempt: Mapped[int] = mapped_column(Integer)
    request_key: Mapped[str] = mapped_column(String(128))


class LockedBot(Base):
    __tablename__ = "tournament_locked_bots"
    __table_args__ = (UniqueConstraint("round_id", "player_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("tournament_rounds.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("submissions.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text)


class Encounter(Base):
    __tablename__ = "tournament_encounters"
    __table_args__ = (UniqueConstraint("round_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("tournament_rounds.id"), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("tournament_groups.id"), nullable=True)
    key: Mapped[str] = mapped_column(String(120))
    playoff: Mapped[str | None] = mapped_column(String(120), nullable=True)
    batch: Mapped[int] = mapped_column(default=1)
    slot: Mapped[int] = mapped_column(default=0)
    player_a: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    player_b: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="scheduled", index=True)
    administrative: Mapped[bool] = mapped_column(default=False)
    excluded: Mapped[bool] = mapped_column(default=False)
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    playing_result: Mapped[float | None] = mapped_column(Float, nullable=True)
    points_a: Mapped[float] = mapped_column(Float, default=0)
    points_b: Mapped[float] = mapped_column(Float, default=0)
    game_points_a: Mapped[float] = mapped_column(Float, default=0)
    game_points_b: Mapped[float] = mapped_column(Float, default=0)
    extra_scores: Mapped[str] = mapped_column(Text, default="[]")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_south: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    appeal_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecordedDraw(Base):
    __tablename__ = "tournament_draws"
    __table_args__ = (UniqueConstraint("tournament_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), index=True)
    key: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str] = mapped_column(Text)
    choices: Mapped[str] = mapped_column(Text)
    seed: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(80), default="python-random-shuffle-v1")
    result: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class OrganizerAction(Base):
    __tablename__ = "tournament_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), index=True)
    round_id: Mapped[int | None] = mapped_column(ForeignKey("tournament_rounds.id"), nullable=True)
    encounter_id: Mapped[int | None] = mapped_column(ForeignKey("tournament_encounters.id"), nullable=True)
    actor: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(40))
    evidence: Mapped[str] = mapped_column(Text)
    hold: Mapped[bool] = mapped_column(default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    appeal_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notice_hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class EloEvent(Base):
    __tablename__ = "tournament_elo_events"
    id: Mapped[int] = mapped_column(primary_key=True)  # immutable rating order
    encounter_id: Mapped[int] = mapped_column(ForeignKey("tournament_encounters.id"), unique=True)
    player_a: Mapped[int] = mapped_column(ForeignKey("players.id"))
    player_b: Mapped[int] = mapped_column(ForeignKey("players.id"))
    active: Mapped[bool] = mapped_column(default=True)
    score: Mapped[float] = mapped_column(Float)
    before_a: Mapped[float] = mapped_column(Numeric(24, 12), default=1000)
    before_b: Mapped[float] = mapped_column(Numeric(24, 12), default=1000)
    after_a: Mapped[float] = mapped_column(Numeric(24, 12), default=1000)
    after_b: Mapped[float] = mapped_column(Numeric(24, 12), default=1000)


class GameAttempt(Base):
    __tablename__ = "tournament_game_attempts"
    __table_args__ = (UniqueConstraint("match_id", "attempt"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="running")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
