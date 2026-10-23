"""Create tables and apply small, repeatable PostgreSQL schema upgrades."""
from sqlalchemy import text

from .db import Base, engine


def ensure_schema():
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(74620147)"))
        Base.metadata.create_all(connection)
        connection.execute(text("ALTER TABLE players ADD COLUMN IF NOT EXISTS password_hash VARCHAR(128)"))
        connection.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS worker_id VARCHAR(64)"))
        connection.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 100"))
        connection.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS claim_token VARCHAR(64)"))
        connection.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ"))
        connection.execute(text("ALTER TABLE matches ADD COLUMN IF NOT EXISTS worker_id VARCHAR(64)"))
        connection.execute(text("ALTER TABLE matches ADD COLUMN IF NOT EXISTS replay_of INTEGER REFERENCES matches(id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_worker_id ON jobs (worker_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_matches_worker_id ON matches (worker_id)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_matches_replay_of ON matches (replay_of)"))
        # The old constraint also blocked intentional manual rematches on the
        # same day. Keep uniqueness only for automatic daily pairings.
        connection.execute(text("ALTER TABLE matches DROP CONSTRAINT IF EXISTS uq_daily_pair"))
        connection.execute(text("DROP INDEX IF EXISTS uq_daily_pair_by_day"))
        connection.execute(text("DROP INDEX IF EXISTS uq_daily_active_pair"))
        connection.execute(text("ALTER TABLE matches ADD COLUMN IF NOT EXISTS encounter_id INTEGER REFERENCES tournament_encounters(id)"))
        connection.execute(text("ALTER TABLE matches ADD COLUMN IF NOT EXISTS game_number INTEGER"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_matches_encounter_id ON matches (encounter_id)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_encounter_game ON matches (encounter_id, game_number)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_practice_request_key ON practice_requests (player_id, idempotency_key)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_practice_request_match ON practice_requests (match_id)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_tournament ON tournaments ((1)) WHERE status NOT IN ('complete', 'cancelled')"))
        connection.execute(text("ALTER TABLE tournament_actions ADD COLUMN IF NOT EXISTS appeal_until TIMESTAMPTZ"))
        connection.execute(text("ALTER TABLE tournament_actions ADD COLUMN IF NOT EXISTS notice_hidden_at TIMESTAMPTZ"))
        # Existing tournaments retain manual operation until explicitly enabled.
        connection.execute(text("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS auto_run BOOLEAN NOT NULL DEFAULT FALSE"))
        connection.execute(text("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS auto_submission_minutes INTEGER NOT NULL DEFAULT 1440"))
        connection.execute(text("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS auto_status TEXT"))
        connection.execute(text("ALTER TABLE tournament_elo_events ALTER COLUMN encounter_id DROP NOT NULL"))
        connection.execute(text("ALTER TABLE tournament_elo_events ADD COLUMN IF NOT EXISTS match_id INTEGER REFERENCES matches(id)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_elo_match ON tournament_elo_events (match_id)"))
        # Preserve old entries/matches, but require an explicit search for all new games.
        connection.execute(text("ALTER TABLE daily_entries DROP CONSTRAINT IF EXISTS uq_daily_entry_day"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_submission_request ON daily_entries (player_id, day, request_key)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_waiting_team ON daily_searches (player_id) WHERE status = 'waiting'"))
