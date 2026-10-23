"""Daily invariants on a dedicated disposable PostgreSQL database."""
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app import daily as d, main, schema, tournament as t, worker
from app.db import Base
from app.models import DailyControl, DailyDraw, DailyEntry, DailySearch, EloEvent, Job, Match, Player, Submission, TournamentRound


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to disposable PostgreSQL")
class DailyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["TEST_DATABASE_URL"])
        cls.Session = sessionmaker(cls.engine, expire_on_commit=False)
        with patch.object(schema, "engine", cls.engine):
            schema.ensure_schema()
            schema.ensure_schema()  # migration is repeatable

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        with self.engine.begin() as c:
            c.execute(text("TRUNCATE " + ",".join(Base.metadata.tables) + " RESTART IDENTITY CASCADE"))
        self.db = self.Session()
        self.image = patch.dict(os.environ, {"SANDBOX_IMAGE": "sha256:" + "a" * 64})
        self.image.start()
        self.db.add(DailyControl(id=1, enabled=True))
        self.players = [Player(name=f"Daily Team {i}", token_hash="x" * 64) for i in range(4)]
        self.db.add_all(self.players); self.db.flush()

    def tearDown(self):
        self.db.rollback(); self.db.close(); self.image.stop()

    def submit(self, index, ready=True, key=None, queue=True):
        s = d.submit(self.db, self.players[index].id, "python",
                     'def choose_move(state):\n return state["legal_moves"][0]', key or f"day-{index}")
        if ready:
            s.status = "ready"
            self.db.flush()
            for job in self.db.scalars(select(Job).where(Job.kind == "compile")):
                job.status = "done"
            if queue:
                d.find_match(self.db, self.players[index].id, s.id, d.today() + "-search-" + (key or f"day-{index}"))
        return s

    def test_daily_is_disabled_by_default(self):
        self.db.delete(self.db.get(DailyControl, 1)); self.db.flush()
        self.assertFalse(d.enabled(self.db))
        with self.assertRaises(HTTPException): self.submit(0)
        self.assertEqual(d.pair(self.db), [])

    def test_five_submissions_including_failed_and_idempotent_retry(self):
        s = self.submit(0, queue=False)
        self.assertEqual(self.submit(0, queue=False).id, s.id)
        s.status = "failed"
        for i in range(4): self.submit(0, key=f"extra-{i}", queue=False)
        with self.assertRaises(HTTPException): self.submit(0, key="sixth", queue=False)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(DailyEntry)), 5)

    def test_daily_uses_its_own_submission_not_practice(self):
        self.db.add(Submission(player_id=self.players[0].id, language="python", source="practice", status="ready"))
        s = self.submit(0); self.submit(1)
        m = self.db.get(Match, d.pair(self.db)[0])
        self.assertIn(s.id, (m.submission_a, m.submission_b))
        self.assertNotEqual(m.submission_a, m.submission_b)

    def test_pair_once_odd_team_waits_and_opt_out_is_respected(self):
        for i in range(4): self.submit(i)
        d.cancel_search(self.db, self.players[3].id)
        self.assertEqual(len(d.pair(self.db)), 1)
        self.assertEqual(d.pair(self.db), [])
        e = d.entry_for(self.db, self.players[3].id, d.today())
        d.find_match(self.db, self.players[3].id, e.submission_id, "new-search-3")
        self.assertEqual(len(d.pair(self.db)), 1)
        matches = self.db.scalars(select(Match)).all()
        ids = [pid for m in matches for pid in (m.player_a, m.player_b)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_not_ready_or_incompatible_snapshots_are_not_paired(self):
        self.submit(0); s = self.submit(1, ready=False)
        self.assertEqual(d.pair(self.db), [])
        s.status = "ready"
        entry = d.entry_for(self.db, self.players[1].id, d.today())
        entry.environment = t.dump({"image": "other"})
        self.db.flush()
        d.find_match(self.db, self.players[1].id, s.id, "ready-later")
        self.assertEqual(d.pair(self.db), [])

    def test_new_vietnam_day_has_fresh_submission_quota(self):
        self.submit(0)
        with patch.object(d, "today", return_value="2099-01-02"):
            self.submit(0)
            self.assertEqual(d.pair(self.db, "2099-01-01"), [])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(DailyEntry)), 2)
        from app.event_schedule import submission_day_bounds
        from datetime import date
        start, end = submission_day_bounds(date(2026, 10, 23))
        self.assertEqual(start.isoformat(), "2026-10-22T17:00:00+00:00")
        self.assertEqual((end - start).total_seconds(), 86400)

    def test_void_releases_quota_and_reverses_elo(self):
        self.submit(0); self.submit(1)
        m = self.db.get(Match, d.pair(self.db)[0])
        m.status, m.points_a, m.points_b, m.winner_id = "done", 1, 0, m.player_a
        d.after_game(self.db, m); d.after_game(self.db, m)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(EloEvent)), 1)
        self.assertEqual(t.ratings(self.db)[m.player_a], 1016)
        self.assertEqual(d.pair(self.db), [])
        main.mark_match_void(m, "Infrastructure problem")
        d.after_game(self.db, m)
        self.assertEqual(d.leaderboard(self.db), [])
        self.assertEqual(t.ratings(self.db), {})
        self.assertEqual(d.pair(self.db), [])  # no automatic replay/search
        for p in self.players[:2]:
            e = d.entry_for(self.db, p.id, d.today())
            d.find_match(self.db, p.id, e.submission_id, f"after-void-{p.id}")
        self.assertEqual(len(d.pair(self.db)), 1)

    def test_retry_same_match_not_new_quota_or_duplicate_job(self):
        self.submit(0); self.submit(1)
        m = self.db.get(Match, d.pair(self.db)[0]); m.status = "failed"
        self.db.commit()
        with patch.object(d, "SessionLocal", self.Session):
            result = d.retry(m.id, m.player_a)
            self.assertEqual(result["match_id"], m.id)
            with self.assertRaises(HTTPException): d.retry(m.id, m.player_b)
        self.db.expire_all()
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Match)), 1)
        self.assertEqual(d.pair(self.db), [])

    def test_api_auth_toggle_submit_and_opt_in(self):
        self.db.commit()
        patches = [patch.object(d, "SessionLocal", self.Session), patch.object(main, "SessionLocal", self.Session),
                   patch.dict(os.environ, {"ADMIN_API_KEY": "daily-test-admin"})]
        for p in patches: p.start()
        try:
            client = TestClient(main.app)
            self.assertEqual(client.get("/api/daily/state").status_code, 401)
            self.assertEqual(client.put("/api/admin/daily", json={"enabled": True}).status_code, 401)
            auth = {"X-Admin-Key": "daily-test-admin"}
            self.assertEqual(client.put("/api/admin/daily", headers=auth, json={"enabled": True}).status_code, 200)
            main.app.dependency_overrides[d.authorized_player] = lambda: self.players[0].id
            body = {"language": "python", "source": "def choose_move(state): return state['legal_moves'][0]"}
            self.assertEqual(client.post("/api/daily/submissions", json=body).status_code, 400)
            headers = {"Idempotency-Key": "request"}
            a = client.post("/api/daily/submissions", headers=headers, json=body)
            self.assertEqual(a.status_code, 202, a.text)
            self.assertEqual(client.post("/api/daily/submissions", headers=headers, json=body).json(), a.json())
            self.assertEqual(client.put("/api/daily/opt-in", json={"opted_in": False}).status_code, 200)
            self.assertFalse(client.get("/api/daily/state").json()["opted_in"])
        finally:
            main.app.dependency_overrides.clear()
            for p in reversed(patches): p.stop()

    def tournament_encounter(self):
        now = t.utcnow()
        cup = t.create(self.db, dict(name="Busy Cup", max_group_size=2, qualifiers=2,
            registration_open=now-timedelta(days=1), registration_close=now+timedelta(days=1)), "admin")
        for p in self.players[:2]: t.join(self.db, cup, p.id)
        t.lock_roster(self.db, cup, "admin"); t.confirm_groups(self.db, cup, "admin"); self.db.flush()
        r = self.db.scalar(select(TournamentRound))
        t.open_window(self.db, cup, r, now-timedelta(seconds=1), now+timedelta(minutes=1), "admin")
        for p in self.players[:2]:
            link = t.submit(self.db, cup, r, p.id, "python", "def choose_move(s): return s['legal_moves'][0]", f"t-{p.id}")
            self.db.get(Submission, link.submission_id).status = "ready"
        self.db.flush(); t.lock_bots(self.db, cup, r, "admin", force=True)
        self.db.flush()
        return cup, r

    def test_tournament_waiting_blocks_daily_submission_and_pairing(self):
        self.submit(0); self.submit(1)
        self.tournament_encounter()
        self.assertTrue(d.tournament_busy(self.db, [self.players[0].id]))
        self.assertEqual(d.pair(self.db), [])

    def test_new_search_is_rejected_for_tournament_team(self):
        self.tournament_encounter()
        s = self.submit(0, queue=False)
        with self.assertRaises(HTTPException) as caught:
            d.find_match(self.db, self.players[0].id, s.id, "tournament-busy")
        self.assertIn("Tournament", caught.exception.detail)

    def test_infrastructure_compile_retry_preserves_source_and_quota(self):
        s = self.submit(0, ready=False)
        s.status = "infrastructure_error"
        source = s.source
        self.db.commit()
        with patch.object(d, "SessionLocal", self.Session):
            self.assertEqual(d.retry_compile(s.id, self.players[0].id)["submission_id"], s.id)
            with self.assertRaises(HTTPException): d.retry_compile(s.id, self.players[0].id)
        self.db.expire_all()
        self.assertEqual(self.db.get(Submission, s.id).source, source)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(DailyEntry)), 1)

    def test_queued_daily_yields_to_tournament_but_running_daily_finishes(self):
        self.submit(0); self.submit(1)
        m = self.db.get(Match, d.pair(self.db)[0])
        cup, r = self.tournament_encounter()
        m.status = "running"; self.db.flush()
        t.start_batch(self.db, cup, r, None, "admin")
        e = self.db.scalar(select(t.Encounter))
        self.assertEqual(e.status, "pending")
        m.status = "queued"; self.db.flush(); t.pump(self.db); self.db.flush()
        self.assertEqual(e.status, "running")
        for job in self.db.scalars(select(Job).where(Job.kind == "compile")): job.status = "done"
        self.db.flush()
        job = worker.claim(self.db, "test-instance")
        self.assertEqual(self.db.get(Match, json.loads(job.payload)["match_id"]).kind, "tournament")
        job.status = "done"; self.db.flush()
        self.assertIsNone(worker.claim(self.db, "test-instance"))

    def test_concurrent_pairing_does_not_reuse_teams(self):
        for i in range(4): self.submit(i)
        self.db.commit()
        def run():
            with self.Session.begin() as db: return d.pair(db)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: run(), range(2)))
        self.assertEqual(sum(map(len, results)), 2)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Match)), 2)

    def test_worker_uses_frozen_daily_environment_and_rates_once(self):
        self.submit(0); self.submit(1)
        mid = d.pair(self.db)[0]
        job = worker.claim(self.db, "daily-worker-instance")
        jid = job.id
        self.db.commit()
        seen = []
        def play(db, m):
            seen.append(worker.JUDGE_ENV.get()["image"])
            m.status, m.points_a, m.points_b, m.winner_id = "done", 1, 0, m.player_a
        with patch.object(worker, "SessionLocal", self.Session), patch.object(worker, "play_match", side_effect=play), \
             patch.dict(os.environ, {"SANDBOX_IMAGE": "sha256:" + "b" * 64}):
            worker.process(jid, "daily-worker-instance")
            worker.process(jid, "daily-worker-instance")
        self.db.expire_all()
        self.assertEqual(seen, ["sha256:" + "a" * 64])
        self.assertEqual(self.db.get(Match, mid).status, "done")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(EloEvent)), 1)

    def test_concurrent_submission_cannot_bypass_daily_quota(self):
        for i in range(4): self.submit(0, key=f"prefill-{i}", queue=False)
        self.db.commit()
        pid = self.players[0].id
        def run(index):
            try:
                with self.Session.begin() as db:
                    d.submit(db, pid, "python", "def choose_move(s): return s['legal_moves'][0]", f"request-{index}")
                return True
            except HTTPException:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, range(2)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(DailyEntry)), 5)

    def test_draw_keeps_reproducible_pairings_and_sides(self):
        for i in range(3): self.submit(i)
        d.pair(self.db); self.db.flush()
        draw = self.db.scalar(select(DailyDraw))
        ids = json.loads(draw.choices)
        result = json.loads(draw.result)
        planned, waiting = d.pairing_plan(ids, result["history"], draw.seed)
        a, b = planned[0]
        match = result["matches"][0]
        self.assertEqual((match["south"], match["north"]), (a, b))
        self.assertEqual(result["waiting"], waiting)

    def finish(self, mid):
        m = self.db.get(Match, mid)
        m.status, m.points_a, m.points_b, m.winner_id = "done", 1, 0, m.player_a
        d.after_game(self.db, m)
        for job in self.db.scalars(select(Job).where(Job.kind == "match")):
            if json.loads(job.payload)["match_id"] == mid: job.status = "done"
        self.db.flush()
        return m

    def test_submitting_never_searches_automatically(self):
        self.submit(0, queue=False); self.submit(1, queue=False)
        self.assertEqual(d.pair(self.db), [])
        self.assertTrue(d.state(self.db, self.players[0].id)["can_search"])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(DailySearch)), 0)

    def test_policy_only_rules_upgrade_can_pair_without_rewriting_snapshots(self):
        self.submit(0); self.submit(1)
        entries = [d.entry_for(self.db, p.id, d.today()) for p in self.players[:2]]
        revisions = list(d.DAILY_RULES_COMPATIBILITY)
        for entry, revision in zip(entries, revisions): entry.rules_digest = revision
        self.db.flush()
        self.assertEqual(len(d.pair(self.db)), 1)
        self.assertEqual([e.rules_digest for e in entries], revisions)

    def test_five_matches_repeated_opponent_and_no_automatic_requeue(self):
        a = self.submit(0, queue=False); b = self.submit(1, queue=False)
        sides = []
        for i in range(5):
            for p, s in zip(self.players[:2], (a, b)):
                d.find_match(self.db, p.id, s.id, f"find-{p.id}-{i}")
            mid = d.pair(self.db)[0]
            self.assertEqual(d.pair(self.db), [])
            m = self.finish(mid); sides.append(m.player_a)
            self.assertEqual(d.pair(self.db), [])
        self.assertLessEqual(abs(sides.count(self.players[0].id)-sides.count(self.players[1].id)), 1)
        with self.assertRaises(HTTPException) as caught:
            d.find_match(self.db, self.players[0].id, a.id, "sixth-game")
        self.assertEqual(caught.exception.status_code, 429)
        status = d.state(self.db, self.players[0].id)
        self.assertEqual((status["matches_used"], status["matches_remaining"]), (5, 0))

    def test_search_locks_selected_version_even_if_new_bot_is_submitted(self):
        a = self.submit(0); self.submit(1)
        newer = self.submit(0, key="newer", queue=False)
        m = self.db.get(Match, d.pair(self.db)[0])
        self.assertIn(a.id, (m.submission_a, m.submission_b))
        self.assertNotIn(newer.id, (m.submission_a, m.submission_b))
        self.finish(m.id)
        for i, sid in ((0, newer.id), (1, self.submit(1, queue=False).id)):
            d.find_match(self.db, self.players[i].id, sid, f"new-version-{i}")
        next_match = self.db.get(Match, d.pair(self.db)[0])
        self.assertIn(newer.id, (next_match.submission_a, next_match.submission_b))

    def test_search_idempotency_cancel_and_cross_day_expiry(self):
        a = self.submit(0, queue=False)
        first = d.find_match(self.db, self.players[0].id, a.id, "find-once")
        self.assertEqual(d.find_match(self.db, self.players[0].id, a.id, "find-once").id, first.id)
        with self.assertRaises(HTTPException): d.find_match(self.db, self.players[0].id, a.id, "duplicate")
        d.cancel_search(self.db, self.players[0].id); self.db.flush()
        self.assertEqual(d.used_matches(self.db, self.players[0].id, d.today()), 0)
        second = d.find_match(self.db, self.players[0].id, a.id, "find-again")
        with patch.object(d, "today", return_value="2099-01-02"):
            self.assertEqual(d.pair(self.db), [])
            self.assertEqual(second.status, "expired")
            with self.assertRaises(HTTPException): d.find_match(self.db, self.players[0].id, a.id, "old-bot")

    def test_prefers_new_opponents(self):
        planned, waiting = d.pairing_plan([1, 2, 3, 4], [(1, 2), (3, 4)], "seed")
        self.assertEqual(waiting, [])
        for a, b in planned: self.assertNotIn(set((a, b)), [{1, 2}, {3, 4}])

    def test_concurrent_search_cannot_queue_team_twice(self):
        a = self.submit(0, queue=False); self.db.commit()
        def run(i):
            try:
                with self.Session.begin() as db: d.find_match(db, self.players[0].id, a.id, f"concurrent-{i}")
                return True
            except HTTPException: return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sum(executor.map(run, range(2))), 1)

    def test_elo_ranks_above_accumulated_daily_points(self):
        self.submit(0); self.submit(1)
        m = self.finish(d.pair(self.db)[0])
        winner = m.player_a
        self.assertEqual(d.leaderboard(self.db)[0]["player_id"], winner)
        with patch.object(t, "ratings", return_value={m.player_a: 900, m.player_b: 1100}):
            self.assertEqual(d.leaderboard(self.db)[0]["player_id"], m.player_b)

    def test_schema_upgrade_preserves_existing_daily_records(self):
        self.submit(0); self.submit(1)
        mid = d.pair(self.db)[0]; self.finish(mid); self.db.commit()
        with patch.object(schema, "engine", self.engine): schema.ensure_schema()
        self.db.expire_all()
        self.assertEqual(self.db.get(Match, mid).status, "done")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(DailyEntry)), 2)
