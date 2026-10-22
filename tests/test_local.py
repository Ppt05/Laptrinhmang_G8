"""Real local bot execution and loopback API; no database or Docker required."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.game_engine import MatchEngine, legal_moves, parse_reply
from app.local import LabServer, LocalSession
from app.local_runtime import BotProcess, ROOT, compile_bot, docker_command

FIRST = 'def choose_move(state):\n return state["legal_moves"][0]\n'
GREEDY = (ROOT / 'examples/bot_greedy.py').read_text(encoding='utf-8')


def specification(source=FIRST, language='python', name='First'):
    return {'source': source, 'language': language, 'name': name}


def wait_for(predicate, seconds=12):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if result := predicate():
            return result
        time.sleep(.02)
    raise AssertionError('Timed out waiting for local runner')


class EngineTests(unittest.TestCase):
    def test_initial_moves_do_not_mutate_game_and_protocol_is_strict(self):
        engine = MatchEngine()
        observation = engine.observation()
        self.assertEqual(len(observation['pieces']), 20)
        self.assertEqual(observation['side'], 'south')
        observation['pieces'].clear()
        self.assertEqual(len(engine.game['pieces']), 20)
        choices = legal_moves(engine.game, 'south')
        valid = {'piece_id': choices[0]['piece_id'], 'to': choices[0]['to']}
        self.assertEqual(parse_reply((json.dumps(valid)+'\n').encode(), choices), choices[0])
        self.assertIsNone(parse_reply(b'{"piece_id":"S01","piece_id":"S02","to":"a1"}\n', choices))

    def test_move_limit_repetition_and_forfeit_are_recorded(self):
        engine = MatchEngine(1)
        engine.advance(engine.observation()['legal_moves'][0])
        self.assertEqual(engine.game['win_reason'], 'move_limit_draw')
        self.assertEqual(len(engine.detail()['turns']), 1)
        engine = MatchEngine()
        for square in ('a5', 'd8', 'b5', 'e8') * 2:
            piece = 'S01' if engine.game['turn'] == 'south' else 'N01'
            engine.advance({'piece_id': piece, 'to': square})
        self.assertEqual(engine.game['win_reason'], 'threefold_repetition_draw')
        engine = MatchEngine()
        engine.advance({'piece_id': 'N01', 'to': 'a1'})
        self.assertEqual(engine.game['winner'], 'north')
        self.assertEqual(engine.detail()['turns'][0]['error'], 'invalid_move_or_timeout')

    def test_goal_and_elimination(self):
        engine = MatchEngine()
        engine.game['pieces'] = [dict(id='S', side='south', type='rock', x=7, y=8),
                                 dict(id='N', side='north', type='paper', x=0, y=1)]
        engine.advance({'piece_id':'S','to':'i9'})
        self.assertEqual((engine.game['winner'],engine.game['win_reason']),('south','goal'))
        engine = MatchEngine()
        engine.game['pieces'] = [dict(id='S', side='south', type='rock', x=1, y=1),
                                 dict(id='N', side='north', type='scissors', x=2, y=2)]
        engine.advance({'piece_id':'S','to':'c3'})
        self.assertEqual(engine.game['win_reason'],'elimination')


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.bots = []

    def tearDown(self):
        for bot in self.bots:
            bot.close()
        self.directory.cleanup()

    def bot(self, source, language='python'):
        artifact = compile_bot(language, source, Path(self.directory.name)/str(len(self.bots)))
        bot = BotProcess(language, artifact)
        self.bots.append(bot)
        return bot

    def test_python_globals_and_stderr_survive_multiple_turns(self):
        source = 'import sys\ncalls=0\ndef choose_move(state):\n global calls\n calls+=1\n print("calls",calls,file=sys.stderr,flush=True)\n return state["legal_moves"][calls-1]\n'
        bot = self.bot(source)
        observation = MatchEngine().observation()
        for index in range(3):
            raw, _ = bot.request(observation)
            self.assertEqual(parse_reply(raw, observation['legal_moves']), observation['legal_moves'][index])
        wait_for(lambda:'calls 3' in bot.log())

    @unittest.skipUnless(shutil.which(os.environ.get('CXX','g++')), 'g++ required')
    def test_cpp_real_harness_with_globals_and_stderr(self):
        source = 'int calls=0;\nMove chooseMove(const BoardState& state) { std::cerr << "calls=" << ++calls << std::endl; return state.legal_moves.at(calls-1); }'
        bot = self.bot(source, 'cpp')
        observation = MatchEngine().observation()
        for index in range(2):
            raw, _ = bot.request(observation)
            self.assertEqual(parse_reply(raw, observation['legal_moves']), observation['legal_moves'][index])
        wait_for(lambda:'calls=2' in bot.log())

    def test_timeout_crash_extra_output_and_log_flood_are_bounded(self):
        observation = MatchEngine().observation()
        hung = self.bot('import time\ndef choose_move(state):\n time.sleep(3)\n return state["legal_moves"][0]\n')
        with self.assertRaisesRegex(ValueError, 'thời gian'):
            hung.request(observation, first_timeout=.15)
        crashed = self.bot('def choose_move(state):\n raise RuntimeError("test crash")\n')
        with self.assertRaises(ValueError):
            crashed.request(observation)
        wait_for(lambda:'test crash' in crashed.log())
        flood = self.bot('import sys\ndef choose_move(state):\n print("x"*100000,file=sys.stderr,flush=True)\n return state["legal_moves"][0]\n')
        raw, _ = flood.request(observation)
        self.assertIsNotNone(parse_reply(raw, observation['legal_moves']))
        wait_for(lambda:len(flood.log())>60000)
        self.assertLessEqual(len(flood.log().encode()),65536)
        extra = self.bot('import json\ndef choose_move(state):\n m=state["legal_moves"][0]\n print(json.dumps({"piece_id":m["piece_id"],"to":m["to"]}),flush=True)\n return m\n')
        try:
            extra.request(observation)
        except ValueError:
            return
        # Separate writes may race with the first read; next turn rejects leftovers.
        time.sleep(.05)
        with self.assertRaises(ValueError):
            extra.request(observation)

    def test_compile_error_and_docker_arguments(self):
        with self.assertRaises((SyntaxError,ValueError)):
            self.bot('def choose_move(:')
        with self.assertRaises(ValueError):
            self.bot('def other(): pass')
        command, name = docker_command('run','cpp','test@sha256:abc',debug=True)
        self.assertIn('--read-only',command)
        for flag,value in [('--network','none'),('--memory','256m'),('--cpus','1'),('--pids-limit','32')]:
            self.assertEqual(command[command.index(flag)+1],value)
        self.assertIn('OTT_LOCAL_DEBUG=1',command)
        self.assertIn(name,command)

    def test_bot_hanging_before_reading_input_still_times_out(self):
        bot=self.bot('while True: pass\ndef choose_move(state): return state["legal_moves"][0]\n')
        begin=time.monotonic()
        with self.assertRaisesRegex(ValueError,'thời gian'):
            bot.request(MatchEngine().observation(),first_timeout=.15)
        self.assertLess(time.monotonic()-begin,3)

    @unittest.skipUnless(os.name=='nt','Windows job object cleanup')
    def test_windows_cleanup_also_stops_native_bot_children(self):
        import ctypes
        from ctypes import wintypes
        source='import subprocess,sys\nchild=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"])\ndef choose_move(state):\n print(child.pid,file=sys.stderr,flush=True)\n return state["legal_moves"][0]\n'
        bot=self.bot(source)
        if not getattr(bot.proc,'local_job',None):
            self.skipTest('Windows job assignment unavailable on this host')
        bot.request(MatchEngine().observation())
        child_pid=int(wait_for(lambda:bot.log().strip()))
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x100000,False,child_pid)
        self.assertTrue(handle)
        try:
            bot.close()
            self.assertEqual(kernel.WaitForSingleObject(handle,2000),0)
        finally:
            kernel.CloseHandle(handle)


class SessionTests(unittest.TestCase):
    def session(self, *bots, max_moves=30):
        session = LocalSession(list(bots),max_moves=max_moves)
        self.addCleanup(session.command,'stop')
        wait_for(lambda:session.status in ('paused','error'))
        self.assertEqual(session.status,'paused',session.error)
        return session

    def test_step_pause_complete_and_replay_reconstruction(self):
        session = self.session(specification(GREEDY,name='V2'),specification(FIRST,name='V1'))
        self.assertEqual(len(session.snapshot()['history']),1)
        session.command('step')
        wait_for(lambda:len(session.snapshot()['history'])==2)
        time.sleep(.05)
        self.assertEqual(session.status,'paused')
        self.assertEqual(len(session.snapshot()['history']),2)
        session.command('run',0)
        wait_for(lambda:not session.thread.is_alive())
        snapshot = session.snapshot()
        self.assertEqual(snapshot['status'],'done',snapshot['error'])
        replay = MatchEngine(30)
        for frame in snapshot['history'][1:]:
            replay.advance(frame['action'])
            self.assertEqual(replay.game,frame['game'])
        self.assertEqual(replay.detail(),snapshot['detail'])
        self.assertNotIn('source',snapshot['bots'][0])

    def test_stop_hung_bot_and_compile_failure(self):
        session = self.session(specification('import time\ndef choose_move(state):\n time.sleep(20)\n'),specification())
        session.command('step')
        wait_for(lambda:bool(session.bots))
        begin = time.monotonic()
        session.command('stop')
        wait_for(lambda:not session.thread.is_alive())
        self.assertLess(time.monotonic()-begin,5)
        self.assertEqual(session.status,'cancelled')
        self.assertTrue(all(bot.proc.poll() is not None for bot in session.bots.values()))
        failed=LocalSession([specification('def broken(:'),specification()])
        self.addCleanup(failed.command,'stop')
        wait_for(lambda:not failed.thread.is_alive())
        self.assertEqual(failed.status,'error')
        self.assertTrue(failed.error)

    def test_invalid_move_is_a_bot_forfeit(self):
        session=self.session(specification('def choose_move(state):\n return {"piece_id":"fake","to":"a1"}\n'),specification())
        session.command('run',0)
        wait_for(lambda:not session.thread.is_alive())
        result=session.snapshot()
        self.assertEqual(result['detail']['winner_side'],'north')
        self.assertTrue(result['history'][-1]['error'])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.server=LabServer(0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self,path,body=None,headers=None,authenticated=True):
        supplied={'X-Local-Token':self.server.token} if authenticated else {}
        supplied.update(headers or {})
        return urlopen(Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,
                       headers=supplied),timeout=10)

    def test_local_page_and_api_reject_cross_origin_and_untrusted_host(self):
        with self.request('/',authenticated=False) as response:
            page=response.read().decode()
            self.assertIn(self.server.token,page)
            self.assertNotIn('__LOCAL_TOKEN__',page)
            self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
        for path,headers,auth in [('/api/config',{},False),('/',{'Host':'evil.example'},False),
                                  ('/api/config',{'Origin':'https://evil.example'},True)]:
            with self.assertRaises(HTTPError) as error:
                self.request(path,headers=headers,authenticated=auth)
            self.assertEqual(error.exception.code,403)
        with self.request('/api/config') as response:
            result=json.load(response)
            self.assertEqual(len(result['initial']['pieces']),20)
            self.assertIn('greedy',result['samples'])

    def test_http_session_step_and_replay(self):
        with self.request('/api/session',{'bots':[specification(),specification()], 'max_moves':2}) as response:
            identifier=json.load(response)['id']
        wait_for(lambda:self.server.session.status=='paused')
        with self.request('/api/control',{'id':identifier,'action':'run','delay':0}) as response:
            self.assertTrue(json.load(response)['ok'])
        wait_for(lambda:not self.server.session.thread.is_alive())
        with self.request('/api/replay') as response:
            replay=json.load(response)
            self.assertEqual(replay['format'],'ott-local-v1')
            self.assertEqual(len(replay['history']),3)
            self.assertEqual(replay['detail']['win_reason'],'move_limit_draw')
        with self.assertRaises(HTTPError):
            self.request('/api/control',{'id':'stale','action':'step'})


if __name__=='__main__':
    unittest.main()
