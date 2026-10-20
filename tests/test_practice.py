"""Practice request quotas, idempotency, and retry behavior (disposable PostgreSQL)."""
import json
import os
import unittest
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select, text, func
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (Job, Match, Player, PracticeRequest, Submission,
                        Tournament, TournamentRound, RoundSubmission)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to disposable PostgreSQL")
class PracticeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["TEST_DATABASE_URL"])
        cls.Session = sessionmaker(cls.engine, expire_on_commit=False)
        from app import schema
        with patch.object(schema, "engine", cls.engine):
            schema.ensure_schema()
        from app import main
        cls.main = main

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        with self.engine.begin() as conn:
            conn.execute(text("TRUNCATE " + ",".join(Base.metadata.tables) + " RESTART IDENTITY CASCADE"))
        self.db = self.Session()
        self.team = Player(name="Practice Team", token_hash="t" * 64)
        self.db.add(self.team)
        self.db.flush()
        self.submission = Submission(player_id=self.team.id, language="python",
                                     source='def choose_move(state):\n return state["legal_moves"][0]\n', status="ready")
        self.db.add(self.submission)
        self.db.flush()
        from app import tournament as tournament_service
        rules = (Path(__file__).resolve().parents[1] / "GAMERULE.md").read_text(encoding="utf-8")
        digest = hashlib.sha256(rules.encode()).hexdigest()
        environment = tournament_service.dump(tournament_service.environment())
        cup = Tournament(name="Practice Compatibility Cup", registration_open=datetime.now(timezone.utc),
                         registration_close=datetime.now(timezone.utc), incident_policy="", appeal_contact="",
                         rules_digest=digest, rules_text=rules, environment=environment)
        self.db.add(cup)
        self.db.flush()
        game_round = TournamentRound(tournament_id=cup.id, level=0, rules_digest=digest, environment=environment)
        self.db.add(game_round)
        self.db.flush()
        self.db.add(RoundSubmission(round_id=game_round.id, player_id=self.team.id,
                                    submission_id=self.submission.id, attempt=1, request_key="practice-test"))
        self.db.commit()
        self.db.close()
        self.patch = patch.object(self.main, "SessionLocal", self.Session)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def create(self, key="practice-key"):
        return self.main.create_practice(
            self.main.PracticeInput(opponent_id="first", submission_id=self.submission.id),
            self.team.id, key)

    def test_request_is_idempotent_and_queues_low_priority_job(self):
        first = self.create()
        second = self.create()
        self.assertEqual(first, second)
        with self.assertRaises(HTTPException) as changed:
            self.main.create_practice(
                self.main.PracticeInput(opponent_id="greedy", submission_id=self.submission.id),
                self.team.id, "practice-key")
        self.assertEqual(changed.exception.status_code, 409)
        with self.Session() as db:
            self.assertEqual(db.scalar(select(func.count(Match.id)).where(Match.kind == "practice")), 1)
            job = db.scalar(select(Job).where(Job.kind == "match"))
            self.assertEqual(json.loads(job.payload)["match_id"], first["match_id"])
            self.assertGreater(job.priority, 0)
            self.assertEqual(db.scalar(select(func.count(PracticeRequest.id))), 1)

    def test_rejects_second_active_match_and_invalid_owner(self):
        self.create()
        with self.assertRaises(HTTPException) as active:
            self.create("another-key")
        self.assertEqual(active.exception.status_code, 409)
        with self.assertRaises(HTTPException) as owner:
            self.main.create_practice(
                self.main.PracticeInput(opponent_id="first", submission_id=self.submission.id + 1),
                self.team.id, "invalid-submission")
        self.assertEqual(owner.exception.status_code, 409)

    def test_hides_and_rejects_ready_submission_from_incompatible_environment(self):
        from app import tournament as tournament_service
        with self.Session.begin() as db:
            cup = db.scalar(select(Tournament).where(Tournament.name == "Practice Compatibility Cup"))
            old_round = db.scalar(select(TournamentRound).where(TournamentRound.tournament_id == cup.id))
            old_environment = json.loads(old_round.environment)
            old_environment["image"] = "old-image@sha256:" + "0" * 64
            incompatible_round = TournamentRound(tournament_id=cup.id, level=1,
                                                  rules_digest=old_round.rules_digest,
                                                  environment=json.dumps(old_environment))
            db.add(incompatible_round)
            stale = Submission(player_id=self.team.id, language="python", source=self.submission.source, status="ready")
            db.add(stale)
            db.flush()
            db.add(RoundSubmission(round_id=incompatible_round.id, player_id=self.team.id,
                                   submission_id=stale.id, attempt=1, request_key="old-environment"))
            stale_id = stale.id
        state = self.main.get_practice_state(self.team.id)
        self.assertNotIn(stale_id, {row["id"] for row in state["submissions"]})
        with self.assertRaises(HTTPException) as incompatible:
            self.main.create_practice(self.main.PracticeInput(opponent_id="first", submission_id=stale_id),
                                      self.team.id, "incompatible")
        self.assertEqual(incompatible.exception.status_code, 409)

    def test_retry_reuses_same_match_without_counting_another_acceptance(self):
        accepted = self.create()
        with self.Session.begin() as db:
            match = db.get(Match, accepted["match_id"])
            match.status = "failed"
            match.detail = '{"error":"sandbox unavailable"}'
        retried = self.main.retry_practice(accepted["match_id"], self.team.id)
        self.assertEqual(retried["match_id"], accepted["match_id"])
        with self.Session() as db:
            self.assertEqual(db.scalar(select(func.count(Match.id)).where(Match.kind == "practice")), 1)
            self.assertEqual(db.get(Match, accepted["match_id"]).status, "queued")

    def test_practice_submission_is_available_without_an_open_tournament_and_idempotent(self):
        from app.models import PracticeSubmissionRequest
        source = 'def choose_move(state):\n return state["legal_moves"][0]\n'
        body = self.main.Submit(language="python", source=source)
        first = self.main.submit_practice_bot(body, self.team.id, "practice-bot-key")
        second = self.main.submit_practice_bot(body, self.team.id, "practice-bot-key")
        self.assertEqual(first, second)

        with self.assertRaises(HTTPException) as changed:
            self.main.submit_practice_bot(
                self.main.Submit(language="python", source=source + "# changed\n"),
                self.team.id, "practice-bot-key")
        self.assertEqual(changed.exception.status_code, 409)

        with self.Session.begin() as db:
            db.query(Tournament).update({Tournament.status: "complete"})
            submission = db.get(Submission, first["submission_id"])
            submission.status = "ready"
            record = db.scalar(select(PracticeSubmissionRequest).where(
                PracticeSubmissionRequest.submission_id == submission.id))
            self.assertIsNotNone(record)
            job = db.scalar(select(Job).where(Job.kind == "compile"))
            self.assertTrue(json.loads(job.payload)["practice_validation"])
            self.assertGreater(job.priority, 0)

        state = self.main.get_practice_state(self.team.id)
        self.assertIn(first["submission_id"], {row["id"] for row in state["submissions"]})

    def test_daily_quota_counts_accepted_practice_matches(self):
        from app.main import VN
        today = datetime.now(VN).date().isoformat()
        opponent = Player(name="Quota Opponent", token_hash="o" * 64)
        self.db.add(opponent)
        self.db.flush()
        for _ in range(10):
            self.db.add(Match(player_a=self.team.id, player_b=opponent.id,
                              submission_a=self.submission.id, submission_b=self.submission.id,
                              event_day=today, kind="practice", status="done"))
        self.db.commit()
        with self.assertRaises(HTTPException) as quota:
            self.create("eleventh")
        self.assertEqual(quota.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
