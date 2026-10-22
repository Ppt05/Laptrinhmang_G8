"""Local C++/Python callback harnesses. No database or organizer credentials."""
import ast
import base64
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from .sandbox_runtime import sandbox_args

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / 'sandbox'
MAX_SOURCE = 100_000
MAX_ARTIFACT = 5_000_000


def validate_source(language, source):
    if language not in ('python', 'cpp'):
        raise ValueError('Chọn Python hoặc C++17.')
    if not isinstance(source, str) or not 0 < len(source.encode('utf-8')) <= MAX_SOURCE or '\0' in source:
        raise ValueError('Source phải là UTF-8, từ 1 đến 100.000 byte.')


def stop_process(proc):
    closed_job = False
    if os.name == 'nt':
        from .local_process import close_job
        closed_job = close_job(proc)
    if not closed_job and proc.poll() is None:
        try:
            if os.name == 'posix':
                os.killpg(proc.pid, signal.SIGKILL)
            elif os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            else:
                proc.kill()
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    if getattr(proc, 'sandbox_name', None):
        try:
            subprocess.run(['docker', 'rm', '-f', proc.sandbox_name],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass


def spawn(command, **options):
    proc = subprocess.Popen(command, start_new_session=os.name == 'posix',
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                            **options)
    if os.name == 'nt':
        from .local_process import attach_job
        try:
            attach_job(proc)
        except (AttributeError,OSError,TypeError):
            pass
    return proc


def docker_command(mode, language, image, debug=False):
    name = 'ott-local-' + secrets.token_hex(10)
    args = sandbox_args() + ['--name', name]
    if debug:
        args += ['-e', 'OTT_LOCAL_DEBUG=1']
    return args + [image, mode, language], name


def compile_bot(language, source, directory, backend='native', image='oantuti-sandbox:dev'):
    validate_source(language, source)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if backend == 'docker':
        if not shutil.which('docker'):
            raise ValueError('Không tìm thấy Docker. Cài Docker hoặc chọn Native.')
        command, name = docker_command('compile', language, image)
        proc = spawn(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.sandbox_name = name
        try:
            output, errors = proc.communicate(source.encode('utf-8'), timeout=35)
            if not output.strip():
                raise ValueError('Docker chưa sẵn sàng hoặc thiếu image sandbox. ' + errors.decode('utf-8', 'replace')[-3000:])
            result = json.loads(output.decode('utf-8', 'replace').splitlines()[-1])
            if not result.get('ok'):
                raise ValueError(result.get('error', 'Biên dịch thất bại.'))
            artifact = base64.b64decode(result['artifact'], validate=True)
            if len(artifact) > MAX_ARTIFACT:
                raise ValueError('Artifact vượt 5 MB.')
            target = directory / 'artifact.bin'
            target.write_bytes(artifact)
            return target
        except subprocess.TimeoutExpired:
            raise ValueError('Biên dịch quá 35 giây.')
        finally:
            stop_process(proc)
    if language == 'python':
        tree = ast.parse(source, 'submission.py')
        if not any(isinstance(node, ast.FunctionDef) and node.name == 'choose_move' for node in tree.body):
            raise ValueError('Cần khai báo def choose_move(state).')
        wrapped = source + '\n\n' + (SUPPORT / 'python_main.py').read_text(encoding='utf-8')
        compile(wrapped, 'submission.py', 'exec')
        target = directory / 'main.py'
        target.write_text(wrapped, encoding='utf-8')
        return target
    compiler = shutil.which(os.environ.get('CXX', 'g++'))
    if not compiler:
        raise ValueError('Không tìm thấy g++. Cài compiler C++17, đặt CXX hoặc chọn Docker.')
    src = directory / 'main.cpp'
    target = directory / ('program.exe' if os.name == 'nt' else 'program')
    wrapped = '#include "cpp_bot.hpp"\n#line 1 "submission.cpp"\n' + source + '\n#include "cpp_main.hpp"\n'
    src.write_text(wrapped, encoding='utf-8')
    # Redirect diagnostics to disk so a broken source cannot fill Python RAM.
    with (directory / 'compile.log').open('w+b') as diagnostics:
        proc = spawn([compiler, '-std=c++17', '-O2', '-pipe', '-static', '-s', '-I', str(SUPPORT),
                      str(src), '-o', str(target)], stdout=diagnostics, stderr=diagnostics)
        try:
            proc.wait(timeout=25)
        except subprocess.TimeoutExpired:
            raise ValueError('Biên dịch C++ quá 25 giây.')
        finally:
            stop_process(proc)
        diagnostics.seek(max(0, diagnostics.tell() - 6000))
        error = diagnostics.read().decode('utf-8', 'replace')
    if proc.returncode:
        raise ValueError(error or 'Biên dịch C++ thất bại.')
    if target.stat().st_size > MAX_ARTIFACT:
        raise ValueError('Artifact vượt 5 MB.')
    return target


class BotProcess:
    def __init__(self, language, artifact, backend='native', image='oantuti-sandbox:dev'):
        if backend == 'docker':
            command, name = docker_command('run', language, image, debug=True)
        else:
            command = [sys.executable, '-I', '-S', '-u', '-X', 'utf8', str(artifact)] if language == 'python' else [str(artifact)]
            name = None
        self.started = time.perf_counter()
        self.proc = spawn(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd=str(Path(artifact).parent), bufsize=0)
        self.proc.sandbox_name = name
        self.output = queue.Queue(maxsize=128)
        self.log_lock = threading.Lock()
        self.logs = bytearray()
        self.pending = bytearray()
        self.closed = False
        self.close_lock = threading.Lock()
        self.first = True
        self.flood = False
        self.readers = [threading.Thread(target=self._read_output, daemon=True),
                        threading.Thread(target=self._read_logs, daemon=True)]
        for reader in self.readers:
            reader.start()
        if backend == 'docker':
            try:
                data = Path(artifact).read_bytes()
                self.proc.stdin.write(struct.pack('>I', len(data)) + data)
                self.proc.stdin.flush()
            except Exception:
                self.close()
                raise

    def _read_output(self):
        try:
            while True:
                chunk = os.read(self.proc.stdout.fileno(), 256)
                try:
                    self.output.put_nowait(chunk)
                except queue.Full:
                    self.flood = True
                    self.proc.kill()
                    return
                if not chunk:
                    return
        except (OSError, ValueError):
            pass

    def _read_logs(self):
        try:
            while chunk := os.read(self.proc.stderr.fileno(), 4096):
                with self.log_lock:
                    self.logs.extend(chunk)
                    del self.logs[:-65536]
        except (OSError, ValueError):
            pass

    def log(self):
        with self.log_lock:
            return bytes(self.logs).decode('utf-8', 'replace')

    def _unsolicited(self):
        if self.pending or self.flood:
            return True
        try:
            chunk = self.output.get_nowait()
            self.pending.extend(chunk)
            return bool(chunk)
        except queue.Empty:
            return False

    def request(self, observation, move_timeout=1.5, first_timeout=15):
        if self._unsolicited():
            raise ValueError('Bot gửi stdout khi chưa được hỏi.')
        allowance = max(0, first_timeout - (time.perf_counter() - self.started)) if self.first else move_timeout
        self.first = False
        begin = time.perf_counter()
        try:
            payload = (json.dumps(observation, separators=(',', ':')) + '\n').encode()
            written = queue.Queue(maxsize=1)

            def write_input():
                try:
                    offset = 0
                    while offset < len(payload):
                        count = self.proc.stdin.write(payload[offset:])
                        if not count:
                            raise BrokenPipeError('stdin đã đóng')
                        offset += count
                    self.proc.stdin.flush()
                    written.put(None)
                except (OSError, ValueError) as error:
                    written.put(error)

            # A bot can hang at module startup and never read stdin. A blocking
            # pipe write must not bypass the move timeout or freeze cancellation.
            writer = threading.Thread(target=write_input, daemon=True)
            writer.start()
            try:
                write_error = written.get(timeout=max(.001, allowance-(time.perf_counter()-begin)))
            except queue.Empty:
                stop_process(self.proc)
                writer.join(timeout=1)
                raise ValueError('Bot quá thời gian đọc observation.')
            if write_error:
                raise ValueError('Không gửi được observation: '+str(write_error))
            while b'\n' not in self.pending:
                if len(self.pending) >= 256 or self.flood:
                    raise ValueError('Phản hồi vượt 256 byte hoặc bot gửi quá nhiều stdout.')
                remaining = allowance - (time.perf_counter() - begin)
                if remaining <= 0:
                    raise ValueError('Bot quá thời gian suy nghĩ.')
                try:
                    chunk = self.output.get(timeout=remaining)
                except queue.Empty:
                    raise ValueError('Bot quá thời gian suy nghĩ.')
                if not chunk:
                    raise ValueError('Bot thoát trước khi trả lời.')
                self.pending.extend(chunk)
            newline = self.pending.index(b'\n') + 1
            line = bytes(self.pending[:newline])
            del self.pending[:newline]
            if self._unsolicited():
                raise ValueError('Bot gửi nhiều hơn một phản hồi.')
            return line, (time.perf_counter() - begin) * 1000
        except (BrokenPipeError, OSError) as error:
            raise ValueError('Bot không còn chạy: ' + str(error))

    def close(self):
        with self.close_lock:
            if self.closed:
                return
            stop_process(self.proc)
            for reader in self.readers:
                reader.join(timeout=1)
            for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                if pipe:
                    pipe.close()
            self.closed = True
