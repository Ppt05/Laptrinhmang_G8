"""Round controls, mixed worker versions and revoked-result delivery on PostgreSQL."""
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select, text, func

import test_tournament as fixtures
from app import tournament as s, worker
from app.models import (Job, Match, WorkerNode, TournamentRound, GameAttempt,
                        OrganizerAction, Submission, RoundSubmission, EloEvent)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to disposable PostgreSQL")
class RoundControlTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.TournamentTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.TournamentTests.tearDownClass.__func__)
    setUp = fixtures.TournamentTests.setUp
    tearDown = fixtures.TournamentTests.tearDown
    make = fixtures.TournamentTests.make
    ready_round = fixtures.TournamentTests.ready_round

    def nodes(self):
        for name in ("chosen", "other"):
            self.db.add(WorkerNode(id=name, instance_token=name, last_seen=s.utcnow(), round_control_version=1))
        self.db.add(WorkerNode(id="legacy", instance_token="legacy", last_seen=s.utcnow()))
        self.db.flush()

    def prepared(self):
        t, r = self.make(4, 4, 2)
        self.ready_round(t, r)
        for job in self.db.scalars(select(Job)): job.status = "done"
        self.nodes()
        s.select_worker(self.db, t, r, "chosen", "admin")
        s.start_batch(self.db, t, r, None, "admin")
        self.db.flush()
        return t, r

    def test_worker_routes_compile_and_match_jobs_and_never_falls_back(self):
        t, r = self.make(2, 4, 2)
        self.nodes()
        s.select_worker(self.db, t, r, "chosen", "admin")
        now = s.utcnow()
        s.open_window(self.db, t, r, now-timedelta(minutes=1), now+timedelta(minutes=1), "admin")
        link = s.submit(self.db, t, r, 1, "python", "pass", "request")
        with patch.object(worker, "WORKER_ID", "other"):
            self.assertIsNone(worker.claim(self.db, "other"))
        with patch.object(worker, "WORKER_ID", "chosen"):
            job = worker.claim(self.db, "chosen")
        self.assertEqual((job.round_id, job.status, job.kind), (r.id, "tournament_running", "compile"))
        self.assertEqual(json.loads(job.payload)["submission_id"], link.submission_id)
        with self.assertRaises(HTTPException): s.select_worker(self.db, t, r, "other", "admin")
        s.pause_round(self.db, t, r, "admin")
        s.select_worker(self.db, t, r, "other", "admin")
        s.resume_round(self.db, t, r, "admin")
        with patch.object(worker, "WORKER_ID", "chosen"):
            self.assertIsNone(worker.claim(self.db, "chosen"))
        with patch.object(worker, "WORKER_ID", "other"):
            resumed = worker.claim(self.db, "other")
        self.assertNotEqual(resumed.id, job.id)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(RoundSubmission)), 1)

    def test_legacy_workers_cannot_claim_or_schedule_controlled_jobs(self):
        t, r = self.prepared()
        # These are the actual predicates used by previous worker versions.
        self.assertIsNone(self.db.scalar(select(Job).where(Job.status == "queued")))
        legacy_hold = self.db.scalar(select(OrganizerAction).where(
            OrganizerAction.tournament_id == t.id, OrganizerAction.hold.is_(True),
            OrganizerAction.resolved_at.is_(None)))
        self.assertEqual(legacy_hold.kind, s.QUEUE_GUARD)
        self.assertFalse(s.held(self.db, t, r))
        public = s.public_view(self.db, t)
        self.assertEqual(public["holds"], [])
        self.assertEqual(public["notices"], [])
        with patch.object(worker, "WORKER_ID", "other"):
            self.assertIsNone(worker.claim(self.db, "other"))
        self.db.get(WorkerNode, "chosen").last_seen = s.utcnow()-timedelta(minutes=5)
        self.assertTrue(self.db.scalar(select(Job.id).where(Job.status == "tournament_queued")))
        with patch.object(worker, "WORKER_ID", "other"):
            self.assertIsNone(worker.claim(self.db, "other"))

    def test_stop_preserves_completed_games_and_attempts_resume_only_unfinished(self):
        t, r = self.prepared()
        with patch.object(worker, "WORKER_ID", "chosen"):
            job = worker.claim(self.db, "chosen")
        match = self.db.get(Match, json.loads(job.payload)["match_id"])
        match.status, match.detail, match.points_a, match.points_b = "done", '{"turns":[]}', 1, 0
        job.status = "done"
        self.db.flush(); s.after_game(self.db, match); self.db.flush()
        with patch.object(worker, "WORKER_ID", "chosen"):
            running = worker.claim(self.db, "chosen")
        unfinished = self.db.get(Match, json.loads(running.payload)["match_id"])
        attempt = self.db.scalar(select(GameAttempt).where(GameAttempt.match_id == unfinished.id))
        s.pause_round(self.db, t, r, "admin"); self.db.flush()
        self.assertTrue(s.round_paused(self.db, r))
        self.assertEqual((running.status, running.claim_token, unfinished.status), ("cancelled", None, "scheduled"))
        self.assertEqual(attempt.status, "abandoned")
        self.assertEqual((match.status, match.detail, match.points_a), ("done", '{"turns":[]}', 1))
        actions = self.db.scalar(select(func.count()).select_from(OrganizerAction))
        s.pause_round(self.db, t, r, "admin"); self.db.flush()
        self.assertEqual(self.db.scalar(select(func.count()).select_from(OrganizerAction)), actions)
        with patch.object(worker, "WORKER_ID", "chosen"):
            self.assertIsNone(worker.claim(self.db, "chosen"))
        s.auto_tick(self.db); self.assertIn("dừng", t.auto_status)
        with self.assertRaises(HTTPException): s.start_batch(self.db, t, r, 2, "admin", force=True)
        self.assertTrue(s.round_paused(self.db, r))
        s.resume_round(self.db, t, r, "admin"); self.db.flush()
        queued = self.db.scalars(select(Job).where(Job.round_id == r.id, Job.status == "tournament_queued")).all()
        self.assertTrue(queued)
        self.assertNotIn(match.id, [json.loads(j.payload).get("match_id") for j in queued])
        self.assertTrue(all(j.id != running.id for j in queued))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Match)), 12)
        self.assertFalse(s.round_paused(self.db, r))

    def test_late_result_from_revoked_worker_rolls_back_after_resume(self):
        t, r = self.prepared()
        with patch.object(worker, "WORKER_ID", "chosen"):
            job = worker.claim(self.db, "chosen")
        jid, rid, tid = job.id, r.id, t.id
        mid = json.loads(job.payload)["match_id"]
        self.db.commit()
        started, finish = threading.Event(), threading.Event()

        def stale_game(db, match):
            started.set()
            self.assertTrue(finish.wait(10))
            match.status, match.points_a, match.points_b, match.detail = "done", 1, 0, '{"stale":true}'

        with patch.object(worker, "SessionLocal", self.Session), patch.object(worker, "WORKER_ID", "chosen"), patch.object(worker, "play_match", stale_game):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(worker.process, jid, "chosen")
                try:
                    self.assertTrue(started.wait(10))
                    with self.Session.begin() as db:
                        s.serialize(db)
                        current_t, current_r = s.get_tournament(db, tid), s.get_round(db, tid, rid)
                        s.pause_round(db, current_t, current_r, "admin")
                        s.resume_round(db, current_t, current_r, "admin")
                finally:
                    finish.set()
                future.result(timeout=10)
        self.db.expire_all()
        match = self.db.get(Match, mid)
        self.assertNotEqual(match.status, "done")
        self.assertNotEqual(match.detail, '{"stale":true}')
        self.assertEqual(self.db.get(Job, jid).status, "cancelled")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(EloEvent)), 0)

    def test_recovery_keeps_protected_queue_and_worker_pin(self):
        t, r = self.prepared()
        with patch.object(worker, "WORKER_ID", "chosen"):
            job = worker.claim(self.db, "old-instance")
        self.db.get(WorkerNode, "chosen").instance_token = "new-instance"
        worker.recover_abandoned_jobs(self.db)
        self.assertEqual(job.status, "tournament_queued")
        self.assertEqual(r.worker_id, "chosen")
        with patch.object(worker, "WORKER_ID", "other"):
            self.assertIsNone(worker.claim(self.db, "other"))
        with patch.object(worker, "WORKER_ID", "chosen"):
            reclaimed = worker.claim(self.db, "new-instance")
            worker.release_claim(self.db, reclaimed.id, "new-instance")
        self.assertEqual(reclaimed.status, "tournament_queued")

    def test_stopped_compilation_cannot_commit_ready_or_spend_another_attempt(self):
        t, r = self.make(2, 4, 2)
        self.nodes(); s.select_worker(self.db, t, r, "chosen", "admin")
        now = s.utcnow()
        s.open_window(self.db, t, r, now-timedelta(minutes=1), now+timedelta(minutes=1), "admin")
        link = s.submit(self.db, t, r, 1, "python", "pass", "compile")
        with patch.object(worker, "WORKER_ID", "chosen"):
            job = worker.claim(self.db, "chosen")
        jid, tid, rid, sid = job.id, t.id, r.id, link.submission_id
        self.db.commit()
        started, finish = threading.Event(), threading.Event()
        def stale_compile(db, submission, validate=False):
            started.set()
            self.assertTrue(finish.wait(10))
            submission.status, submission.artifact = "ready", "stale.bin"
        with patch.object(worker, "SessionLocal", self.Session), patch.object(worker, "WORKER_ID", "chosen"), patch.object(worker, "compile_submission", stale_compile):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(worker.process, jid, "chosen")
                try:
                    self.assertTrue(started.wait(10))
                    with self.Session.begin() as db:
                        s.serialize(db)
                        current_t, current_r = s.get_tournament(db, tid), s.get_round(db, tid, rid)
                        s.pause_round(db, current_t, current_r, "admin")
                        s.resume_round(db, current_t, current_r, "admin")
                finally:
                    finish.set()
                future.result(timeout=10)
        self.db.expire_all()
        self.assertEqual(self.db.get(Submission, sid).status, "queued")
        self.assertIsNone(self.db.get(Submission, sid).artifact)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(RoundSubmission)), 1)
        self.assertEqual(self.db.get(Job, jid).status, "cancelled")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Job).where(
            Job.round_id == rid, Job.status == "tournament_queued")), 1)

    def test_resume_keeps_other_holds_and_selection_rejects_old_offline_workers(self):
        t, r = self.prepared()
        for worker_id in ("unknown", "legacy"):
            with self.assertRaises(HTTPException): s.select_worker(self.db, t, r, worker_id, "admin")
        self.db.get(WorkerNode, "other").last_seen = s.utcnow()-timedelta(minutes=1)
        with self.assertRaises(HTTPException): s.select_worker(self.db, t, r, "other", "admin")
        s.pause_round(self.db, t, r, "admin")
        s.exception(self.db, t, r, "hold", {}, "admin")
        s.resume_round(self.db, t, r, "admin"); self.db.flush()
        self.assertFalse(s.round_paused(self.db, r))
        self.assertTrue(s.held(self.db, t, r))
        guard = self.db.scalar(select(OrganizerAction).where(OrganizerAction.kind == s.QUEUE_GUARD))
        self.assertIsNone(guard.resolved_at)

    def test_authenticated_controls_api_and_round_status(self):
        from fastapi.testclient import TestClient
        from app import main, tournament_api
        t, r = self.prepared(); tid, rid = t.id, r.id; self.db.commit()
        with patch.object(main, "SessionLocal", self.Session), patch.object(tournament_api, "SessionLocal", self.Session), patch.dict(os.environ, {"ADMIN_API_KEY": "control-key"}):
            client = TestClient(main.app)
            base = f"/api/admin/tournaments/{tid}/rounds/{rid}"
            headers = {"X-Admin-Key": "control-key"}
            self.assertEqual(client.post(base+"/pause", json={}).status_code, 401)
            self.assertEqual(client.put(base+"/worker", json={"worker_id": "other"}).status_code, 401)
            for action in ("pause", "pause"):
                self.assertEqual(client.post(base+"/"+action, json={}, headers=headers).status_code, 200)
            self.assertEqual(client.put(base+"/worker", json={"worker_id": "legacy"}, headers=headers).status_code, 400)
            self.assertEqual(client.put(base+"/worker", json={"worker_id": "other"}, headers=headers).status_code, 200)
            public = client.get(f"/api/tournaments/{tid}").json()
            self.assertTrue(public["rounds"][0]["paused"])
            self.assertEqual(public["rounds"][0]["worker_id"], "other")
            self.assertEqual(client.post(base+"/resume", json={}, headers=headers).status_code, 200)
            self.assertEqual(client.put(base+"/worker", json={"worker_id": None}, headers=headers).status_code, 200)

    def test_migration_preserves_completed_results_and_adopts_legacy_pending_jobs(self):
        from app import schema
        t, r = self.prepared()
        queued = self.db.scalars(select(Job).where(Job.status == "tournament_queued").order_by(Job.id)).all()
        queued[0].status, queued[1].status = "running", "queued"
        queued[0].worker_id, queued[0].claim_token = "chosen", "old-owner"
        running_id, queued_id, rid, tid = queued[0].id, queued[1].id, r.id, t.id
        match = self.db.scalar(select(Match).where(Match.status == "scheduled"))
        match.status, match.detail = "done", '{"retained":true}'
        mid = match.id
        self.db.commit()
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM tournament_actions WHERE kind='worker_queue_guard'"))
            connection.execute(text("ALTER TABLE jobs DROP COLUMN round_id"))
            connection.execute(text("ALTER TABLE tournament_rounds DROP COLUMN worker_id"))
            connection.execute(text("ALTER TABLE worker_nodes DROP COLUMN round_control_version"))
        with patch.object(schema, "engine", self.engine):
            schema.ensure_schema()
            schema.ensure_schema()
        self.db.expire_all()
        self.assertEqual(self.db.get(Job, queued_id).round_id, rid)
        self.assertEqual(self.db.get(Job, queued_id).status, "tournament_queued")
        self.assertEqual((self.db.get(Job, running_id).status, self.db.get(Job, running_id).claim_token),
                         ("running", "old-owner"))
        self.assertEqual((self.db.get(Match, mid).status, self.db.get(Match, mid).detail),
                         ("done", '{"retained":true}'))
        self.assertEqual(self.db.get(WorkerNode, "chosen").round_control_version, 0)
        self.assertIsNone(self.db.get(TournamentRound, rid).worker_id)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(OrganizerAction).where(
            OrganizerAction.tournament_id == tid, OrganizerAction.kind == s.QUEUE_GUARD)), 1)


if __name__ == "__main__":
    unittest.main()
