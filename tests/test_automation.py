"""Unattended lifecycle on disposable PostgreSQL with a controlled clock."""
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select,func,text

import test_tournament as fixtures
from app import tournament as s
from app.models import (Tournament,TournamentRound,Player,Enrollment,Submission,Job,Encounter,
                        Match,OrganizerAction,RecordedDraw,LockedBot,GroupMember,TournamentGroup,EloEvent,WorkerNode)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"),"Set TEST_DATABASE_URL to disposable PostgreSQL")
class AutomationTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.TournamentTests.setUpClass.__func__)
    tearDownClass=classmethod(fixtures.TournamentTests.tearDownClass.__func__)
    setUp=fixtures.TournamentTests.setUp
    tearDown=fixtures.TournamentTests.tearDown
    make=fixtures.TournamentTests.make
    ready_round=fixtures.TournamentTests.ready_round

    def tick(self,now):
        with patch.object(s,"utcnow",return_value=now):
            result=s.auto_tick(self.db,now)
            self.db.flush()
        return result

    def create_registration(self,count=14):
        now=s.utcnow()
        t=s.create(self.db,dict(name="Automatic Cup",max_group_size=4,qualifiers=2,
            registration_open=now-timedelta(hours=1),registration_close=now+timedelta(minutes=1),
            auto_submission_minutes=1),"admin")
        for i in range(count):
            p=Player(name=f"Auto Team {i+1}",token_hash="x"*64)
            self.db.add(p);self.db.flush();s.join(self.db,t,p.id,now)
        self.db.flush()
        return t,now

    def finish_game(self,match,now,draw=False):
        e=self.db.get(Encounter,match.encounter_id)
        score=.5 if draw else float(match.player_a==min(e.player_a,e.player_b))
        match.status,match.points_a,match.points_b="done",score,1-score
        match.winner_id=match.player_a if score==1 else match.player_b if score==0 else None
        match.detail='{"turns":[],"win_reason":"deterministic_test"}'
        for job in self.db.scalars(select(Job).where(Job.kind=="match",Job.status=="queued")):
            if json.loads(job.payload)["match_id"]==match.id:job.status="done"
        with patch.object(s,"utcnow",return_value=now):s.after_game(self.db,match)
        self.db.flush()

    def test_full_14_team_flow_without_organizer_clicks(self):
        t,now=self.create_registration()
        self.tick(t.registration_close)
        self.assertEqual(t.status,"registration")
        now=t.registration_close+timedelta(microseconds=1);self.tick(now)
        r=self.db.scalar(select(TournamentRound))
        self.assertEqual((t.status,r.status),("groups","submission"))
        self.assertEqual(r.closes_at-r.opens_at,timedelta(minutes=1))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(TournamentGroup)),4)
        for pid in s.participants(self.db,t,r):
            link=s.submit(self.db,t,r,pid,"python","pass",f"auto-{pid}",now)
            self.db.get(Submission,link.submission_id).status="ready"
        for job in self.db.scalars(select(Job)):job.status="done"
        group_bots={pid:self.db.scalar(select(Submission.id).where(Submission.player_id==pid)) for pid in range(1,15)}
        now=r.closes_at+timedelta(microseconds=1)
        for _ in range(300):
            self.tick(now)
            counts=lambda:tuple(self.db.scalar(select(func.count()).select_from(model)) for model in (Job,Encounter,OrganizerAction,RecordedDraw,LockedBot))
            before=counts();self.tick(now);self.assertEqual(counts(),before,"Duplicate tick changed immutable scheduling")
            if t.status=="complete":break
            queued=self.db.scalar(select(Match).where(Match.status=="queued").order_by(Match.id).limit(1))
            if queued:
                self.finish_game(queued,now)
                continue
            current=self.db.scalar(select(TournamentRound).order_by(TournamentRound.level.desc()).limit(1))
            if current.status=="submission":now=current.closes_at+timedelta(microseconds=1)
            elif current.status=="provisional":now=current.appeal_until+timedelta(microseconds=1)
            else:
                next_appeal=self.db.scalar(select(func.min(Encounter.appeal_until)).where(Encounter.status=="provisional"))
                self.assertIsNotNone(next_appeal,t.auto_status)
                now=next_appeal+timedelta(microseconds=1)
        else:self.fail("Automatic tournament did not complete")
        self.assertEqual(t.champion_id,1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Encounter)),25)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Match)),78)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(EloEvent)),25)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(TournamentRound)),4)
        for lock in self.db.scalars(select(LockedBot).join(TournamentRound).where(TournamentRound.level>0)):
            self.assertEqual(lock.submission_id,group_bots[lock.player_id])
        self.assertFalse(self.db.scalar(select(OrganizerAction.id).where(OrganizerAction.kind=="force_stage")))
        self.assertTrue(self.db.scalar(select(OrganizerAction.id).where(OrganizerAction.actor=="scheduler")))

    def test_compilation_wait_and_infrastructure_pause_do_not_force_progress(self):
        t,r=self.make(4,4,2);now=s.utcnow()
        s.open_window(self.db,t,r,now-timedelta(minutes=1),now+timedelta(minutes=1),"admin")
        entry=s.submit(self.db,t,r,1,"python","pass","pending",now)
        for pid in (2,3,4):
            ready=s.submit(self.db,t,r,pid,"python","pass",f"ready-{pid}",now)
            self.db.get(Submission,ready.submission_id).status="ready"
        after=r.closes_at+timedelta(seconds=1)
        self.tick(after)
        self.assertEqual(r.status,"submission")
        self.assertIn("đang xử lý",t.auto_status)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(LockedBot)),0)
        sub=self.db.get(Submission,entry.submission_id);sub.status="infrastructure_error"
        self.tick(after);self.assertEqual(r.status,"submission");self.assertIn("hạ tầng",t.auto_status)
        sub.status="ready";self.tick(after)
        self.assertEqual(r.status,"running")
        e=self.db.scalar(select(Encounter));e.status="paused"
        self.tick(after);self.assertIn("hạ tầng",t.auto_status)
        self.assertEqual(e.status,"paused")

    def test_hold_and_appeals_stop_automation_until_resolved_or_due(self):
        t,r=self.make(4,4,2);self.ready_round(t,r);now=s.utcnow()
        s.audit(self.db,t,"hold",{},"admin",r,hold=True);self.db.flush()
        self.tick(now);self.assertEqual(r.status,"locked")
        self.assertIn("hold",t.auto_status)
        hold=self.db.scalar(select(OrganizerAction).where(OrganizerAction.hold.is_(True)))
        self.assertIsNone(hold.resolved_at)
        hold.resolved_at=now;self.tick(now)
        while (match:=self.db.scalar(select(Match).where(Match.status=="queued").limit(1))):
            self.finish_game(match,now)
        provisional=self.db.scalars(select(Encounter).where(Encounter.status=="provisional")).all()
        self.assertTrue(provisional)
        self.tick(now+timedelta(minutes=29));self.assertTrue(all(e.status=="provisional" for e in provisional))
        self.tick(now+timedelta(minutes=30));self.assertTrue(all(e.status=="finalized" for e in provisional))

    def knockout_round(self):
        t,r=self.make(8,4,2)
        self.ready_round(t,r)
        fixtures.TournamentTests.run_round(self,t,r)
        s.qualify(self.db,t,r,"admin")
        s.finalize_round(self.db,t,r,"admin",now=s.utcnow()+timedelta(hours=1))
        s.confirm_bracket(self.db,t,"admin");self.db.flush()
        knockout=self.db.scalar(select(TournamentRound).where(TournamentRound.level==1))
        self.ready_round(t,knockout)
        t.auto_submission_minutes=1
        return t,knockout,s.utcnow()

    def test_knockout_runs_continuously_and_waits_for_all_winners_before_submissions(self):
        t,r,now=self.knockout_round()
        self.tick(now)
        semis=self.db.scalars(select(Encounter).where(Encounter.round_id==r.id).order_by(Encounter.id)).all()
        first,second=semis
        for _ in range(6):
            match=self.db.scalar(select(Match).where(Match.encounter_id==first.id,Match.status=="queued"))
            self.assertIsNotNone(match)
            self.finish_game(match,now)
        self.assertEqual(first.status,"provisional")
        self.assertIsNone(first.appeal_until)
        self.tick(now)
        self.assertEqual(first.status,"finalized")
        self.assertEqual(r.status,"running")
        self.assertIsNone(self.db.scalar(select(TournamentRound).where(TournamentRound.level==2)))
        # Existing provisional results also use the new operational policy.
        for _ in range(6):
            match=self.db.scalar(select(Match).where(Match.encounter_id==second.id,Match.status=="queued"))
            self.assertIsNotNone(match)
            self.finish_game(match,now)
        second.appeal_until=now+timedelta(minutes=30)
        self.tick(now)
        final=self.db.scalar(select(TournamentRound).where(TournamentRound.level==2))
        self.assertEqual((r.status,final.status),("finalized","submission"))
        self.assertEqual((final.opens_at,final.closes_at),(now,now+timedelta(minutes=1)))
        self.assertFalse(self.db.scalar(select(Match.id).join(Encounter).where(Encounter.round_id==final.id)))
        now=final.closes_at+timedelta(microseconds=1)
        self.tick(now)
        self.assertEqual(final.status,"running")
        self.assertTrue(all(b.reason=="inherited_compatible" for b in self.db.scalars(
            select(LockedBot).where(LockedBot.round_id==final.id))))
        for _ in range(10):
            match=self.db.scalar(select(Match).join(Encounter).where(Encounter.round_id==final.id,Match.status=="queued"))
            self.assertIsNotNone(match)
            self.finish_game(match,now,draw=True)
        self.tick(now)
        self.assertEqual((t.status,final.status),("complete","finalized"))
        self.assertFalse(self.db.scalar(select(OrganizerAction.id).where(OrganizerAction.kind=="force_stage")))

    def test_report_stops_queued_games_and_advancement_until_decision(self):
        from app import worker
        t,r,now=self.knockout_round()
        self.tick(now)
        for job in self.db.scalars(select(Job).where(Job.kind=="compile")):job.status="done"
        e=self.db.scalar(select(Encounter).where(Encounter.round_id==r.id).order_by(Encounter.id))
        hold=s.exception(self.db,t,r,"hold",{"encounter_id":e.id},"admin")
        self.tick(now)
        self.assertIn("hold",t.auto_status)
        self.assertEqual(r.status,"running")
        # A held queued game stays untouched; other games can still be claimed.
        job=worker.claim(self.db,"test-owner")
        self.assertIsNotNone(job)
        self.assertNotEqual(self.db.get(Match,json.loads(job.payload)["match_id"]).encounter_id,e.id)
        self.assertIsNone(worker.claim(self.db,"test-owner"))
        held_game=self.db.scalar(select(Match).where(Match.encounter_id==e.id,Match.status=="queued"))
        self.assertIsNotNone(held_game)
        self.assertIsNone(held_game.worker_id)
        s.exception(self.db,t,r,"decision",{"action_id":hold["action_id"]},"admin")
        resumed=worker.claim(self.db,"test-owner")
        self.assertEqual(json.loads(resumed.payload)["match_id"],held_game.id)
        # A completed result with a new report must not advance either.
        for encounter in self.db.scalars(select(Encounter).where(Encounter.round_id==r.id)):
            for game in s.games(self.db,encounter):
                self.finish_game(game,now)
        hold=s.exception(self.db,t,r,"hold",{},"admin")
        self.tick(now)
        self.assertTrue(all(e.status=="provisional" for e in self.db.scalars(
            select(Encounter).where(Encounter.round_id==r.id))))
        self.assertIsNone(self.db.scalar(select(TournamentRound).where(TournamentRound.level==2)))
        s.exception(self.db,t,r,"decision",{"action_id":hold["action_id"]},"admin")
        self.tick(now)
        self.assertEqual(r.status,"finalized")
        self.assertEqual(self.db.scalar(select(TournamentRound).where(TournamentRound.level==2)).status,"submission")

    def test_two_workers_and_restart_do_not_duplicate_due_actions(self):
        t,now=self.create_registration();tid=t.id;now=t.registration_close+timedelta(seconds=1);self.db.commit()
        def run_tick(_):
            with self.Session.begin() as db:return s.auto_tick(db,now)
        with patch.object(s,"utcnow",return_value=now),ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(run_tick,range(2)))
        self.db.expire_all();t=self.db.get(Tournament,tid)
        self.assertEqual(t.status,"groups")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(GroupMember)),14)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(TournamentRound)),1)
        actions=self.db.scalar(select(func.count()).select_from(OrganizerAction))
        run_tick(0);self.db.expire_all()
        self.assertEqual(self.db.scalar(select(func.count()).select_from(OrganizerAction)),actions)

    def test_disable_automation_and_update_any_active_stage_api(self):
        from fastapi.testclient import TestClient
        from app import main,tournament_api
        t,r=self.make(4,4,2);tid=t.id;self.db.commit()
        with patch.object(main,"SessionLocal",self.Session),patch.object(tournament_api,"SessionLocal",self.Session),patch.dict(os.environ,{"ADMIN_API_KEY":"auto-key"}):
            client=TestClient(main.app);url=f"/api/admin/tournaments/{tid}/automation";headers={"X-Admin-Key":"auto-key"}
            self.assertEqual(client.put(url,json={"auto_run":False}).status_code,401)
            for body in ({"auto_run":True,"auto_submission_minutes":0},{"auto_run":"true"},{"auto_run":True,"auto_submission_minutes":1.5}):
                self.assertEqual(client.put(url,json=body,headers=headers).status_code,422)
            response=client.put(url,json={"auto_run":False,"auto_submission_minutes":5},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            self.db.expire_all();self.tick(s.utcnow()+timedelta(days=1))
            self.assertEqual(self.db.get(TournamentRound,r.id).status,"planned")
            self.db.commit()
            response=client.put(url,json={"auto_run":True,"auto_submission_minutes":5},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            self.db.expire_all();now=s.utcnow();self.tick(now)
            self.assertEqual(self.db.get(TournamentRound,r.id).closes_at,now+timedelta(minutes=5))

    def test_existing_window_is_preserved_and_invalid_roster_is_visible(self):
        t,r=self.make(4,4,2);now=s.utcnow()
        start,end=now+timedelta(hours=2),now+timedelta(hours=7)
        s.open_window(self.db,t,r,start,end,"admin")
        self.tick(now);self.assertEqual((r.opens_at,r.closes_at),(start,end))
        t.status="cancelled"
        other,now=self.create_registration(count=1)
        self.tick(other.registration_close+timedelta(seconds=1))
        self.assertEqual(other.status,"registration")
        self.assertTrue(other.auto_status)
        self.assertIsNone(other.allocation)

    def test_stale_worker_cannot_run_automatic_actions(self):
        from app import worker
        t,now=self.create_registration()
        self.db.add(WorkerNode(id="auto-worker",instance_token="new-owner",last_seen=now));self.db.commit()
        stop=threading.Event()
        with patch.object(worker,"SessionLocal",self.Session),patch.object(worker,"WORKER_ID","auto-worker"),patch.object(worker.tournament,"auto_tick") as tick:
            worker.automation_loop("old-owner",stop)
        self.assertTrue(stop.is_set());tick.assert_not_called()

    def test_tied_groups_playoffs_and_ten_game_final_complete_automatically(self):
        t,r=self.make(4,4,2);self.ready_round(t,r);now=s.utcnow()
        for _ in range(150):
            self.tick(now)
            if t.status=="complete":break
            match=self.db.scalar(select(Match).where(Match.status=="queued").limit(1))
            if match:
                self.finish_game(match,now,draw=True)
                continue
            current=self.db.scalar(select(TournamentRound).order_by(TournamentRound.level.desc()).limit(1))
            if current.status=="submission":now=current.closes_at+timedelta(microseconds=1)
            elif current.status=="provisional":now=current.appeal_until+timedelta(microseconds=1)
            else:
                deadline=self.db.scalar(select(func.min(Encounter.appeal_until)).where(Encounter.status=="provisional"))
                self.assertIsNotNone(deadline,t.auto_status);now=deadline+timedelta(microseconds=1)
        else:self.fail("Automatic tied tournament stalled")
        self.assertIsNotNone(t.champion_id)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Encounter).where(Encounter.playoff.is_not(None))),6)
        final=self.db.scalar(select(Encounter).join(TournamentRound).where(TournamentRound.level==1))
        self.assertEqual(len(s.games(self.db,final)),10)
        self.assertEqual(final.playing_result,.5)
        self.assertTrue(all(value==1000 for value in s.ratings(self.db).values()))

    def test_missing_bots_finish_without_fake_games_or_champion(self):
        t,r=self.make(2,4,2);now=s.utcnow()
        for _ in range(10):
            self.tick(now)
            if t.status=="complete":break
            current=self.db.scalar(select(TournamentRound).order_by(TournamentRound.level.desc()).limit(1))
            if current.status=="submission":now=current.closes_at+timedelta(microseconds=1)
            elif current.status=="provisional":now=current.appeal_until+timedelta(microseconds=1)
            else:
                now=self.db.scalar(select(func.min(Encounter.appeal_until)).where(Encounter.status=="provisional"))+timedelta(microseconds=1)
        self.assertEqual((t.status,t.champion_id,t.result),("complete",None,"no_champion"))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Match)),0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(EloEvent)),0)

    def test_additive_automation_migration_preserves_legacy_and_manual_defaults(self):
        from app import schema
        t,r=self.make(4,4,2);tid=t.id;digest=t.rules_digest;self.db.commit()
        with self.engine.begin() as conn:
            for column in ("auto_run","auto_submission_minutes","auto_status"):
                conn.execute(text(f"ALTER TABLE tournaments DROP COLUMN {column}"))
        with patch.object(schema,"engine",self.engine):
            schema.ensure_schema();schema.ensure_schema()
        self.db.expire_all();t=self.db.get(Tournament,tid)
        self.assertEqual((t.status,t.rules_digest,t.auto_run,t.auto_submission_minutes),("groups",digest,False,1440))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(GroupMember)),4)
        self.db.commit()
        with self.engine.begin() as conn:
            row=conn.execute(text("""INSERT INTO tournaments
                (name,status,max_group_size,qualifiers,registration_open,registration_close,incident_policy,appeal_contact,rules_digest,rules_text,environment,created_at)
                SELECT 'Legacy writer','cancelled',max_group_size,qualifiers,registration_open,registration_close,incident_policy,appeal_contact,
                rules_digest,rules_text,environment,created_at FROM tournaments WHERE id=:tid
                RETURNING auto_run,auto_submission_minutes"""),{"tid":tid}).one()
            self.assertEqual(tuple(row),(False,1440))
