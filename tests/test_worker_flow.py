import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import worker


class FakeDb:
    def __init__(self, submissions):
        self.submissions = {submission.id: submission for submission in submissions}

    def get(self, model, identifier):
        return self.submissions.get(identifier)


class WorkerFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.artifact_patch = patch.object(worker, "ARTIFACTS", Path(self.temp.name))
        self.artifact_patch.start()

    def tearDown(self):
        self.artifact_patch.stop()
        self.temp.cleanup()

    def submission(self, identifier, source):
        return SimpleNamespace(id=identifier, language="python", source=source,
                               status="queued", artifact=None, compile_error=None)

    def test_compile_and_complete_match(self):
        source = Path("examples/bot_greedy.py").read_text(encoding="utf-8")
        a = self.submission(1, source)
        b = self.submission(2, source)
        db = FakeDb([a, b])
        worker.compile_submission(db, a)
        worker.compile_submission(db, b)
        self.assertEqual((a.status, b.status), ("ready", "ready"))

        match = SimpleNamespace(submission_a=1, submission_b=2, player_a=1,
                                player_b=2, status="queued", detail=None,
                                winner_id=None, points_a=0, points_b=0)
        worker.play_match(db, match)
        self.assertEqual(match.status, "done")
        self.assertAlmostEqual(match.points_a + match.points_b, 1)
        detail = json.loads(match.detail)
        self.assertGreater(len(detail["turns"]), 2)
        self.assertEqual(detail["turns"][0]["side"], "south")
        self.assertEqual(detail["turns"][1]["side"], "north")

    def test_partial_output_loses_without_hanging(self):
        bad = self.submission(3, "import time\ndef choose_move(state):\n print('{', end='', flush=True)\n time.sleep(5)\n")
        good = self.submission(4, Path("examples/bot_greedy.py").read_text(encoding="utf-8"))
        db = FakeDb([bad, good])
        worker.compile_submission(db, bad)
        worker.compile_submission(db, good)
        match = SimpleNamespace(submission_a=3, submission_b=4, player_a=3,
                                player_b=4, status="queued", detail=None,
                                winner_id=None, points_a=0, points_b=0)
        with patch.dict(os.environ, {"MATCH_FIRST_MOVE_TIMEOUT_SECONDS": "0.5"}):
            worker.play_match(db, match)
        self.assertEqual(match.status, "done")
        self.assertEqual(match.winner_id, 4)
        self.assertEqual(json.loads(match.detail)["win_reason"], "invalid_move_or_timeout")

    def test_exited_bot_loses_instead_of_failing_match(self):
        bad = self.submission(5, "def choose_move(state):\n raise SystemExit(0)\n")
        good = self.submission(6, Path("examples/bot_greedy.py").read_text(encoding="utf-8"))
        db = FakeDb([bad, good])
        worker.compile_submission(db, bad)
        worker.compile_submission(db, good)
        match = SimpleNamespace(submission_a=5, submission_b=6, player_a=5,
                                player_b=6, status="queued", detail=None,
                                winner_id=None, points_a=0, points_b=0)
        worker.play_match(db, match)
        self.assertEqual(match.status, "done")
        self.assertEqual(match.winner_id, 6)

    def test_tournament_protocol_validation_both_sides_and_cpp_game(self):
        a=self.submission(7,Path("examples/bot_greedy.py").read_text())
        b=self.submission(8,Path("examples/bot_first.cpp").read_text())
        b.language="cpp"
        db=FakeDb([a,b])
        worker.compile_submission(db,a,validate=True)
        worker.compile_submission(db,b,validate=True)
        self.assertEqual((a.status,b.status),("ready","ready"),(a.compile_error,b.compile_error))
        match=SimpleNamespace(submission_a=7,submission_b=8,player_a=7,player_b=8,status="queued",detail=None,
                              winner_id=None,points_a=0,points_b=0)
        worker.play_match(db,match)
        self.assertEqual(match.status,"done")
        self.assertGreater(len(json.loads(match.detail)["turns"]),2)

    def test_tournament_protocol_rejects_one_shot_and_extra_output(self):
        sources=[
            'calls=0\ndef choose_move(state):\n global calls\n calls+=1\n if calls>1: raise SystemExit(0)\n return state["legal_moves"][0]\n',
            'import json\ndef choose_move(state):\n m=state["legal_moves"][0]\n print(json.dumps({"piece_id":m["piece_id"],"to":m["to"]}),flush=True)\n return m\n']
        for i,source in enumerate(sources,9):
            submission=self.submission(i,source)
            worker.compile_submission(FakeDb([submission]),submission,validate=True)
            self.assertEqual(submission.status,"failed")


if __name__ == "__main__":
    unittest.main()
