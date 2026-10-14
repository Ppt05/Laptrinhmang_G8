"""Create tables and apply small, repeatable PostgreSQL schema upgrades."""
from sqlalchemy import text

from .db import Base, engine


def ensure_schema():
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE players ADD COLUMN IF NOT EXISTS password_hash VARCHAR(128)"))
        connection.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS worker_id VARCHAR(64)"))
        connection.execute(text("ALTER TABLE matches ADD COLUMN IF NOT EXISTS worker_id VARCHAR(64)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_worker_id ON jobs (worker_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_matches_worker_id ON matches (worker_id)"))
        # The old constraint also blocked intentional manual rematches on the
        # same day. Keep uniqueness only for automatic daily pairings.
        connection.execute(text("ALTER TABLE matches DROP CONSTRAINT IF EXISTS uq_daily_pair"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_pair_by_day ON matches (LEAST(player_a, player_b), GREATEST(player_a, player_b), event_day) WHERE kind = 'daily'"))
