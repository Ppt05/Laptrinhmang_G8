"""Seed only a dedicated disposable database for browser_daily.py."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, text
from app import daily as d, tournament as t
from app.db import SessionLocal, Base, engine
from app.models import DailyControl, Player, Submission, Job
from app.schema import ensure_schema
from app.security import hash_password

assert os.environ['DATABASE_URL']==os.environ['TEST_DATABASE_URL']
assert os.environ['TEST_DATABASE_URL'].endswith('/daily_v2_test')
ensure_schema()
with engine.begin() as connection:
    connection.execute(text("TRUNCATE " + ",".join(Base.metadata.tables) + " RESTART IDENTITY CASCADE"))
with SessionLocal.begin() as db:
    t.serialize(db)
    control=db.get(DailyControl,1)
    if control is None: control=DailyControl(id=1);db.add(control)
    control.enabled=True;db.flush()
    def account(name):
        p=Player(name=name,token_hash='x'*64,password_hash=hash_password('DailyUITest2026!'))
        db.add(p);db.flush();return p
    def bot(p,key):
        s=d.submit(db,p.id,'python','def choose_move(state): return state["legal_moves"][0]',key)
        s.status='ready';db.flush();return s
    ready=account('Daily Ready Browser')
    bot(ready,'ready-v1');bot(ready,'ready-v2')
    teams=[account('Browser Daily Team '+str(i+1)) for i in range(6)]
    bots=[bot(p,'bot') for p in teams]
    for round_number in range(4):
        for p,s in zip(teams,bots):d.find_match(db,p.id,s.id,f'fixture-{round_number}-{p.id}')
        for mid in d.pair(db):
            m=db.get(d.Match,mid);m.status='done';m.winner_id=m.player_a;m.points_a=1;m.points_b=0
            m.detail='{"turns":[],"win_reason":"goal","winner_side":"south"}'
            d.after_game(db,m)
    legacy_a,legacy_b=account('Legacy Daily A'),account('Legacy Daily B')
    sa,sb=bot(legacy_a,'legacy'),bot(legacy_b,'legacy')
    legacy=d.Match(player_a=legacy_a.id,player_b=legacy_b.id,submission_a=sa.id,submission_b=sb.id,event_day=d.today(),kind='daily',status='done',points_a=.5,points_b=.5,detail='{"turns":[],"win_reason":"move_limit_draw"}')
    db.add(legacy);db.flush();d.after_game(db,legacy)
    for job in db.scalars(select(Job)):job.status='done'
print('Seeded disposable browser fixtures')
