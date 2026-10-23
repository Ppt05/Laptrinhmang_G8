"""Run `python -m app.local` for the private, database-free local bot lab."""
import argparse
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .game_engine import MatchEngine, parse_reply
from .local_runtime import ROOT, BotProcess, compile_bot, validate_source

SAMPLES = {'first': ('python', 'bot_first.py'), 'greedy': ('python', 'bot_greedy.py'),
           'random': ('python', 'bot_random.py'), 'first-cpp': ('cpp', 'bot_first.cpp')}


class LocalSession:
    def __init__(self, specifications, backend='native', image='oantuti-sandbox:dev', max_moves=200):
        if backend not in ('native', 'docker'):
            raise ValueError('Backend không hợp lệ.')
        if type(max_moves) is not int or not 1 <= max_moves <= 200:
            raise ValueError('Giới hạn ván phải từ 1 đến 200 lượt.')
        if not isinstance(specifications, list) or len(specifications) != 2:
            raise ValueError('Cần hai bot đối kháng.')
        self.specs = []
        for item in specifications:
            if not isinstance(item, dict):
                raise ValueError('Bot không hợp lệ.')
            language, source = item.get('language'), item.get('source')
            validate_source(language, source)
            self.specs.append({'name': str(item.get('name', 'Bot'))[:120],
                               'language': language, 'source': source,
                               'sha256': hashlib.sha256(source.encode()).hexdigest()})
        self.id = secrets.token_hex(12)
        self.backend, self.image = backend, image
        self.engine = MatchEngine(max_moves)
        self.directory = tempfile.TemporaryDirectory(prefix='ott-local-')
        self.condition = threading.Condition(threading.RLock())
        self.cancelled = threading.Event()
        self.status = 'compiling'
        self.error = None
        self.auto = False
        self.credits = 0
        self.delay = .2
        self.bots = {}
        self.artifacts = []
        self.history = [{'game': copy.deepcopy(self.engine.game), 'observation': None,
                         'action': None, 'elapsed_ms': None, 'error': None}]
        self.thread = threading.Thread(target=self._loop, name='local-match', daemon=True)
        self.thread.start()

    def _loop(self):
        try:
            for index, spec in enumerate(self.specs):
                if self.cancelled.is_set():
                    return
                try:
                    artifact = compile_bot(spec['language'], spec['source'],
                        Path(self.directory.name) / str(index), self.backend, self.image)
                    self.artifacts.append(artifact)
                except Exception as error:
                    raise ValueError(f"Bot {'A' if index == 0 else 'B'} ({spec['name']}): {error}") from error
            with self.condition:
                self.status = 'paused'
                self.condition.notify_all()
            while not self.cancelled.is_set():
                with self.condition:
                    self.condition.wait_for(lambda: self.cancelled.is_set() or self.auto or self.credits)
                    if self.cancelled.is_set():
                        break
                    if self.credits:
                        self.credits -= 1
                    observation = self.engine.observation()
                    if observation is None:
                        break
                    self.status = 'running'
                side = observation['side']
                index = 0 if side == 'south' else 1
                if side not in self.bots:
                    bot = BotProcess(self.specs[index]['language'], self.artifacts[index], self.backend, self.image)
                    with self.condition:
                        self.bots[side] = bot
                bot = self.bots[side]
                raw, elapsed, problem, selected = None, None, None, None
                try:
                    raw, elapsed = bot.request(observation)
                    selected = parse_reply(raw, observation['legal_moves'])
                    if selected is None:
                        problem = 'Bot trả JSON sai định dạng hoặc nước đi không hợp lệ.'
                except ValueError as error:
                    problem = str(error)
                if self.cancelled.is_set():
                    break
                if selected is None and self.backend == 'docker' and bot.proc.poll() in (64, 70, 71):
                    raise ValueError('Docker không khởi chạy được bot; đây là lỗi hạ tầng local.')
                with self.condition:
                    if selected is None:
                        self.engine.forfeit()
                    else:
                        self.engine.advance(selected)
                    self.history.append({'game': copy.deepcopy(self.engine.game),
                        'observation': observation, 'action': selected,
                        'raw': raw.decode('utf-8', 'replace') if raw else None,
                        'elapsed_ms': round(elapsed, 3) if elapsed is not None else None,
                        'error': problem})
                    if self.engine.finished:
                        self.status = 'done'
                        break
                    self.status = 'running' if self.auto else 'paused'
                    self.condition.notify_all()
                    if self.auto:
                        self.condition.wait(timeout=self.delay)
        except Exception as error:
            with self.condition:
                self.error = str(error)[:6000]
                self.status = 'error'
        finally:
            for bot in list(self.bots.values()):
                bot.close()
            self.directory.cleanup()
            with self.condition:
                if self.cancelled.is_set():
                    self.status = 'cancelled'
                elif self.status not in ('done', 'error'):
                    self.status = 'done'
                self.condition.notify_all()

    def command(self, action, delay=.2):
        if action == 'stop':
            self.cancelled.set()
            with self.condition:
                self.auto = False
                self.condition.notify_all()
                bots = list(self.bots.values())
            for bot in bots:
                bot.close()
            self.thread.join(timeout=3)
            return
        with self.condition:
            if self.status not in ('paused', 'running'):
                raise ValueError('Đợi biên dịch hoàn tất hoặc tạo ván mới.')
            if action == 'step':
                self.auto = False
                self.credits = 1
            elif action == 'run':
                if not isinstance(delay, (int, float)) or not 0 <= delay <= 2:
                    raise ValueError('Tốc độ phát không hợp lệ.')
                self.auto = True
                self.delay = float(delay)
            elif action == 'pause':
                self.auto = False
                self.credits = 0
            else:
                raise ValueError('Lệnh điều khiển không hợp lệ.')
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            result = {'id': self.id, 'status': self.status, 'error': self.error,
                      'backend': self.backend, 'max_moves': self.engine.max_moves,
                      'busy': self.thread.is_alive(),
                      'bots': [{k: v for k, v in spec.items() if k != 'source'} for spec in self.specs],
                      'history': copy.deepcopy(self.history), 'detail': copy.deepcopy(self.engine.detail()),
                      'logs': {side: bot.log() for side, bot in self.bots.items()}}
            return result


class LabServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port=8765, backend='native', image='oantuti-sandbox:dev'):
        super().__init__(('127.0.0.1', port), LabHandler)
        self.token = secrets.token_hex(32)
        self.backend, self.image = backend, image
        self.session = None
        self.session_lock = threading.Lock()

    def server_close(self):
        with self.session_lock:
            if self.session:
                self.session.command('stop')
        super().server_close()


class LabHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, value, content_type='application/json; charset=utf-8'):
        body = json.dumps(value, ensure_ascii=False).encode() if not isinstance(value, bytes) else value
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed(self, api=False):
        host = self.headers.get('Host', '')
        port = self.server.server_port
        if host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            self.reply(403, {'error': 'Local lab chỉ nhận Host loopback.'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + host:
            self.reply(403, {'error': 'Origin không hợp lệ.'})
            return False
        if api and not secrets.compare_digest(self.headers.get('X-Local-Token', ''), self.server.token):
            self.reply(403, {'error': 'Mở trang local để nhận phiên làm việc.'})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.allowed(api=path.startswith('/api/')):
            return
        if path == '/':
            page = (ROOT / 'web' / 'local.html').read_text(encoding='utf-8')
            self.reply(200, page.replace('__LOCAL_TOKEN__', self.server.token).encode(), 'text/html; charset=utf-8')
        elif path in ('/local.css', '/local.js'):
            kind = 'text/css' if path.endswith('.css') else 'text/javascript'
            self.reply(200, (ROOT / 'web' / path[1:]).read_bytes(), kind + '; charset=utf-8')
        elif path == '/api/config':
            samples = {key: {'language': lang, 'name': filename,
                       'source': (ROOT / 'examples' / filename).read_text(encoding='utf-8')}
                       for key, (lang, filename) in SAMPLES.items()}
            self.reply(200, {'backend': self.server.backend, 'docker': bool(shutil.which('docker')),
                'cpp': bool(shutil.which(os.environ.get('CXX', 'g++'))),
                'initial': MatchEngine().game, 'samples': samples})
        elif path in ('/api/session', '/api/replay'):
            with self.server.session_lock:
                session = self.server.session
            if not session:
                self.reply(200, None)
            else:
                result = session.snapshot()
                if path == '/api/replay':
                    result['format'] = 'ott-local-v1'
                self.reply(200, result)
        else:
            self.reply(404, {'error': 'Không tìm thấy.'})

    def do_POST(self):
        if not self.allowed(api=True):
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1_500_000:
                raise ValueError('Yêu cầu quá lớn hoặc không có nội dung.')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Yêu cầu phải là JSON object.')
            path = urlsplit(self.path).path
            with self.server.session_lock:
                if path == '/api/session':
                    old = self.server.session
                    if old and old.thread.is_alive():
                        raise ValueError('Dừng ván hiện tại và đợi nó kết thúc trước khi tạo ván mới.')
                    self.server.session = LocalSession(body.get('bots'), body.get('backend', self.server.backend),
                        self.server.image, body.get('max_moves', 200))
                    result = {'id': self.server.session.id}
                elif path == '/api/control':
                    session = self.server.session
                    if not session or body.get('id') != session.id:
                        raise ValueError('Phiên local đã thay đổi; tải lại trạng thái.')
                    session.command(body.get('action'), body.get('delay', .2))
                    result = {'ok': True}
                else:
                    self.reply(404, {'error': 'Không tìm thấy.'})
                    return
            self.reply(200, result)
        except (ValueError, TypeError, KeyError, UnicodeError) as error:
            self.reply(400, {'error': str(error)[:6000]})


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Thử hai bot G8 trên máy của bạn, không cần database.')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--backend', choices=('native', 'docker'), default='native')
    parser.add_argument('--image', default='oantuti-sandbox:dev')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('port phải từ 0 đến 65535')
    server = LabServer(args.port, args.backend, args.image)
    print(f'Local bot lab: http://127.0.0.1:{server.server_port}', flush=True)
    print('Ctrl+C để dừng. Source và kết quả ở trên máy này, không gửi lên BTC.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
