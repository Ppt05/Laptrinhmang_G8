"""Daily pagination against a disposable PostgreSQL; never point this at a real DB."""
import os, unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app import daily, main, schema, security
from app.db import Base
from app.models import Player, Submission, Match, EloEvent

@unittest.skipUnless(os.getenv('TEST_DATABASE_URL'), 'Dedicated test PostgreSQL required')
class DailyLeaderboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine=create_engine(os.environ['TEST_DATABASE_URL'])
        cls.Session=sessionmaker(cls.engine,expire_on_commit=False)
        with patch.object(schema,'engine',cls.engine):schema.ensure_schema()
    @classmethod
    def tearDownClass(cls):cls.engine.dispose()
    def setUp(self):
        with self.engine.begin() as c:c.execute(text('TRUNCATE '+','.join(Base.metadata.tables)+' RESTART IDENTITY CASCADE'))
        self.db=self.Session()
        self.players=[Player(name=f'Rank Team {i:02}',token_hash=security.digest(f'rank-token-{i}')) for i in range(27)]
        self.db.add_all(self.players);self.db.flush()
        subs=[Submission(player_id=p.id,language='python',source='pass',status='ready') for p in self.players]
        self.db.add_all(subs);self.db.flush()
        self.matches=[]
        for i in range(0,26,2):
            m=Match(player_a=self.players[i].id,player_b=self.players[i+1].id,submission_a=subs[i].id,submission_b=subs[i+1].id,
                kind='daily',event_day='2026-10-25' if i<12 else '2026-10-26',status='done',points_a=1,points_b=0,winner_id=self.players[i].id)
            self.matches.append(m);self.db.add(m)
        self.db.commit()
    def tearDown(self):self.db.close()
    def test_pages_have_global_rank_and_stable_order(self):
        reference=daily.leaderboard(self.db)
        pages=[daily.leaderboard_page(self.db,offset=o) for o in (0,10,20)]
        self.assertEqual([len(p['items']) for p in pages],[10,10,6])
        items=[row for page in pages for row in page['items']]
        self.assertEqual([r['player_id'] for r in items],[r['player_id'] for r in reference])
        self.assertEqual([r['rank'] for r in items],list(range(1,27)))
        self.assertTrue(pages[0]['has_next']);self.assertFalse(pages[0]['has_previous'])
        self.assertFalse(pages[-1]['has_next']);self.assertTrue(pages[-1]['has_previous'])
    def test_own_rank_available_on_every_page_and_jump(self):
        pid=self.players[25].id
        first=daily.leaderboard_page(self.db,player_id=pid)
        self.assertEqual(first['me']['rank'],26)
        self.assertNotIn(pid,[r['player_id'] for r in first['items']])
        jump=daily.leaderboard_page(self.db,player_id=pid,locate_me=True)
        self.assertEqual(jump['offset'],20)
        self.assertIn(pid,[r['player_id'] for r in jump['items']])
    def test_date_filter_and_unranked_team(self):
        p=daily.leaderboard_page(self.db,day='2026-10-25',player_id=self.players[25].id)
        self.assertEqual(p['total'],12);self.assertIsNone(p['me'])
        self.assertIsNone(daily.leaderboard_page(self.db,player_id=self.players[-1].id)['me'])
        empty=daily.leaderboard_page(self.db,day='2000-01-01',offset=20)
        self.assertEqual((empty['total'],empty['offset'],empty['items']),(0,0,[]))
        self.assertFalse(empty['has_next']);self.assertFalse(empty['has_previous'])
    def test_stale_offset_clamps_to_last_page(self):
        self.assertEqual(daily.leaderboard_page(self.db,offset=9999)['offset'],20)
        self.db.execute(text("UPDATE matches SET status='void' WHERE kind='daily'"));self.db.commit()
        self.assertEqual(daily.leaderboard_page(self.db,offset=20)['offset'],0)
    def test_latest_active_global_elo_is_used_for_date_filtered_page(self):
        m=self.matches[-1]
        old=EloEvent(match_id=m.id,player_a=m.player_a,player_b=m.player_b,score=0,before_a=1000,before_b=1000,after_a=900,after_b=1100)
        self.db.add(old);self.db.commit()
        page=daily.leaderboard_page(self.db,day='2026-10-26')
        self.assertEqual(page['items'][0]['player_id'],m.player_b)
        self.assertEqual(page['items'][0]['elo'],1100)
        old.active=False;self.db.commit()
        self.assertEqual(daily.leaderboard_page(self.db)['items'][0]['player_id'],self.players[0].id)
    def test_api_validation_guest_and_authenticated_summary(self):
        with patch.object(daily,'SessionLocal',self.Session),patch.object(security,'SessionLocal',self.Session):
            client=TestClient(main.app)
            guest=client.get('/api/daily/leaderboard').json()
            self.assertEqual(len(guest['items']),10);self.assertFalse(guest['signed_in']);self.assertIsNone(guest['me'])
            own=client.get('/api/daily/leaderboard?locate_me=true',headers={'Authorization':'Bearer rank-token-25'})
            self.assertEqual(own.status_code,200,own.text)
            self.assertEqual(own.json()['me']['rank'],26);self.assertEqual(own.json()['offset'],20)
            for query in ('offset=-1','limit=0','limit=101'):
                self.assertEqual(client.get('/api/daily/leaderboard?'+query).status_code,422)
            self.assertEqual(client.get('/api/daily/leaderboard?day=bad').status_code,400)
            self.assertEqual(client.get('/api/daily/leaderboard',headers={'Authorization':'Bearer invalid'}).status_code,401)
