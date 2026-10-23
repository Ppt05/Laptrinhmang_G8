"""Regression: local lab and official worker produce identical match details.

Requires the normal server Python dependencies, but never a real database or Docker.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
from app import worker
from app.local import LocalSession
from app.local_runtime import ROOT, compile_bot, spawn, stop_process
from tests.test_local import specification, wait_for, FIRST, GREEDY


class WorkerParityTests(unittest.TestCase):
    def test_real_callbacks_match_worker_in_both_seats_and_on_forfeit(self):
        invalid='def choose_move(state):\n return {"piece_id":"unknown","to":"a1"}\n'
        for sources in ((GREEDY,FIRST),(FIRST,GREEDY),(invalid,FIRST)):
            with self.subTest(sources=sources):
                local=LocalSession([specification(source) for source in sources],max_moves=40)
                self.addCleanup(local.command,'stop')
                wait_for(lambda:local.status in ('paused','error'))
                self.assertEqual(local.status,'paused',local.error)
                local.command('run',0)
                wait_for(lambda:not local.thread.is_alive())
                with tempfile.TemporaryDirectory() as directory:
                    submissions={i:SimpleNamespace(id=i,language='python',source=source)
                                 for i,source in enumerate(sources,1)}
                    artifacts={i:compile_bot('python',source,Path(directory)/str(i))
                               for i,source in enumerate(sources,1)}
                    def start(submission):
                        return spawn([sys.executable,'-I','-S','-u','-X','utf8',str(artifacts[submission.id])],
                                     stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0),None
                    def stop(proc):
                        stop_process(proc)
                        proc.stdin.close();proc.stdout.close()
                    match=SimpleNamespace(submission_a=1,submission_b=2,player_a=1,player_b=2,
                        status='queued',winner_id=None,kind='practice',detail=None)
                    db=SimpleNamespace(get=lambda model,identifier:submissions[identifier])
                    with patch.object(worker,'ensure_local_artifact',return_value=True), \
                         patch.object(worker,'start_bot',side_effect=start), \
                         patch.object(worker,'stop_bot',side_effect=stop), \
                         patch.dict(os.environ,{'MATCH_MAX_MOVES':'40'}):
                        worker.play_match(db,match)
                    self.assertEqual(match.status,'done')
                    self.assertEqual(json.loads(match.detail),local.snapshot()['detail'])
                    self.assertEqual((match.points_a,match.points_b),(0,0))

    def test_public_local_route_is_instructions_without_code_execution(self):
        from fastapi.testclient import TestClient
        from app.main import app
        # No lifespan context: this read-only route must not need PostgreSQL.
        client=TestClient(app)
        response=client.get('/local')
        self.assertEqual(response.status_code,200)
        self.assertIn('python -m app.local',response.text)
        self.assertEqual(client.post('/api/session',json={}).status_code,404)
        response=client.get('/')
        self.assertIn('href="/local"',response.text)


if __name__=='__main__':
    unittest.main()
