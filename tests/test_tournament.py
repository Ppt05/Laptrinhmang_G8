"""Deterministic orchestration on disposable PostgreSQL (never production).

TEST_DATABASE_URL must name a dedicated test database. Games use deterministic
results here; test_worker_flow exercises the actual Docker judge separately.
"""
import json
import os
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select, text, func
from sqlalchemy.orm import sessionmaker

from app import tournament as s
from app.db import Base
from app.models import *


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to disposable PostgreSQL")
class TournamentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["TEST_DATABASE_URL"])
        cls.Session = sessionmaker(cls.engine, expire_on_commit=False)
        from app import schema
        with patch.object(schema,"engine",cls.engine):
            schema.ensure_schema()

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        with self.engine.begin() as conn:
            conn.execute(text("TRUNCATE " + ",".join(Base.metadata.tables) + " RESTART IDENTITY CASCADE"))
        self.db = self.Session()
        self.image = patch.dict(os.environ, {"SANDBOX_IMAGE": "test@sha256:" + "a"*64})
        self.image.start()

    def tearDown(self):
        self.db.rollback()
        self.db.close()
        self.image.stop()

    def make(self, n=14, m=4, q=2):
        now = s.utcnow()
        t = s.create(self.db, dict(name="Test Cup", max_group_size=m, qualifiers=q,
            registration_open=now-timedelta(days=1), registration_close=now+timedelta(days=1),
            appeal_contact="organizer@example.test", incident_policy="Postpone affected round after 24 hours"), "admin")
        for i in range(n):
            p = Player(name=f"Team {i+1}", token_hash="x"*64)
            self.db.add(p)
            self.db.flush()
            s.join(self.db, t, p.id)
        self.db.flush()
        s.lock_roster(self.db, t, "admin")
        s.confirm_groups(self.db, t, "admin")
        self.db.flush()
        r = self.db.scalar(select(TournamentRound).where(TournamentRound.tournament_id == t.id))
        return t, r

    def ready_round(self, t, r, missing=()):
        now = s.utcnow()
        s.open_window(self.db, t, r, now-timedelta(hours=1), now+timedelta(hours=1), "admin")
        for pid in s.participants(self.db, t, r):
            if pid in missing or s.enrollment(self.db,t,pid).status != "enrolled":
                continue
            link = s.submit(self.db, t, r, pid, "python", "pass", "key", now)
            self.db.get(Submission, link.submission_id).status = "ready"
        self.db.flush()
        s.lock_bots(self.db, t, r, "admin", now=r.closes_at+timedelta(seconds=1))
        self.db.flush()

    def run_round(self, t, r, draw_all=False):
        batches = self.db.scalars(select(Encounter.batch).where(Encounter.round_id == r.id).distinct()).all()
        for batch in batches:
            s.start_batch(self.db, t, r, batch, "admin")
        for _ in range(2000):
            self.db.flush()
            active = self.db.scalars(select(Encounter).where(Encounter.status == "running")).all()
            teams = [pid for e in active for pid in (e.player_a, e.player_b)]
            self.assertEqual(len(teams), len(set(teams)), "encounters overlap")
            queued = self.db.scalars(select(Match).where(Match.status == "queued").order_by(Match.id)).all()
            for m in queued:
                e = self.db.get(Encounter, m.encounter_id)
                score = .5 if draw_all else float(m.player_a == min(e.player_a, e.player_b))
                m.points_a, m.points_b = score, 1-score
                m.winner_id = m.player_a if score == 1 else m.player_b if score == 0 else None
                m.status, m.detail = "done", '{"turns":[],"win_reason":"deterministic_test"}'
                for job in self.db.scalars(select(Job).where(Job.kind == "match", Job.status.in_(s.QUEUED_JOBS))):
                    if json.loads(job.payload)["match_id"] == m.id:
                        job.status = "done"
                self.db.flush()
                s.after_game(self.db, m)
            provisional = self.db.scalars(select(Encounter).where(Encounter.round_id == r.id, Encounter.status == "provisional")).all()
            if provisional:
                s.finalize_encounters(self.db, t, r, "admin", now=s.utcnow()+timedelta(hours=1))
            pending = self.db.scalar(select(Encounter.id).where(Encounter.round_id == r.id,
                Encounter.status.not_in(["finalized", "cancelled"])))
            if not pending:
                return
        self.fail("Round did not drain")

    def full_flow(self, n, m=4, q=2):
        t, r = self.make(n, m, q)
        self.ready_round(t, r)
        self.run_round(t, r)
        result = s.qualify(self.db, t, r, "admin")
        self.assertFalse(result["playoffs_required"])
        s.finalize_round(self.db, t, r, "admin", now=s.utcnow()+timedelta(hours=1))
        s.confirm_bracket(self.db, t, "admin")
        while t.status != "complete":
            self.db.flush()
            r = self.db.scalar(select(TournamentRound).where(TournamentRound.tournament_id == t.id).order_by(TournamentRound.level.desc()))
            self.ready_round(t, r)
            self.run_round(t, r)
            s.finalize_round(self.db, t, r, "admin")
        self.assertIsNotNone(t.champion_id)
        return t

    def test_full_14_team_flow(self):
        t = self.full_flow(14)
        self.assertEqual(json.loads(t.allocation)["sizes"], [4,4,3,3])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Encounter).where(Encounter.group_id.is_not(None))), 18)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Encounter).where(Encounter.group_id.is_(None))), 7)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Match)), 78)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(EloEvent)), 25)
        self.assertEqual(len(json.loads(t.bracket)["seeds"]), 8)

    def test_full_two_team_flow_defaults(self):
        t=self.full_flow(2)
        self.assertEqual(json.loads(t.allocation)["sizes"],[2])
        self.assertEqual(len(json.loads(t.bracket)["seeds"]),2)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Encounter)),2)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Match)),8)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(EloEvent)),2)

    def test_two_team_m_equals_q_and_roster_locks_before_deadline(self):
        t,r=self.make(2,2,2)
        self.assertGreater(t.registration_close,s.utcnow())
        self.assertEqual(t.status,"groups")
        self.assertEqual(json.loads(t.allocation)["sizes"],[2])
        outsider=Player(name="Late team",token_hash="z"*64)
        self.db.add(outsider);self.db.flush()
        with self.assertRaises(HTTPException):
            s.join(self.db,t,outsider.id)
        self.ready_round(t,r)
        self.run_round(t,r)
        self.assertFalse(s.qualify(self.db,t,r,"admin")["playoffs_required"])

    def test_10_teams_byes(self):
        t = self.full_flow(10)
        self.assertEqual(json.loads(t.bracket)["size"], 8)
        self.assertEqual(sum(n["bye"] for n in json.loads(t.bracket)["levels"]["1"]), 2)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Encounter).where(Encounter.group_id.is_(None))), 5)

    def test_17_teams_byes(self):
        t = self.full_flow(17)
        self.assertEqual(json.loads(t.bracket)["size"], 16)
        self.assertEqual(sum(n["bye"] for n in json.loads(t.bracket)["levels"]["1"]), 6)

    def test_q_greater_than_two(self):
        t = self.full_flow(12, 6, 3)
        self.assertEqual(len(json.loads(t.bracket)["seeds"]), 6)

    def test_tournament_leaderboard_does_not_mix_rosters_events_or_ratings(self):
        from fastapi.testclient import TestClient
        from app import main
        with patch.object(main,"SessionLocal",self.Session):
            self.assertEqual(TestClient(main.app).get("/api/leaderboard").json(),[])
        t1,r1=self.make(4,4,2)
        e1=Encounter(round_id=r1.id,key="rated-one",player_a=1,player_b=2,playing_result=1,status="finalized")
        self.db.add(e1);self.db.flush();s.rate(self.db,e1)
        t1.status="complete"
        now=s.utcnow()
        t2=s.create(self.db,dict(name="Second Cup",max_group_size=4,qualifiers=2,
            registration_open=now-timedelta(hours=1),registration_close=now+timedelta(days=1)),"admin")
        newcomer=Player(name="Second cup only",token_hash="y"*64)
        outsider=Player(name="Never enrolled",token_hash="z"*64)
        self.db.add_all([newcomer,outsider]);self.db.flush()
        for pid in (1,2,newcomer.id):s.join(self.db,t2,pid)
        s.lock_roster(self.db,t2,"admin");s.confirm_groups(self.db,t2,"admin");self.db.flush()
        r2=self.db.scalar(select(TournamentRound).where(TournamentRound.tournament_id==t2.id))
        e2=Encounter(round_id=r2.id,key="rated-two",player_a=1,player_b=2,playing_result=0,status="finalized")
        ignored=Encounter(round_id=r1.id,key="inactive",player_a=1,player_b=2,playing_result=1,status="cancelled")
        administrative=Encounter(round_id=r2.id,key="forfeit",player_a=1,player_b=newcomer.id,administrative=True,status="finalized")
        self.db.add_all([e2,ignored,administrative]);self.db.flush();s.rate(self.db,e2)
        self.db.add(EloEvent(encounter_id=ignored.id,player_a=1,player_b=2,score=1,active=False))
        tid1,tid2,new_id=t1.id,t2.id,newcomer.id;self.db.commit()
        with patch.object(main,"SessionLocal",self.Session):
            client=TestClient(main.app)
            first=client.get(f"/api/leaderboard?tournament_id={tid1}").json()
            second=client.get(f"/api/leaderboard?tournament_id={tid2}").json()
            self.assertEqual({row["player_id"] for row in first},{1,2,3,4})
            self.assertEqual({row["player_id"] for row in second},{1,2,new_id})
            f={row["player_id"]:row for row in first};g={row["player_id"]:row for row in second}
            self.assertEqual((f[1]["elo"],f[2]["elo"],f[1]["matches"],f[1]["wins"]),(1016,984,1,1))
            self.assertEqual((g[1]["elo"],g[2]["elo"],g[1]["matches"],g[2]["wins"]),(984,1016,1,1))
            self.assertEqual((g[new_id]["elo"],g[new_id]["matches"]),(1000,0))
            self.assertEqual(client.get("/api/leaderboard").json(),second)
            self.assertEqual(client.get("/api/leaderboard?tournament_id=99999").status_code,404)
            self.assertEqual(client.get("/api/leaderboard?tournament_id=invalid").status_code,422)
            event=self.db.scalar(select(EloEvent).where(EloEvent.encounter_id==e1.id));event.score=.5
            s.recalculate_elo(self.db);self.db.commit()
            updated=client.get(f"/api/leaderboard?tournament_id={tid1}").json()
            self.assertTrue(all(row["elo"]==1000 for row in updated))
            self.assertEqual(client.get(f"/api/leaderboard?tournament_id={tid2}").json(),second)

    def test_submission_status_explains_every_block_and_deadline_boundary(self):
        t,r=self.make(4,4,2)
        spectator=Player(name="Spectator",token_hash="z"*64);self.db.add(spectator);self.db.flush()
        def reason(pid=1,round_record=r):
            status=s.team_status(self.db,t,round_record,pid)
            self.assertEqual(status["can_submit"],not bool(status["submission_block_reason"]))
            return status["submission_block_reason"]
        self.assertIn("chưa tham gia",reason(spectator.id))
        self.assertIn("chưa xác nhận",reason(round_record=None))
        self.assertIn("chưa mở",reason())
        now=s.utcnow();s.open_window(self.db,t,r,now+timedelta(minutes=5),now+timedelta(minutes=20),"admin")
        self.assertIn("Chưa tới giờ",reason())
        with patch.object(s,"utcnow",return_value=r.opens_at):self.assertEqual(reason(),"")
        with patch.object(s,"utcnow",return_value=r.closes_at):self.assertEqual(reason(),"")
        with patch.object(s,"utcnow",return_value=r.closes_at+timedelta(microseconds=1)):self.assertIn("hết hạn",reason())
        r.opens_at=now-timedelta(minutes=1)
        for attempt in range(3):s.submit(self.db,t,r,1,"python","pass",f"quota-{attempt}")
        self.db.flush();self.assertIn("3 lượt",reason())
        r.status="locked";self.assertIn("đã khóa",reason())
        r.status="submission";s.enrollment(self.db,t,2).status="withdrawn"
        self.assertIn("đã rút",reason(2))
        ko=TournamentRound(tournament_id=t.id,level=1,status="submission",opens_at=r.opens_at,closes_at=r.closes_at,
                           rules_digest=r.rules_digest,environment=r.environment)
        self.db.add(ko);self.db.flush();t.bracket=s.dump({"levels":{"1":[{"a":1,"b":2}]}})
        self.assertIn("không có suất",reason(3,ko))
        t.status="cancelled";self.assertIn("đã kết thúc",reason())

    def test_configuration(self):
        self.assertEqual(s.configuration(14,4,2), [4,4,3,3])
        self.assertEqual(s.configuration(2,4,2),[2])
        self.assertEqual(s.configuration(2,2,2),[2])
        for args in ((5,4,2), (1,4,2), (2,4,1), (2,1,1), (2,4,3), (14,4,0), (14,4,4), (3,4,1), (8,4,True)):
            with self.assertRaises(HTTPException, msg=str(args)):
                s.configuration(*args)
        self.assertEqual(s.seed_order(8), [1,8,4,5,2,7,3,6])

    def test_quota_idempotency_deadlines_and_latest_ready(self):
        t, r = self.make(4,4,2)
        now = s.utcnow()
        s.open_window(self.db, t, r, now-timedelta(seconds=1), now+timedelta(seconds=1), "admin")
        a = s.submit(self.db, t, r, 1, "python", "one", "a", r.closes_at)
        self.assertEqual(s.submit(self.db, t, r, 1, "python", "one", "a", r.closes_at+timedelta(days=1)).submission_id, a.submission_id)
        with self.assertRaises(HTTPException):
            s.submit(self.db, t, r, 1, "python", "changed", "a", now)
        with self.assertRaises(HTTPException):
            s.submit(self.db, t, r, 2, "python", "late", "late", r.closes_at+timedelta(microseconds=1))
        b = s.submit(self.db, t, r, 1, "python", "two", "b", r.closes_at)
        c = s.submit(self.db, t, r, 1, "python", "three", "c", r.closes_at)
        self.db.flush()
        with self.assertRaises(HTTPException):
            s.submit(self.db, t, r, 1, "python", "four", "d", now)
        with self.assertRaises(HTTPException):
            s.lock_bots(self.db, t, r, "admin", now=r.closes_at+timedelta(seconds=1))
        self.db.get(Submission, a.submission_id).status = "ready"
        self.db.get(Submission, b.submission_id).status = "ready"
        self.db.get(Submission, c.submission_id).status = "failed"
        s.lock_bots(self.db, t, r, "admin", now=r.closes_at+timedelta(seconds=1))
        self.assertEqual(s.bot(self.db, r.id, 1).submission_id, b.submission_id)
        self.assertEqual(len(s.games(self.db, self.db.scalar(select(Encounter)))), 0)

    def test_missing_bots_no_champion(self):
        t, r = self.make(4,4,2)
        self.ready_round(t, r, missing=(1,2,3,4))
        for e in self.db.scalars(select(Encounter)):
            self.assertTrue(e.administrative)
            self.assertEqual((e.points_a,e.points_b,e.game_points_a,e.game_points_b), (0,0,0,0))
        self.run_round(t, r)
        s.qualify(self.db,t,r,"admin")
        s.finalize_round(self.db,t,r,"admin",now=s.utcnow()+timedelta(hours=1))
        s.confirm_bracket(self.db,t,"admin")
        self.assertEqual((t.status,t.result,t.champion_id), ("complete","no_champion",None))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(EloEvent)), 0)

    def test_sole_eligible_qualifier_is_champion_without_fake_final(self):
        t,r=self.make(4,4,2)
        self.ready_round(t,r,missing=(2,3,4))
        self.run_round(t,r)
        s.qualify(self.db,t,r,"admin")
        s.finalize_round(self.db,t,r,"admin",now=s.utcnow()+timedelta(hours=1))
        s.confirm_bracket(self.db,t,"admin")
        self.assertEqual((t.status,t.result,t.champion_id),("complete","sole_eligible_qualifier",1))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Match)),0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(EloEvent)),0)

    def test_cancel_entire_round_cancels_queued_jobs_and_awards_no_champion(self):
        t,r=self.make(4,4,2)
        self.ready_round(t,r)
        s.start_batch(self.db,t,r,1,"admin")
        self.assertTrue(self.db.scalar(select(Match.id).where(Match.status=="queued")))
        s.exception(self.db,t,r,"cancel",{"reason":"Infrastructure outage cancels entire round","scope":"round"},"admin")
        self.assertEqual((t.status,r.status,t.champion_id),("cancelled","cancelled",None))
        self.assertTrue(all(m.status=="void" for m in self.db.scalars(select(Match))))
        self.assertTrue(all(j.status=="cancelled" for j in self.db.scalars(select(Job).where(Job.kind=="match"))))

    def test_close_tournament_api_registration_auth_idempotency_and_new_event(self):
        from fastapi.testclient import TestClient
        from app import main,tournament_api
        now=s.utcnow()
        values=dict(name="Close API Cup",max_group_size=4,qualifiers=2,
            registration_open=now-timedelta(hours=1),registration_close=now+timedelta(days=1),
            appeal_contact="admin@example.test",incident_policy="Wait for infrastructure repair")
        t=s.create(self.db,values,"admin");tid=t.id;self.db.commit()
        headers={"X-Admin-Key":"close-test-key"};reason="Organizer ends this test tournament"
        with patch.object(main,"SessionLocal",self.Session),patch.object(tournament_api,"SessionLocal",self.Session),patch.dict(os.environ,{"ADMIN_API_KEY":"close-test-key"}):
            client=TestClient(main.app);url=f"/api/admin/tournaments/{tid}/close"
            self.assertEqual(client.post(url,json={"reason":reason}).status_code,401)
            self.assertEqual(client.post(url,json={"reason":"x"*2001},headers=headers).status_code,422)
            self.assertEqual(client.post(url,json={"reason":[]},headers=headers).status_code,422)
            response=client.post(url,json={"reason":reason},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()["result"],"closed_by_organizer")
            public=client.get(f"/api/tournaments/{tid}").json()
            self.assertEqual((public["status"],public["champion_id"]),("cancelled",None))
            self.assertEqual(public["notices"][-1]["reason"],reason)
            self.assertEqual(client.post(url,json={"reason":reason},headers=headers).status_code,200)
            actions=client.get(f"/api/admin/tournaments/{tid}/audit",headers=headers).json()["actions"]
            self.assertEqual(sum(a["kind"]=="close_tournament" for a in actions),1)
            response=client.post("/api/admin/tournaments",json=json.loads(s.dump(values)),headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertNotEqual(response.json()["id"],tid)
            self.assertEqual(len(client.get("/api/tournaments").json()),2)

    def test_optional_configuration_text_and_close_reason_api(self):
        from fastapi.testclient import TestClient
        from app import main,tournament_api
        self.db.commit()
        now=s.utcnow()
        dates=dict(registration_open=(now-timedelta(hours=1)).isoformat(),
                   registration_close=(now+timedelta(days=1)).isoformat())
        headers={"X-Admin-Key":"optional-test-key"}
        with patch.object(main,"SessionLocal",self.Session),patch.object(tournament_api,"SessionLocal",self.Session),patch.dict(os.environ,{"ADMIN_API_KEY":"optional-test-key"}):
            client=TestClient(main.app)
            for text_values,close_values in (({},{}),({"name":"","appeal_contact":"","incident_policy":""},{"reason":"   "}),
                                             ({"name":"X","appeal_contact":"x","incident_policy":"x"},{"reason":"x"})):
                response=client.post("/api/admin/tournaments",json={**dates,**text_values},headers=headers)
                self.assertEqual(response.status_code,200,response.text)
                tid=response.json()["id"];url=f"/api/admin/tournaments/{tid}"
                public=client.get(f"/api/tournaments/{tid}").json()
                self.assertEqual(public["appeal_contact"],text_values.get("appeal_contact",""))
                self.assertEqual(public["incident_policy"],text_values.get("incident_policy",""))
                response=client.put(url+"/configuration",json={**dates,"name":"","appeal_contact":"","incident_policy":""},headers=headers)
                self.assertEqual(response.status_code,200,response.text)
                response=client.post(url+"/close",json=close_values,headers=headers)
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(client.post(url+"/close",headers=headers).status_code,200)
                actions=client.get(url+"/audit",headers=headers).json()["actions"]
                closed=[a for a in actions if a["kind"]=="close_tournament"]
                self.assertEqual(len(closed),1)
                self.assertEqual(closed[0]["evidence"]["reason"],close_values.get("reason","").strip())

    def test_exception_reason_and_evidence_are_optional_api(self):
        from fastapi.testclient import TestClient
        from app import main,tournament_api
        t,r=self.make(4,4,2);tid,rid=t.id,r.id;self.db.commit()
        headers={"X-Admin-Key":"optional-test-key"}
        with patch.object(main,"SessionLocal",self.Session),patch.object(tournament_api,"SessionLocal",self.Session),patch.dict(os.environ,{"ADMIN_API_KEY":"optional-test-key"}):
            client=TestClient(main.app);url=f"/api/admin/tournaments/{tid}/rounds/{rid}/exceptions"
            self.assertEqual(client.post(url+"/hold",json={}).status_code,401)
            response=client.post(url+"/hold",json={},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            action_id=response.json()["action_id"]
            response=client.post(url+"/decision",json={"action_id":action_id},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(client.post(url+"/forfeit",json={},headers=headers).status_code,409)
            response=client.post(url+"/disqualify",json={"player_id":1},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            response=client.post(f"/api/admin/tournaments/{tid}/enrollments/2/withdraw",headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            public=client.get(f"/api/tournaments/{tid}").json()
            statuses={p["player_id"]:p["status"] for p in public["roster"]}
            self.assertEqual((statuses[1],statuses[2]),("disqualified","withdrawn"))
            actions=client.get(f"/api/admin/tournaments/{tid}/audit",headers=headers).json()["actions"]
            for action in actions:
                if action["kind"] in ("hold","decision","disqualify","withdraw"):
                    self.assertFalse(action["evidence"].get("reason"))
                    self.assertFalse(action["evidence"].get("evidence"))

    def test_legacy_void_reason_is_optional_api(self):
        from fastapi.testclient import TestClient
        from app import main
        t,r=self.make(2,4,2)
        submissions=[Submission(player_id=pid,language="python",source="pass",status="ready") for pid in (1,2)]
        self.db.add_all(submissions);self.db.flush()
        match=Match(player_a=1,player_b=2,submission_a=submissions[0].id,submission_b=submissions[1].id,
                    event_day=s.utcnow().date().isoformat(),kind="daily",status="done",detail="{}")
        self.db.add(match);self.db.flush();mid=match.id;self.db.commit()
        with patch.object(main,"SessionLocal",self.Session),patch.dict(os.environ,{"ADMIN_API_KEY":"optional-test-key"}):
            client=TestClient(main.app)
            response=client.post(f"/api/admin/matches/{mid}/void",json={},headers={"X-Admin-Key":"optional-test-key"})
            self.assertEqual(response.status_code,200,response.text)
        self.db.expire_all()
        match=self.db.get(Match,mid)
        self.assertEqual(match.status,"void")
        self.assertEqual(json.loads(match.detail)["void_reason"],"")

    def test_close_tournament_preserves_finalized_round_replays_elo_and_unrelated_jobs(self):
        t,r=self.knockout()
        ratings=s.ratings(self.db)
        group=self.db.scalar(select(TournamentRound).where(TournamentRound.level==0))
        group_games={m.id:m.detail for m in self.db.scalars(select(Match).join(Encounter,Match.encounter_id==Encounter.id).where(Encounter.round_id==group.id))}
        s.start_batch(self.db,t,r,None,"admin")
        played=self.db.scalar(select(Match).join(Encounter,Match.encounter_id==Encounter.id).where(Encounter.round_id==r.id,Match.status=="queued"))
        played.status,played.points_a,played.points_b,played.detail="done",1,0,'{"turns":[],"win_reason":"test"}'
        for job in self.db.scalars(select(Job).where(Job.kind=="match")):
            if json.loads(job.payload)["match_id"]==played.id:job.status="done"
        self.db.flush();s.after_game(self.db,played);self.db.flush()
        unrelated=Submission(player_id=1,language="python",source="unrelated",status="queued")
        self.db.add(unrelated);self.db.flush()
        job=Job(kind="compile",payload=s.dump({"submission_id":unrelated.id}),status="queued")
        self.db.add(job);self.db.flush()
        count=self.db.scalar(select(func.count()).select_from(Match))
        s.close_tournament(self.db,t,"admin","Organizer stops remaining knockout games")
        self.db.flush()
        self.assertEqual((t.status,t.champion_id,r.status,group.status),("cancelled",None,"cancelled","finalized"))
        self.assertEqual(s.ratings(self.db),ratings)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Match)),count)
        self.assertEqual((played.status,played.detail),("done",'{"turns":[],"win_reason":"test"}'))
        for mid,detail in group_games.items():
            self.assertEqual((self.db.get(Match,mid).status,self.db.get(Match,mid).detail),("done",detail))
        self.assertTrue(all(e.status=="finalized" for e in self.db.scalars(select(Encounter).where(Encounter.round_id==group.id))))
        self.assertEqual((job.status,unrelated.status),("queued","queued"))
        self.assertFalse(self.db.scalar(select(Match.id).where(Match.encounter_id.in_(select(Encounter.id).where(Encounter.round_id==r.id)),Match.status.in_(["queued","scheduled"])) ))

    def test_close_tournament_cancels_queued_compilation_and_preserves_champion(self):
        t,r=self.make(2,4,2)
        now=s.utcnow()
        s.open_window(self.db,t,r,now-timedelta(hours=1),now+timedelta(hours=1),"admin")
        link=s.submit(self.db,t,r,1,"python","pass","queued-close")
        self.db.flush()
        s.close_tournament(self.db,t,"admin","Organizer closes submission window permanently")
        self.db.flush()
        self.assertEqual(self.db.get(Submission,link.submission_id).status,"cancelled")
        self.assertTrue(all(j.status=="cancelled" for j in self.db.scalars(select(Job))))
        # A separate completed tournament remains unchanged when closed again.
        completed=s.create(self.db,dict(name="Completed Cup",max_group_size=4,qualifiers=2,
            registration_open=now-timedelta(hours=1),registration_close=now+timedelta(hours=1),
            appeal_contact="admin@example.test",incident_policy="Wait for infrastructure repair"),"admin")
        completed.status,completed.champion_id,completed.result="complete",1,"champion"
        s.close_tournament(self.db,completed,"admin","Already complete event remains complete")
        self.assertEqual((completed.status,completed.champion_id,completed.result),("complete",1,"champion"))

    def test_close_provisional_tournament_does_not_block_same_teams_in_new_event(self):
        t,r=self.make(2,4,2);self.ready_round(t,r);s.start_batch(self.db,t,r,None,"admin")
        for _ in range(2):
            match=self.db.scalar(select(Match).where(Match.status=="queued"))
            match.status="done";match.points_a=1;match.points_b=0
            self.db.flush();s.after_game(self.db,match);self.db.flush()
        old=self.db.scalar(select(Encounter))
        self.assertEqual(old.status,"provisional")
        s.close_tournament(self.db,t,"admin","Organizer closes event during appeal period")
        now=s.utcnow()
        replacement=s.create(self.db,dict(name="Same Teams New Cup",max_group_size=4,qualifiers=2,
            registration_open=now-timedelta(hours=1),registration_close=now+timedelta(days=1),
            appeal_contact="admin@example.test",incident_policy="Wait for infrastructure repair"),"admin")
        for pid in (1,2):s.join(self.db,replacement,pid)
        s.lock_roster(self.db,replacement,"admin");s.confirm_groups(self.db,replacement,"admin");self.db.flush()
        new_round=self.db.scalar(select(TournamentRound).where(TournamentRound.tournament_id==replacement.id))
        self.ready_round(replacement,new_round)
        s.start_batch(self.db,replacement,new_round,None,"admin");self.db.flush()
        encounter=self.db.scalar(select(Encounter).where(Encounter.round_id==new_round.id))
        self.assertEqual(encounter.status,"running")
        self.assertEqual([g.status for g in s.games(self.db,encounter)],["queued","scheduled"])
        self.assertEqual(old.status,"provisional")  # Original completed results stay intact.

    def test_close_tournament_rejects_running_jobs_and_worker_claim_lock(self):
        from fastapi.testclient import TestClient
        from app import main,tournament_api
        t,r=self.make(2,4,2);self.ready_round(t,r);s.start_batch(self.db,t,r,None,"admin")
        self.db.flush()
        job=self.db.scalar(select(Job).where(Job.kind=="match"));jid=job.id
        match=self.db.get(Match,json.loads(job.payload)["match_id"])
        match.status="running";tid=t.id;self.db.commit()
        with patch.object(main,"SessionLocal",self.Session),patch.object(tournament_api,"SessionLocal",self.Session),patch.dict(os.environ,{"ADMIN_API_KEY":"close-lock-key"}):
            client=TestClient(main.app);headers={"X-Admin-Key":"close-lock-key"}
            def close():return client.post(f"/api/admin/tournaments/{tid}/close",json={"reason":"Organizer closes event after worker finishes"},headers=headers)
            self.assertEqual(close().status_code,409)
            match.status="queued";job.status="running";self.db.commit()
            self.assertEqual(close().status_code,409)
            job.status="queued";self.db.commit()
            with self.engine.connect() as connection:
                transaction=connection.begin()
                connection.execute(text("SELECT id FROM jobs WHERE id=:id FOR UPDATE"),{"id":jid})
                self.assertEqual(close().status_code,409)
                transaction.rollback()
            self.assertEqual(client.get(f"/api/tournaments/{tid}").json()["status"],"groups")
            self.assertEqual(close().status_code,200)

    def test_ranking_playoff_exactly_once_then_recorded_draw(self):
        t, r = self.make(4,4,2)
        self.ready_round(t,r)
        self.run_round(t,r,draw_all=True)
        self.assertTrue(s.qualify(self.db,t,r,"admin")["playoffs_required"])
        self.db.flush()
        count = self.db.scalar(select(func.count()).select_from(Encounter))
        self.assertTrue(s.qualify(self.db,t,r,"admin")["playoffs_required"])
        self.assertEqual(count,12)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Encounter)),count)
        self.run_round(t,r,draw_all=True)
        self.assertFalse(s.qualify(self.db,t,r,"admin")["playoffs_required"])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Encounter)),12)
        self.assertTrue(self.db.scalar(select(RecordedDraw.id).where(RecordedDraw.key.like("playoff-final:%"))))

    def knockout(self):
        t,r = self.make(4,4,2)
        self.ready_round(t,r)
        self.run_round(t,r)
        s.qualify(self.db,t,r,"admin")
        s.finalize_round(self.db,t,r,"admin",now=s.utcnow()+timedelta(hours=1))
        s.confirm_bracket(self.db,t,"admin")
        self.db.flush()
        kr = self.db.scalar(select(TournamentRound).where(TournamentRound.level == 1))
        self.ready_round(t,kr)
        return t,kr

    def test_ten_games_lottery_draw_elo_and_sides(self):
        t,r = self.knockout()
        self.run_round(t,r,draw_all=True)
        e = self.db.scalar(select(Encounter).where(Encounter.round_id == r.id))
        rows = s.games(self.db,e)
        self.assertEqual(len(rows),10)
        self.assertEqual(e.playing_result,.5)
        self.assertEqual(json.loads(e.extra_scores),[[1,1],[1,1]])
        self.assertIn(e.winner_id,[e.player_a,e.player_b])
        self.assertNotEqual(rows[0].player_a, rows[6].player_a)
        self.assertEqual(rows[0].player_a, rows[8].player_a)
        self.assertEqual(self.db.scalar(select(EloEvent).where(EloEvent.encounter_id == e.id)).score,.5)

    def test_eight_games_when_first_extra_pair_decides(self):
        t,r = self.knockout()
        s.start_batch(self.db,t,r,1,"admin")
        e = self.db.scalar(select(Encounter).where(Encounter.round_id == r.id))
        for _ in range(8):
            self.db.flush()
            m = self.db.scalar(select(Match).where(Match.encounter_id == e.id, Match.status == "queued"))
            score = .5 if m.game_number <= 6 else float(m.player_a == e.player_a)
            m.points_a,m.points_b,m.status = score,1-score,"done"
            self.db.flush()
            s.after_game(self.db,m)
        self.assertEqual(e.status,"provisional")
        self.assertEqual(len(s.games(self.db,e)),8)
        self.assertEqual(e.winner_id,e.player_a)

    def test_appeal_gate_hold_and_decision(self):
        t,r = self.make(4,4,2)
        self.ready_round(t,r)
        self.run_round(t,r)
        s.qualify(self.db,t,r,"admin")
        with self.assertRaises(HTTPException):
            s.finalize_round(self.db,t,r,"admin")
        result = s.exception(self.db,t,r,"hold",{"reason":"Player reported valid appeal","evidence":"mail archive"},"admin")
        self.db.flush()
        with self.assertRaises(HTTPException):
            s.finalize_round(self.db,t,r,"admin",now=s.utcnow()+timedelta(hours=1))
        s.exception(self.db,t,r,"decision",{"reason":"Appeal reviewed and rejected","action_id":result["action_id"]},"admin")
        s.finalize_round(self.db,t,r,"admin",now=s.utcnow()+timedelta(hours=1))

    def test_elo_correction_recalculates_dependents(self):
        t,r = self.make(4,4,2)
        self.ready_round(t,r)
        self.run_round(t,r)
        events = self.db.scalars(select(EloEvent).order_by(EloEvent.id)).all()
        first = self.db.get(Encounter,events[0].encounter_id)
        dependent = next(event for event in events[1:] if {event.player_a,event.player_b} & {first.player_a,first.player_b})
        original = (float(dependent.before_a),float(dependent.before_b))
        corrected_winner = first.player_b if first.winner_id == first.player_a else first.player_a
        for m in s.games(self.db,first):
            s.exception(self.db,t,r,"correct_game",{"reason":"Verified corrected game outcome","encounter_id":first.id,
                        "match_id":m.id,"south_score":float(m.player_a == corrected_winner)},"admin")
        self.assertNotEqual((float(dependent.before_a),float(dependent.before_b)),original)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(EloEvent)),6)
        old_until = first.appeal_until
        s.after_game(self.db,s.games(self.db,first)[0])
        self.assertEqual(first.appeal_until,old_until)

    def test_forfeit_and_withdraw_preserve_played_elo(self):
        t,r = self.make(4,4,2)
        self.ready_round(t,r)
        self.run_round(t,r)
        e = self.db.scalar(select(Encounter))
        s.exception(self.db,t,r,"forfeit",{"reason":"Both teams explicitly forfeit","encounter_id":e.id,
                                          "player_ids":[e.player_a,e.player_b]},"admin")
        self.assertEqual((e.points_a,e.points_b,e.game_points_a,e.game_points_b),(0,0,0,0))
        self.assertFalse(self.db.scalar(select(EloEvent).where(EloEvent.encounter_id == e.id)).active)
        count = self.db.scalar(select(func.count()).select_from(EloEvent).where(EloEvent.active.is_(True)))
        s.exception(self.db,t,r,"withdraw",{"reason":"Team permanently withdraws","player_id":1},"admin")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(EloEvent).where(EloEvent.active.is_(True))),count)
        self.assertTrue(all(e.excluded for e in self.db.scalars(select(Encounter).where((Encounter.player_a == 1)|(Encounter.player_b == 1)))))

    def test_bot_inheritance_ignores_other_modes(self):
        t,r = self.knockout()
        # Rebuild the planned KO round before bot lock for the inheritance case.
        for e in self.db.scalars(select(Encounter).where(Encounter.round_id == r.id)):
            for m in s.games(self.db,e):
                self.db.delete(m)
            self.db.flush()
            self.db.delete(e)
        self.db.query(LockedBot).filter(LockedBot.round_id == r.id).delete()
        self.db.query(RoundSubmission).filter(RoundSubmission.round_id == r.id).delete()
        r.status = "submission"
        for pid in s.participants(self.db,t,r):
            self.db.add(Submission(player_id=pid,language="python",source="unrelated",status="ready"))
        self.db.flush()
        s.lock_bots(self.db,t,r,"admin",now=r.closes_at+timedelta(seconds=1))
        for pid in s.participants(self.db,t,r):
            self.assertEqual(s.bot(self.db,r.id,pid).reason,"inherited_compatible")

    def test_concurrent_submissions_quota_and_same_request(self):
        t,r = self.make(4,4,2)
        now=s.utcnow()
        s.open_window(self.db,t,r,now-timedelta(hours=1),now+timedelta(hours=1),"admin")
        tid,rid=t.id,r.id
        self.db.commit()
        def request(key):
            try:
                with self.Session.begin() as db:
                    s.serialize(db)
                    return s.submit(db,db.get(Tournament,tid),db.get(TournamentRound,rid),1,"python","pass",key).submission_id
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(max_workers=8) as pool:
            same=list(pool.map(request,["same"]*8))
        self.assertEqual(len(set(same)),1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            different=list(pool.map(request,[str(i) for i in range(8)]))
        self.assertEqual(different.count(429),6)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(RoundSubmission)),3)

    def test_failed_game_retry_keeps_completed_games_and_attempts(self):
        from app import worker
        t,r=self.make(4,4,2)
        self.ready_round(t,r)
        s.start_batch(self.db,t,r,1,"admin")
        e=self.db.scalar(select(Encounter).where(Encounter.status=="running").order_by(Encounter.id))
        first=s.games(self.db,e)[0]
        first.points_a,first.points_b,first.status,first.detail=1,0,"done",'{"turns":[]}'
        self.db.flush()
        s.after_game(self.db,first)
        second=s.games(self.db,e)[1]
        second.status,second.detail="failed",'{"error":"infrastructure"}'
        self.db.flush()
        s.after_game(self.db,second)
        self.assertEqual(e.status,"paused")
        for job in self.db.scalars(select(Job).where(Job.kind=="match")):
            if json.loads(job.payload)["match_id"] in (first.id,second.id): job.status="done"
        s.exception(self.db,t,r,"retry",{"reason":"Docker service restored","match_id":second.id},"admin")
        self.assertEqual(first.status,"done")
        self.assertEqual(second.status,"queued")
        eid,mid=e.id,second.id
        self.db.commit()
        def deterministic(db,m):
            m.status,m.points_a,m.points_b,m.detail="done",0,1,'{"turns":[]}'
        with patch.object(worker,"SessionLocal",self.Session),patch.object(worker,"WORKER_ID","test-worker"),patch.object(worker,"play_match",deterministic):
            with self.Session.begin() as db:
                # Discard fixture compilation jobs, then exercise actual claim.
                for j in db.scalars(select(Job).where(Job.kind=="compile")):j.status="done"
                for j in db.scalars(select(Job).where(Job.kind=="match",Job.status.in_(s.QUEUED_JOBS))):
                    if json.loads(j.payload)["match_id"] != mid:j.status="done"
                job=worker.claim(db,"owner")
                jid=job.id
            worker.process(jid,"owner")
            worker.process(jid,"owner")
        self.db.expire_all()
        self.assertEqual(self.db.get(Encounter,eid).status,"provisional")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(EloEvent).where(EloEvent.encounter_id==eid)),1)
        attempt=self.db.scalar(select(GameAttempt).where(GameAttempt.match_id==mid))
        self.assertEqual(attempt.status,"done")

    def test_worker_restart_and_lost_ownership(self):
        from app import worker
        t,r=self.make(4,4,2)
        self.ready_round(t,r)
        s.start_batch(self.db,t,r,1,"admin")
        for j in self.db.scalars(select(Job).where(Job.kind=="compile")):j.status="done"
        with patch.object(worker,"WORKER_ID","test-worker"):
            job=worker.claim(self.db,"old")
            self.db.add(WorkerNode(id="test-worker",instance_token="new",last_seen=s.utcnow()))
            self.db.flush()
            worker.recover_abandoned_jobs(self.db)
            self.assertEqual(job.status,"tournament_queued")
            attempt=self.db.scalar(select(GameAttempt).where(GameAttempt.match_id==json.loads(job.payload)["match_id"]))
            self.assertEqual(attempt.status,"abandoned")
            new=worker.claim(self.db,"new")
            self.assertEqual(new.id,job.id)
            self.assertEqual(new.attempts,2)
            jid=job.id
            worker.release_claim(self.db,jid,"new")
            self.assertEqual(job.status,"tournament_queued")
            second_attempt=self.db.scalar(select(GameAttempt).where(GameAttempt.match_id==attempt.match_id,GameAttempt.attempt==2))
            self.assertEqual(second_attempt.status,"abandoned")
            worker.claim(self.db,"new")
            self.assertEqual(job.attempts,3)
            self.db.commit()
            with patch.object(worker,"SessionLocal",self.Session),patch.object(worker,"play_match") as play:
                worker.process(jid,"old")
                play.assert_not_called()

    def test_real_attempt_history_survives_organizer_retry_new_job(self):
        from app import worker
        t,r=self.make(4,4,2)
        self.ready_round(t,r)
        s.start_batch(self.db,t,r,1,"admin")
        target=self.db.scalar(select(Match).where(Match.status=="queued").order_by(Match.id))
        for j in self.db.scalars(select(Job).where(Job.status.in_(s.QUEUED_JOBS))):
            if j.kind!="match" or json.loads(j.payload)["match_id"]!=target.id:j.status="done"
        mid,tid,rid=target.id,t.id,r.id
        self.db.commit()
        def failed(db,m):m.status,m.detail="failed",'{"error":"lost Docker daemon"}'
        def done(db,m):m.status,m.points_a,m.points_b,m.detail="done",1,0,'{"turns":[]}'
        with patch.object(worker,"SessionLocal",self.Session),patch.object(worker,"WORKER_ID","retry-worker"):
            for index,play in enumerate((failed,done)):
                with self.Session.begin() as db:
                    job=worker.claim(db,"owner")
                    jid=job.id
                with patch.object(worker,"play_match",play):worker.process(jid,"owner")
                if index==0:
                    with self.Session.begin() as db:
                        s.serialize(db)
                        s.exception(db,db.get(Tournament,tid),db.get(TournamentRound,rid),"retry",
                                    {"reason":"Docker daemon restored","match_id":mid},"admin")
        self.db.expire_all()
        history=self.db.scalars(select(GameAttempt).where(GameAttempt.match_id==mid).order_by(GameAttempt.attempt)).all()
        self.assertEqual([(a.attempt,a.status) for a in history],[(1,"failed"),(2,"done")])

    def test_withdraw_after_group_finalization_replacement_before_bracket(self):
        t,r=self.make(8,4,2)
        self.ready_round(t,r); self.run_round(t,r); s.qualify(self.db,t,r,"admin")
        s.finalize_round(self.db,t,r,"admin",now=s.utcnow()+timedelta(hours=1))
        group=self.db.scalar(select(TournamentGroup).order_by(TournamentGroup.id))
        order=json.loads(group.final_order)
        s.exception(self.db,t,r,"withdraw",{"reason":"Withdrawal before bracket confirmation","player_id":order[0]},"admin")
        s.confirm_bracket(self.db,t,"admin")
        seeds=json.loads(t.bracket)["seeds"]
        self.assertNotIn(order[0],seeds)
        self.assertIn(order[2],seeds)
        self.assertEqual(json.loads(group.final_order),order)

    def test_withdraw_before_bot_lock_removes_opponent_points_and_denominators(self):
        t,r=self.make(4,4,2)
        s.exception(self.db,t,r,"withdraw",{"reason":"Withdrawal before bot window","player_id":1},"admin")
        self.ready_round(t,r)
        withdrawn=self.db.scalars(select(Encounter).where((Encounter.player_a==1)|(Encounter.player_b==1))).all()
        self.assertEqual(len(withdrawn),3)
        self.assertTrue(all(e.excluded and e.status=="cancelled" for e in withdrawn))
        self.run_round(t,r)
        group=self.db.scalar(select(TournamentGroup))
        order,rows,waiting=s.resolve_group(self.db,t,r,group,"admin")
        self.assertNotIn(1,order)
        self.assertEqual([row["encounters"] for row in rows.values()],[2,2,2])

    def test_discipline_has_separate_appeal_gate(self):
        t,r=self.make(4,4,2)
        s.exception(self.db,t,r,"disqualify",{"reason":"Identity fraud verified by organizer","evidence":"signed verification report","player_id":1},"admin")
        self.assertTrue(s.held(self.db,t,r))
        action=self.db.scalar(select(OrganizerAction).where(OrganizerAction.kind=="disqualify"))
        self.assertIsNotNone(action.appeal_until)
        self.assertIsNone(action.round_id)

    def test_vacant_knockout_branches_and_no_champion(self):
        t,r=self.knockout()
        for pid in s.participants(self.db,t,r):
            s.exception(self.db,t,r,"withdraw",{"reason":"Both finalists withdraw from tournament","player_id":pid},"admin")
        self.run_round(t,r)
        s.finalize_round(self.db,t,r,"admin")
        self.assertEqual(t.result,"no_champion")

    def test_public_view_does_not_expose_sources_or_private_evidence(self):
        t,r=self.make(4,4,2)
        self.ready_round(t,r)
        s.exception(self.db,t,r,"hold",{"reason":"Wait for data review","evidence":"private-evidence-secret"},"admin")
        data=s.public_view(self.db,t)
        self.assertEqual(len(data["groups"]),1)
        self.assertNotIn('"source"',json.dumps(data,default=str))
        self.assertEqual(data["notices"][0]["reason"],"Wait for data review")
        self.assertNotIn("private-evidence-secret",json.dumps(data,default=str))

    def test_auto_batch_api_serializes_concurrent_requests_and_stops_at_end(self):
        from fastapi.testclient import TestClient
        from app import main,tournament_api
        t,r=self.make(4,4,2)
        self.ready_round(t,r)
        tid,rid=t.id,r.id
        self.db.commit()
        with patch.object(main,"SessionLocal",self.Session),patch.object(tournament_api,"SessionLocal",self.Session),patch.dict(os.environ,{"ADMIN_API_KEY":"auto-batch-key"}):
            client=TestClient(main.app)
            url=f"/api/admin/tournaments/{tid}/rounds/{rid}/start-batch"
            headers={"X-Admin-Key":"auto-batch-key"}
            self.assertEqual(client.post(url,json={}).status_code,401)
            self.assertEqual(client.post(url,json={"batch":0},headers=headers).status_code,422)
            def start():
                response=client.post(url,json={},headers=headers)
                self.assertEqual(response.status_code,200,response.text)
                return response.json()["batch"]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:start(),range(2)))
            self.assertEqual(sorted(results),[1,2])
            self.assertEqual(start(),3)
            response=client.post(url,json={},headers=headers)
            self.assertEqual(response.status_code,409,response.text)
        self.db.expire_all()
        started=[json.loads(a.evidence)["batch"] for a in self.db.scalars(select(OrganizerAction).where(OrganizerAction.kind=="start_batch").order_by(OrganizerAction.id))]
        self.assertEqual(started,[1,2,3])
        self.assertEqual(set(self.db.scalars(select(Encounter.status))),{"running","pending"})
        jobs=self.db.scalars(select(Job).where(Job.kind=="match")).all()
        self.assertEqual(len(jobs),2)  # One game for each encounter in first batch.
        self.assertEqual(len({json.loads(j.payload)["match_id"] for j in jobs}),2)

    def test_auto_batch_selects_new_playoff_after_finished_group_batch(self):
        t,r=self.make(2,4,2)
        self.ready_round(t,r)
        self.run_round(t,r,draw_all=True)
        self.assertTrue(s.qualify(self.db,t,r,"admin")["playoffs_required"])
        self.db.flush()
        self.assertEqual(s.start_batch(self.db,t,r,None,"admin"),2)
        self.db.flush()
        main=self.db.scalar(select(Encounter).where(Encounter.round_id==r.id,Encounter.playoff.is_(None)))
        playoff=self.db.scalar(select(Encounter).where(Encounter.round_id==r.id,Encounter.playoff.is_not(None)))
        self.assertEqual(main.status,"finalized")
        self.assertEqual(playoff.status,"running")
        self.assertEqual([m.status for m in s.games(self.db,playoff)],["queued","scheduled"])
        with self.assertRaises(HTTPException):
            s.start_batch(self.db,t,r,None,"admin")

    def test_force_stages_api_two_team_flow_and_normal_gates(self):
        from fastapi.testclient import TestClient
        from app import main,tournament_api,security
        from app.security import digest
        now=s.utcnow()
        t=s.create(self.db,dict(name="Force Cup",max_group_size=4,qualifiers=2,
            registration_open=now-timedelta(hours=1),registration_close=now+timedelta(days=100),
            appeal_contact="admin@example.test",incident_policy="Wait for infrastructure repair"),"admin")
        for i in range(2):
            p=Player(name=f"Force Team {i}",token_hash=digest(f"force-team-{i}"))
            self.db.add(p); self.db.flush(); s.join(self.db,t,p.id)
        s.audit(self.db,t,"hold",{"reason":"Organizer review"},"admin",hold=True)
        tid=t.id
        self.db.commit()
        headers={"X-Admin-Key":"force-test-key"}
        forced={"force":True,"force_reason":"Run event ahead of schedule"}
        base=f"/api/admin/tournaments/{tid}"
        with patch.object(main,"SessionLocal",self.Session),patch.object(tournament_api,"SessionLocal",self.Session),patch.object(security,"SessionLocal",self.Session),patch.dict(os.environ,{"ADMIN_API_KEY":"force-test-key"}):
            client=TestClient(main.app)
            def post(path,body=None,code=200):
                response=client.post(base+path,json=body or {},headers=headers)
                self.assertEqual(response.status_code,code,response.text)
                return response.json()
            self.assertEqual(client.post(base+"/lock-roster",json=forced).status_code,401)
            post("/lock-roster",{"force":"true"},422)
            post("/lock-roster",code=409)
            post("/confirm-bracket",forced,409)
            post("/lock-roster",forced)
            post("/confirm-groups",forced)
            public=client.get(f"/api/tournaments/{tid}").json()
            rid=public["rounds"][0]["id"]
            rr=f"/rounds/{rid}"
            window={"opens_at":(now+timedelta(days=1)).isoformat(),"closes_at":(now+timedelta(days=100)).isoformat(),**forced}
            response=client.put(base+rr+"/window",json=window,headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            # Force also edits an open window without dropping accepted attempts.
            response=client.put(base+rr+"/window",json=window,headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            for i in range(2):
                response=client.post(f"/api/tournaments/{tid}{rr}/submissions",json={"language":"python","source":"pass"},
                    headers={"Authorization":f"Bearer force-team-{i}","Idempotency-Key":"force-flow"})
                self.assertEqual(response.status_code,200,response.text)
            post(rr+"/lock-bots",forced,409)  # Pending compilation cannot be skipped.
            self.db.expire_all()
            r=self.db.get(TournamentRound,rid)
            self.assertEqual(r.status,"submission")
            self.assertEqual(r.closes_at,now+timedelta(days=100))
            for submission in self.db.scalars(select(Submission)):
                submission.status="ready"
            self.db.commit()
            post(rr+"/lock-bots",code=409)
            post(rr+"/lock-bots",forced)
            post(rr+"/lock-bots",forced,409)  # No duplicate locks or games.
            self.db.expire_all()
            r=self.db.get(TournamentRound,rid)
            self.assertLess(r.closes_at,s.utcnow())
            self.assertEqual(self.db.scalar(select(func.count()).select_from(LockedBot)),2)
            self.assertEqual(self.db.scalar(select(func.count()).select_from(Match)),2)
            s.audit(self.db,self.db.get(Tournament,tid),"hold",{"reason":"Batch review"},"admin",r,hold=True)
            self.db.commit()
            post(rr+"/start-batch",code=409)
            post(rr+"/start-batch",forced)
            post(rr+"/finalize-encounters",forced,409)
            post(rr+"/qualify",forced,409)  # No fabricated game scores.

            def finish_games():
                self.db.expire_all()
                e=self.db.scalar(select(Encounter).where(Encounter.round_id==rid))
                while e.status=="running":
                    m=self.db.scalar(select(Match).where(Match.encounter_id==e.id,Match.status=="queued"))
                    self.assertIsNotNone(m)
                    m.status="done"
                    m.points_a=float(m.player_a==1); m.points_b=1-m.points_a
                    m.winner_id=1
                    self.db.flush(); s.after_game(self.db,m); self.db.flush()
                self.assertEqual(e.status,"provisional")
                if self.db.get(TournamentRound,rid).level:
                    self.assertIsNone(e.appeal_until)
                else:
                    self.assertGreater(e.appeal_until,s.utcnow())
                self.db.commit()
                return e.id

            eid=finish_games()
            post(rr+"/finalize-encounters",{"encounter_id":eid},409)
            post(rr+"/finalize-encounters",{**forced,"encounter_id":eid})
            self.assertFalse(post(rr+"/qualify",forced)["playoffs_required"])
            post(rr+"/finalize",code=409)
            post(rr+"/finalize",forced)
            self.db.expire_all()
            s.audit(self.db,self.db.get(Tournament,tid),"hold",{"reason":"Bracket review"},"admin",hold=True)
            self.db.commit()
            post("/confirm-bracket",code=409)
            post("/confirm-bracket",forced)
            public=client.get(f"/api/tournaments/{tid}").json()
            rid=public["rounds"][-1]["id"]; rr=f"/rounds/{rid}"
            self.assertEqual(public["status"],"knockout")
            response=client.put(base+rr+"/window",json=window,headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            post(rr+"/lock-bots",forced)  # Compatible group bots are inherited.
            post(rr+"/start-batch",forced)
            finish_games()
            post(rr+"/finalize-encounters")  # Knockout needs no appeal wait or force.
            post(rr+"/finalize")
            public=client.get(f"/api/tournaments/{tid}").json()
            self.assertEqual((public["status"],public["champion_id"]),("complete",1))
            self.assertEqual(sum(len(e["games"]) for e in public["encounters"]),8)
            post(rr+"/start-batch",forced,409)
            audit=client.get(base+"/audit",headers=headers).json()["actions"]
            actions={a["evidence"]["action"] for a in audit if a["kind"]=="force_stage"}
            self.assertEqual(actions,{"lock-roster","confirm-groups","open-window","lock-bots","start-batch",
                                      "finalize-encounters","qualify","finalize","confirm-bracket"})
            self.assertTrue(all(a["evidence"]["reason"]==forced["force_reason"] for a in audit if a["kind"]=="force_stage"))
            self.assertTrue(all(n["kind"]!="force_stage" for n in public["notices"]))

    def test_force_hold_scope_and_infrastructure_gate(self):
        t,r=self.make(4,4,2)
        other=TournamentRound(tournament_id=t.id,level=1,rules_digest=r.rules_digest,environment=r.environment)
        self.db.add(other); self.db.flush()
        global_hold=s.audit(self.db,t,"hold",{"reason":"Global hold"},"admin",hold=True)
        other_hold=s.audit(self.db,t,"hold",{"reason":"Other round hold"},"admin",other,hold=True)
        now=s.utcnow()
        s.open_window(self.db,t,r,now+timedelta(days=1),now+timedelta(days=2),"admin",force=True)
        self.assertIsNotNone(global_hold.resolved_at)
        self.assertIsNone(other_hold.resolved_at)
        link=s.submit(self.db,t,r,1,"python","pass","force-error")
        self.db.get(Submission,link.submission_id).status="infrastructure_error"
        self.db.flush()
        with self.assertRaises(HTTPException):
            s.lock_bots(self.db,t,r,"admin",force=True)
        self.assertEqual(r.status,"submission")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(LockedBot)),0)

    def test_force_qualification_keeps_required_playoffs(self):
        t,r=self.make(4,4,2)
        self.ready_round(t,r)
        self.run_round(t,r,draw_all=True)
        result=s.qualify(self.db,t,r,"admin",force=True)
        self.assertTrue(result["playoffs_required"])
        self.assertEqual(r.status,"running")
        with self.assertRaises(HTTPException):
            s.finalize_round(self.db,t,r,"admin",force=True)

    def test_notice_removal_keeps_hold_and_private_audit(self):
        t,r=self.make(4,4,2)
        s.exception(self.db,t,r,"hold",{"reason":"Review pending","evidence":"private proof"},"admin")
        self.db.flush()
        hold=s.visible_notices(self.db,t)[0]
        notice=s.publish_notice(self.db,t,"  Manual notice\nSecond line  ","notice","admin")
        self.assertEqual(s.notice_view(notice)["reason"],"Manual notice\nSecond line")
        self.assertEqual(s.remove_notices(self.db,t,"admin",notice.id),1)
        self.assertEqual(s.remove_notices(self.db,t,"admin",notice.id),0)
        self.assertEqual(s.remove_notices(self.db,t,"admin"),1)
        self.assertEqual(s.public_view(self.db,t)["notices"],[])
        self.assertTrue(s.held(self.db,t,r))
        self.assertIsNone(hold.resolved_at)
        self.assertEqual(json.loads(hold.evidence)["evidence"],"private proof")
        self.assertIsNotNone(hold.notice_hidden_at)
        self.assertEqual(s.remove_notices(self.db,t,"admin"),0)
        latest=s.publish_notice(self.db,t,"New manual notice","rules_update","admin")
        self.assertEqual([a.id for a in s.visible_notices(self.db,t)],[latest.id])

    def test_notice_api_permissions_validation_and_tournament_scope(self):
        from fastapi.testclient import TestClient
        from app import main,tournament_api
        t,r=self.make(4,4,2)
        tid=t.id
        original=(t.status,t.rules_digest,r.status)
        # Completed history must not leak notices into the selected tournament.
        t.status="complete"
        other=s.create(self.db,dict(name="Other Cup",max_group_size=4,qualifiers=2,
            registration_open=s.utcnow()-timedelta(hours=1),registration_close=s.utcnow()+timedelta(days=1),
            appeal_contact="admin@example.test",incident_policy="Wait for infrastructure repair"),"admin")
        other_id=other.id
        private_action=s.audit(self.db,t,"configure",{"secret":"private"},"admin")
        self.db.flush()
        private_id=private_action.id
        self.db.commit()
        with patch.object(main,"SessionLocal",self.Session),patch.object(tournament_api,"SessionLocal",self.Session),patch.dict(os.environ,{"ADMIN_API_KEY":"notice-test-key","ADMIN_USERNAME":"notice-organizer"}):
            client=TestClient(main.app)
            url=f"/api/admin/tournaments/{tid}/notices"
            headers={"X-Admin-Key":"notice-test-key"}
            self.assertEqual(client.post(url,json={"message":"unauthorized"}).status_code,401)
            self.assertEqual(client.get(url).status_code,401)
            self.assertEqual(client.delete(url).status_code,401)
            self.assertEqual(client.delete(f"{url}/1").status_code,401)
            for body,code in [({},204),({"message":""},204),({"message":"   "},204),({"message":"x"*4001},422),
                              ({"message":"valid","kind":"hold"},422)]:
                self.assertEqual(client.post(url,json=body,headers=headers).status_code,code)
            self.assertEqual(client.get(url,headers=headers).json(),[])
            message="<b>Literal text</b>\nManual second line"
            response=client.post(url,json={"message":message,"kind":"rules_update"},headers=headers)
            self.assertEqual(response.status_code,201,response.text)
            notice=response.json()
            self.assertEqual(notice["reason"],message)
            self.assertEqual(notice["actor"],"notice-organizer")
            self.assertEqual(client.get(url,headers=headers).json(),[notice])
            public=client.get(f"/api/tournaments/{tid}").json()
            self.assertEqual((public["status"],public["rules_digest"],public["rounds"][0]["status"]),
                             ("complete",original[1],original[2]))
            self.assertEqual(client.get(f"/api/tournaments/{other_id}").json()["notices"],[])
            self.assertEqual(client.delete(f"/api/admin/tournaments/{other_id}/notices/{notice['id']}",headers=headers).status_code,404)
            self.assertEqual(client.delete(f"{url}/{private_id}",headers=headers).status_code,404)
            self.assertEqual(client.delete(f"{url}/{notice['id']}",headers=headers).json(),{"removed":1})
            self.assertEqual(client.get(f"/api/tournaments/{tid}").json()["notices"],[])
            client.post(url,json={"message":"Another manual notice"},headers=headers).raise_for_status()
            self.assertEqual(client.delete(url,headers=headers).json(),{"cleared":1})
            self.assertEqual(client.delete(url,headers=headers).json(),{"cleared":0})
            audit=client.get(f"/api/admin/tournaments/{tid}/audit",headers=headers).json()
            stored=next(a for a in audit["actions"] if a["id"]==notice["id"])
            self.assertEqual(stored["evidence"]["reason"],message)
            self.assertIsNotNone(stored["notice_hidden_at"])

    def test_notice_migration_preserves_existing_audit_and_repeats(self):
        from app import schema
        t,r=self.make(4,4,2)
        notice=s.publish_notice(self.db,t,"Existing notice","rules_update","admin")
        notice_id=notice.id
        self.db.commit()
        with self.engine.begin() as conn:
            conn.execute(text("ALTER TABLE tournament_actions DROP COLUMN notice_hidden_at"))
        with patch.object(schema,"engine",self.engine):
            schema.ensure_schema()
            schema.ensure_schema()
        self.db.expire_all()
        stored=self.db.get(OrganizerAction,notice_id)
        self.assertEqual(json.loads(stored.evidence)["reason"],"Existing notice")
        self.assertIsNone(stored.notice_hidden_at)

    def test_api_authorization_join_submission_and_public_views(self):
        from fastapi.testclient import TestClient
        from app import main, tournament_api, security
        from app.security import digest
        t,r=self.make(4,4,2)
        self.db.get(Player,1).token_hash=digest("team-token")
        now=s.utcnow()
        s.open_window(self.db,t,r,now-timedelta(hours=1),now+timedelta(hours=1),"admin")
        tid,rid=t.id,r.id
        self.db.commit()
        with patch.object(main,"SessionLocal",self.Session),patch.object(tournament_api,"SessionLocal",self.Session),patch.object(security,"SessionLocal",self.Session),patch.dict(os.environ,{"ADMIN_API_KEY":"test-admin-key"}):
            client=TestClient(main.app)
            self.assertEqual(client.get("/").status_code,200)
            self.assertEqual(client.get("/admin").status_code,200)
            self.assertEqual(client.post(f"/api/admin/tournaments/{tid}/confirm-bracket").status_code,401)
            self.assertEqual(client.post(f"/api/tournaments/{tid}/rounds/{rid}/submissions",json={"language":"python","source":"pass"}).status_code,401)
            headers={"Authorization":"Bearer team-token","Idempotency-Key":"request-key"}
            response=client.post(f"/api/tournaments/{tid}/rounds/{rid}/submissions",json={"language":"python","source":"pass"},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            repeat=client.post("/api/submissions",json={"language":"python","source":"pass"},headers=headers)
            self.assertEqual(repeat.json()["submission_id"],response.json()["submission_id"])
            self.assertEqual(client.get(f"/api/tournaments/{tid}/team/status",headers=headers).json()["attempts_used"],1)
            self.assertEqual(client.post("/api/challenges",json={"opponent_id":2},headers=headers).status_code,409)
            for suffix in ("", "/standings", "/bracket", "/encounters", "/results", "/rules"):
                self.assertEqual(client.get(f"/api/tournaments/{tid}{suffix}").status_code,200,suffix)
            self.assertEqual(client.post(f"/api/admin/tournaments/{tid}/rounds/{rid}/lock-bots",json={},headers={"X-Admin-Key":"test-admin-key"}).status_code,409)

    def test_additive_migration_preserves_legacy_data_and_repeats(self):
        from app import schema
        schema_name="migration_test"
        with self.engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {schema_name}"))
        migration_engine=create_engine(os.environ["TEST_DATABASE_URL"],connect_args={"options":f"-csearch_path={schema_name}"})
        # Old table definitions omit the Tournament links on jobs and matches.
        from sqlalchemy.schema import CreateTable
        with migration_engine.begin() as conn:
            for name in ("players","submissions","jobs","matches"):
                table=Base.metadata.tables[name].to_metadata(__import__('sqlalchemy').MetaData())
                if name=="matches":
                    for constraint in list(table.constraints):
                        if any(c.name in ("encounter_id","game_number") for c in constraint.columns):
                            table.constraints.remove(constraint)
                    table._columns.remove(table.c.encounter_id)
                    table._columns.remove(table.c.game_number)
                # Foreign-key targets resolve against the full metadata.
                sql=str(CreateTable(Base.metadata.tables[name]).compile(migration_engine))
                if name=="jobs":
                    sql="\n".join(line for line in sql.splitlines() if "round_id" not in line).replace(",\n)","\n)")
                if name=="matches":
                    lines=sql.splitlines()
                    lines=[line for line in lines if not any(word in line for word in ("encounter_id","game_number","uq_encounter_game"))]
                    sql="\n".join(lines).replace(",\n)","\n)")
                sql=re.sub(r",\s*\)", "\n)", sql)
                conn.execute(text(sql))
            conn.execute(text("INSERT INTO players (id,name,token_hash,created_at) VALUES (1,'Legacy A','hash',now()),(2,'Legacy B','hash',now())"))
            conn.execute(text("INSERT INTO submissions (id,player_id,language,source,status,created_at) VALUES (1,1,'python','legacy source','ready',now()),(2,2,'python','pass','ready',now())"))
            conn.execute(text("INSERT INTO matches (id,player_a,player_b,submission_a,submission_b,event_day,kind,status,points_a,points_b,detail,created_at) VALUES (42,1,2,1,2,'2026-10-01','daily','done',1,0,'{}',now())"))
        try:
            with patch.object(schema,"engine",migration_engine):
                schema.ensure_schema()
                schema.ensure_schema()
            with migration_engine.connect() as conn:
                self.assertEqual(conn.scalar(text("SELECT source FROM submissions WHERE id=1")),"legacy source")
                self.assertEqual(conn.scalar(text("SELECT status FROM matches WHERE id=42")),"done")
                self.assertIsNone(conn.scalar(text("SELECT encounter_id FROM matches WHERE id=42")))
                self.assertEqual(conn.scalar(text("SELECT count(*) FROM tournament_elo_events")),0)
        finally:
            migration_engine.dispose()
            with self.engine.begin() as conn:
                conn.execute(text(f"DROP SCHEMA {schema_name} CASCADE"))

    @unittest.skipUnless(os.getenv("RUN_SANDBOX_TESTS") == "1", "Set RUN_SANDBOX_TESTS=1 for real DB/queue/Docker integration")
    def test_real_postgres_queue_python_cpp_judge_and_replay(self):
        from pathlib import Path
        from app import worker
        t,r=self.make(4,4,2)
        env=json.loads(r.environment)
        env["image"]=os.environ["TEST_SANDBOX_IMAGE"]
        r.environment=t.environment=s.dump(env)
        now=s.utcnow()
        s.open_window(self.db,t,r,now-timedelta(minutes=1),now+timedelta(minutes=1),"admin")
        for pid in s.participants(self.db,t,r):
            lang="python" if pid%2 else "cpp"
            source=Path("examples/bot_greedy.py" if lang=="python" else "examples/bot_first.cpp").read_text()
            s.submit(self.db,t,r,pid,lang,source,"real-compile")
        tid,rid=t.id,r.id
        self.db.commit()
        def drain():
            for _ in range(30):
                with self.Session.begin() as db:
                    job=worker.claim(db,"real-owner")
                    jid=job.id if job else None
                if jid is None:return
                worker.process(jid,"real-owner")
            self.fail("Queue did not drain")
        with patch.object(worker,"SessionLocal",self.Session),patch.object(worker,"WORKER_ID","test-worker"):
            drain()
            self.db.expire_all()
            t,r=self.db.get(Tournament,tid),self.db.get(TournamentRound,rid)
            entries=self.db.scalars(select(RoundSubmission).where(RoundSubmission.round_id==rid)).all()
            self.assertEqual([self.db.get(Submission,e.submission_id).status for e in entries],["ready"]*4)
            s.lock_bots(self.db,t,r,"admin",now=r.closes_at+timedelta(seconds=1))
            s.start_batch(self.db,t,r,1,"admin")
            self.db.commit()
            drain()
            self.db.expire_all()
            encounters=self.db.scalars(select(Encounter).where(Encounter.round_id==rid,Encounter.batch==1)).all()
            self.assertEqual([e.status for e in encounters],["provisional"]*2)
            self.assertEqual(self.db.scalar(select(func.count()).select_from(GameAttempt).where(GameAttempt.status=="done")),4)
            self.assertEqual(self.db.scalar(select(func.count()).select_from(EloEvent)),2)
            for e in encounters:
                for m in s.games(self.db,e):
                    self.assertGreater(len(json.loads(m.detail)["turns"]),2)
                    self.assertEqual(m.kind,"tournament")


if __name__ == "__main__":
    unittest.main()
