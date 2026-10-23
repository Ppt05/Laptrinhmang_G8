import base64, json, logging, os, queue, re, secrets, selectors, struct, subprocess, tempfile, threading, time, weakref
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import select, text, func
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from .db import SessionLocal, engine
from .models import Job, Match, Submission, WorkerNode, RoundSubmission, TournamentRound, GameAttempt
from . import tournament, daily
from .game_setup import coord, new_game, square
from .game_engine import BEATS, legal_moves, apply_move, position_key, parse_reply, MatchEngine
from .event_schedule import get_event_state, submission_day_bounds
from .schema import ensure_schema
from .sandbox_runtime import sandbox_args

DATA = Path(os.getenv("APP_DATA", "/app/data")); ARTIFACTS = DATA / "artifacts"
IMAGE = os.getenv("SANDBOX_IMAGE", "oantuti-sandbox:dev")
JUDGE_ENV = ContextVar("judge_environment", default=None)
WORKER_ID = os.getenv("WORKER_ID", "may-1")
# OTTv2 tactical board game: coordinates use a1=(0,0), i9=(8,8).
_PIPE_READERS = weakref.WeakKeyDictionary()
_PIPE_READERS_LOCK = threading.Lock()

def _pipe_reader(stream):
    """Windows cannot select() on subprocess pipes; drain each pipe in a thread."""
    with _PIPE_READERS_LOCK:
        reader = _PIPE_READERS.get(stream)
        if reader is None:
            reader = queue.Queue()
            _PIPE_READERS[stream] = reader

            def pump():
                try:
                    while True:
                        chunk = os.read(stream.fileno(), 256)
                        reader.put(chunk)
                        if not chunk:
                            return
                except OSError:
                    reader.put(None)

            threading.Thread(target=pump, daemon=True).start()
        return reader

def docker_cmd(mode, lang):
    env = JUDGE_ENV.get()
    name = "ott-bot-" + secrets.token_hex(12)
    cmd = sandbox_args() + ["--name", name, env["image"] if env else IMAGE, mode, lang]
    # Compilation uses communicate(), which drains both pipes. Preserve Docker
    # startup errors instead of hiding missing images/socket failures.
    stderr = subprocess.PIPE if mode == "compile" else subprocess.DEVNULL
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, bufsize=0)
    proc.sandbox_name = name
    return proc

def compile_submission(db: Session, s: Submission, validate=False):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    proc = docker_cmd("compile", s.language)
    try:
        out, errors = proc.communicate(s.source.encode(), timeout=35)
    except subprocess.TimeoutExpired:
        stop_bot(proc)
        raise RuntimeError("Worker chờ biên dịch quá 35 giây; cần kiểm tra hạ tầng")
    if not out.strip():
        detail = errors.decode("utf-8", "replace").strip()[:1500] if errors else "Không có thông tin stderr"
        raise RuntimeError(f"Sandbox biên dịch không trả kết quả (exit {proc.returncode}): {detail}")
    result = json.loads(out.decode("utf-8", "replace").splitlines()[-1])
    if not result.get("ok"):
        s.status = "failed"; s.compile_error = result.get("error", "Biên dịch thất bại")[:6000]
    else:
        data = base64.b64decode(result["artifact"], validate=True)
        target = ARTIFACTS / f"submission-{s.id}.bin"; target.write_bytes(data)
        s.artifact = target.name; s.status = "ready"; s.compile_error = None
        if validate:
            error = validate_protocol(s)
            if error:
                s.status, s.compile_error = "failed", error

def ensure_local_artifact(db: Session, s: Submission):
    # Artifacts live in each computer's Docker volume. A match assigned to a
    # second computer can rebuild its own copy from the source stored in DB.
    if s.status != "ready":
        return False
    if not s.artifact or not (ARTIFACTS / s.artifact).is_file():
        original = s.status, s.artifact, s.compile_error
        compile_submission(db, s)
        if s.status != "ready":
            error = s.compile_error
            s.status, s.artifact, s.compile_error = original
            raise RuntimeError("Không dựng lại được artifact bot đã khóa: " + str(error))
    return s.status == "ready"

def start_bot(s):
    artifact = ARTIFACTS / s.artifact
    data = artifact.read_bytes()
    proc = docker_cmd("run", s.language)
    try:
        # Send the compiled artifact through stdin before the bot protocol starts.
        # This works with Docker's read-only rootfs, where `docker cp` is rejected.
        proc.stdin.write(struct.pack(">I", len(data)))
        proc.stdin.write(data)
        proc.stdin.flush()
        return proc, None
    except Exception:
        stop_bot(proc)
        raise

def read_line_timeout(stream, timeout, pending):
    # readline() can block forever after select() sees a partial line. Read
    # available bytes directly and keep any bytes after the newline for later.
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as sel:
        sel.register(stream, selectors.EVENT_READ)
        while True:
            newline = pending.find(b"\n")
            if newline >= 0:
                line = bytes(pending[:newline + 1])
                del pending[:newline + 1]
                return line if len(line) <= 256 else None
            if len(pending) >= 256:
                return None
            remaining = deadline - time.monotonic()
            if os.name == "nt":
                if remaining <= 0:
                    return None
                try:
                    chunk = _pipe_reader(stream).get(timeout=remaining)
                except queue.Empty:
                    return None
                if chunk is None:
                    return None
            else:
                if remaining <= 0 or not sel.select(remaining):
                    return None
                chunk = os.read(stream.fileno(), 256)
            if not chunk:
                return None
            pending.extend(chunk)


def unsolicited(proc, pending):
    if pending:
        return True
    if os.name == "nt":
        try:
            chunk = _pipe_reader(proc.stdout).get_nowait()
        except queue.Empty:
            return False
        if chunk:
            pending.extend(chunk)
            return True
        return False
    with selectors.DefaultSelector() as sel:
        sel.register(proc.stdout, selectors.EVENT_READ)
        if sel.select(0):
            chunk = os.read(proc.stdout.fileno(), 256)
            if chunk:
                pending.extend(chunk)
                return True
    return False


def stop_bot(proc):
    try:
        proc.kill()
        proc.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for pipe in (proc.stdin, proc.stdout):
        if pipe:
            pipe.close()
    if getattr(proc, "sandbox_name", None):
        subprocess.run(["docker", "rm", "-f", proc.sandbox_name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)


def validate_protocol(s):
    """Fresh sandbox per side; three consecutive turns in legal game states."""
    env = JUDGE_ENV.get() or {}
    for side in ("south", "north"):
        started = time.monotonic()
        proc, _ = start_bot(s)
        pending = bytearray()
        game, count = new_game(), 0
        try:
            while count < 3:
                choices = legal_moves(game, game["turn"])
                if not choices or game["winner"]:
                    return "Protocol validation ended before three responses"
                if game["turn"] != side:
                    game = apply_move(game, choices[0])
                    continue
                if unsolicited(proc, pending):
                    return f"{side}: unsolicited output"
                payload = {"protocol_version": 2, "move_number": game["move_number"], "side": side,
                           "board_size": 9, "pieces": game["pieces"], "legal_moves": choices}
                try:
                    proc.stdin.write((json.dumps(payload) + "\n").encode())
                    proc.stdin.flush()
                    timeout = max(0, env.get("first_timeout", 15) - (time.monotonic()-started)) if count == 0 else env.get("move_timeout", 1.5)
                    raw = read_line_timeout(proc.stdout, timeout, pending)
                except (BrokenPipeError, OSError):
                    raw = None
                selected = parse_reply(raw, choices)
                if selected is None:
                    if proc.poll() in (64, 70, 71):
                        raise RuntimeError("Sandbox validation failed to start; infrastructure review required")
                    return f"{side}: invalid protocol, illegal move, exit or timeout"
                if unsolicited(proc, pending) or proc.poll() is not None:
                    return f"{side}: extra response or early exit"
                game = apply_move(game, selected)
                count += 1
        finally:
            stop_bot(proc)
    return None

def play_match(db: Session, m: Match):
    a, b = db.get(Submission, m.submission_a), db.get(Submission, m.submission_b)
    if not a or not b or not ensure_local_artifact(db, a) or not ensure_local_artifact(db, b):
        m.status = "failed"; m.detail = json.dumps({"error": "Bot chưa sẵn sàng"}); return
    processes, output_buffers, started = {}, {}, {}
    env = JUDGE_ENV.get()
    match = MatchEngine(env["max_moves"] if env else int(os.getenv("MATCH_MAX_MOVES", "200")))
    timeout = env["move_timeout"] if env else float(os.getenv("MATCH_MOVE_TIMEOUT_SECONDS", "1.5"))
    first_timeout = env["first_timeout"] if env else float(os.getenv("MATCH_FIRST_MOVE_TIMEOUT_SECONDS", "15"))
    first = {"south": True, "north": True}
    try:
        while (payload := match.observation()) is not None:
            side = payload["side"]
            if side not in processes:
                started[side] = time.monotonic()
                processes[side], _ = start_bot(a if side == "south" else b)
                output_buffers[processes[side].stdout] = bytearray()
            proc = processes[side]
            try:
                if unsolicited(proc, output_buffers[proc.stdout]):
                    raise BrokenPipeError("Unsolicited response")
                proc.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
                proc.stdin.flush()
                allowance = max(0, first_timeout - (time.monotonic() - started[side])) if first[side] else timeout
                raw = read_line_timeout(proc.stdout, allowance, output_buffers[proc.stdout])
            except (BrokenPipeError, OSError):
                raw = None
            first[side] = False
            selected = parse_reply(raw, payload["legal_moves"])
            if selected and unsolicited(proc, output_buffers[proc.stdout]):
                selected = None
            if selected is None:
                if proc.poll() in (64, 70, 71):
                    m.status = "failed"
                    m.detail = json.dumps({"error": "Sandbox không khởi chạy được bot",
                                           "side": side, "exit_code": proc.returncode})
                    return
                match.forfeit()
                break
            match.advance(selected)
        winner = match.game["winner"]
        m.winner_id = m.player_a if winner == "south" else m.player_b if winner == "north" else None
        if getattr(m, "kind", None) == "practice":
            m.points_a = m.points_b = 0
        else:
            m.points_a, m.points_b = (1, 0) if winner == "south" else (0, 1) if winner == "north" else (.5, .5)
        m.detail = json.dumps(match.detail())
        m.status = "done"
    finally:
        for proc in processes.values():
            stop_bot(proc)


def recover_abandoned_jobs(db):
    """Return work from a dead or replaced worker to the shared queue."""
    now = datetime.now(timezone.utc)
    jobs = db.scalars(select(Job).where(Job.status == "running")
                      .order_by(Job.id).with_for_update(skip_locked=True).limit(20)).all()
    for job in jobs:
        node = db.get(WorkerNode, job.worker_id) if job.worker_id else None
        stale_node = not node or not node.last_seen or node.last_seen < now - timedelta(seconds=60)
        replaced = bool(node and job.claim_token and node.instance_token != job.claim_token)
        expired = bool(job.claimed_at and job.claimed_at < now - timedelta(hours=1))
        if stale_node or replaced or expired:
            if job.kind == "match":
                mid = json.loads(job.payload).get("match_id")
                attempt = db.scalar(select(GameAttempt).where(GameAttempt.match_id == mid, GameAttempt.attempt == job.attempts))
                if attempt and attempt.status == "running":
                    attempt.status, attempt.finished_at = "abandoned", now
                    attempt.detail = "Worker heartbeat lost or replaced; original locked configuration retained"
            job.status = "queued"
            job.worker_id = None
            job.claim_token = None
            job.claimed_at = None

def claim(db, instance_token):
    # Serialize with organizer reports so a held game cannot start from the queue.
    tournament.serialize(db)
    held_jobs = []
    while True:
        job = db.scalar(select(Job).where(Job.status == "queued", Job.id.not_in(held_jobs))
                        .order_by(Job.priority, Job.id).with_for_update(skip_locked=True).limit(1))
        if not job or job.kind != "match":
            break
        match = db.get(Match, json.loads(job.payload)["match_id"])
        encounter = db.get(tournament.Encounter, match.encounter_id) if match and match.encounter_id else None
        if match and match.kind == "daily":
            players = [match.player_a, match.player_b]
            running = db.scalar(select(Match.id).where(Match.id != match.id, Match.status == "running",
                Match.kind != "practice", (Match.player_a.in_(players)) | (Match.player_b.in_(players))).limit(1))
            if running or daily.tournament_busy(db, players):
                held_jobs.append(job.id)
                continue
        if not encounter:
            break
        r = db.get(TournamentRound, encounter.round_id)
        t = db.get(tournament.Tournament, r.tournament_id)
        if not tournament.held(db, t, r, encounter):
            break
        held_jobs.append(job.id)
    if job:
        job.status = "running"
        job.worker_id = WORKER_ID
        job.claim_token = instance_token
        job.claimed_at = datetime.now(timezone.utc)
        job.attempts += 1
        if job.kind == "match":
            match = db.get(Match, json.loads(job.payload)["match_id"])
            if match:
                match.worker_id = WORKER_ID
                if match.status in ("queued", "scheduled"):
                    match.status = "running"
                if match.encounter_id:
                    # An organizer retry creates a new job for the same game;
                    # attempt numbers belong to the game across all jobs.
                    previous = db.scalar(select(func.max(GameAttempt.attempt)).where(GameAttempt.match_id == match.id)) or 0
                    job.attempts = max(job.attempts, previous + 1)
                    db.add(GameAttempt(match_id=match.id, attempt=job.attempts, worker_id=WORKER_ID))
        db.flush()
        return job

def release_claim(db, job_id, instance_token):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job and job.status == "running" and job.claim_token == instance_token:
        if job.kind == "match":
            attempt = db.scalar(select(GameAttempt).where(GameAttempt.match_id == json.loads(job.payload)["match_id"],
                                                          GameAttempt.attempt == job.attempts))
            if attempt and attempt.status == "running":
                attempt.status, attempt.finished_at = "abandoned", datetime.now(timezone.utc)
                attempt.detail = "Claim released after database connection failure"
        job.status = "queued"
        job.worker_id = None
        job.claim_token = None
        job.claimed_at = None

class LostClaim(Exception):
    pass

def process(job_id, instance_token):
    environment_token = None
    try:
        with SessionLocal.begin() as db:
            job = db.get(Job, job_id)
            if not job or job.status != "running" or job.claim_token != instance_token:
                return
            try:
                payload = json.loads(job.payload)
                round_id = payload.get("round_id")
                if job.kind == "match":
                    game = db.get(Match, payload["match_id"])
                    if game and game.encounter_id:
                        round_id = db.get(tournament.Encounter, game.encounter_id).round_id
                    elif game and game.kind == "practice":
                        environment_token = JUDGE_ENV.set(tournament.environment())
                    elif game and game.kind == "daily":
                        entry = db.scalar(select(daily.DailyEntry).where(daily.DailyEntry.submission_id == game.submission_a))
                        if entry is None:
                            raise RuntimeError("Missing Daily environment snapshot")
                        environment_token = JUDGE_ENV.set(json.loads(entry.environment))
                if round_id:
                    if environment_token is not None:
                        JUDGE_ENV.reset(environment_token)
                        environment_token = None
                    environment_token = JUDGE_ENV.set(json.loads(db.get(TournamentRound, round_id).environment))
                elif job.kind == "compile" and payload.get("practice_validation"):
                    environment_token = JUDGE_ENV.set(tournament.environment())
                elif job.kind == "compile" and payload.get("daily_validation"):
                    entry = db.scalar(select(daily.DailyEntry).where(daily.DailyEntry.submission_id == payload["submission_id"]))
                    environment_token = JUDGE_ENV.set(json.loads(entry.environment))
                if job.kind == "compile":
                    s = db.get(Submission, payload["submission_id"])
                    if s.status not in ("ready", "failed"):
                        compile_submission(db, s, validate=bool(round_id) or bool(payload.get("practice_validation")) or bool(payload.get("daily_validation")))
                elif job.kind == "match":
                    m = db.get(Match, payload["match_id"])
                    if m.status == "done" or m.status == "void":
                        pass
                    else:
                        m.status = "running"; play_match(db, m)
                job.status = "done"
            except Exception as exc:
                job.status = "failed"
                if job.kind == "match":
                    m = db.get(Match, json.loads(job.payload).get("match_id"));
                    if m: m.status = "failed"; m.detail = json.dumps({"error": str(exc)[:1000]})
                elif job.kind == "compile":
                    s = db.get(Submission, json.loads(job.payload).get("submission_id"));
                    if s: s.status = "infrastructure_error"; s.compile_error = str(exc)[:6000]
            if job.kind == "match":
                tournament.serialize(db)
            # A recovered job may already belong to another machine. Lock and
            # re-read its ownership before committing any result or score.
            current = db.scalar(select(Job).where(Job.id == job_id)
                                .with_for_update().execution_options(populate_existing=True))
            if not current or current.claim_token != instance_token or current.worker_id != WORKER_ID:
                raise LostClaim()
            current.claim_token = None
            current.claimed_at = None
            if job.kind == "match":
                m = db.get(Match, json.loads(job.payload)["match_id"])
                if m and m.encounter_id:
                    attempt = db.scalar(select(GameAttempt).where(GameAttempt.match_id == m.id, GameAttempt.attempt == job.attempts))
                    if attempt:
                        attempt.status, attempt.detail, attempt.finished_at = m.status, m.detail, datetime.now(timezone.utc)
                    tournament.after_game(db, m)
                elif m and m.kind == "daily":
                    daily.after_game(db, m)
                tournament.pump(db)
    except LostClaim:
        return
    finally:
        if environment_token is not None:
            JUDGE_ENV.reset(environment_token)

def register_worker(instance_token):
    now = datetime.now(timezone.utc)
    with SessionLocal.begin() as db:
        node = db.get(WorkerNode, WORKER_ID, with_for_update=True)
        if node and node.instance_token != instance_token and (now - node.last_seen).total_seconds() < 30:
            raise RuntimeError(f"WORKER_ID '{WORKER_ID}' is already active; choose a unique ID for this computer")
        if node:
            node.instance_token, node.last_seen = instance_token, now
        else:
            db.add(WorkerNode(id=WORKER_ID, instance_token=instance_token, last_seen=now))

def heartbeat_loop(instance_token, stop_event):
    while not stop_event.wait(5):
        try:
            with SessionLocal.begin() as db:
                node = db.get(WorkerNode, WORKER_ID, with_for_update=True)
                if not node or node.instance_token != instance_token:
                    stop_event.set()
                    return
                node.last_seen = datetime.now(timezone.utc)
        except OperationalError:
            engine.dispose()

def automation_loop(instance_token,stop_event):
    # Separate from judging and heartbeat so a long game cannot delay deadlines.
    while not stop_event.is_set():
        try:
            with SessionLocal.begin() as db:
                node=db.get(WorkerNode,WORKER_ID)
                if not node or node.instance_token!=instance_token:
                    stop_event.set()
                    return
                tournament.auto_tick(db)
                daily.pair(db)
        except OperationalError:
            engine.dispose()
        except Exception:
            logging.getLogger(__name__).exception("Automatic tournament progression failed")
        stop_event.wait(5)


def main():
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", WORKER_ID):
        raise ValueError("WORKER_ID must contain only lowercase letters, digits, '-' or '_'")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    ensure_schema()
    instance_token = secrets.token_hex(16)
    # On a normal container restart the previous heartbeat can remain fresh
    # briefly. Wait for it to expire while still rejecting a truly duplicate ID.
    for attempt in range(8):
        try:
            register_worker(instance_token)
            break
        except RuntimeError:
            if attempt == 7:
                raise
            time.sleep(5)
    stop_event = threading.Event()
    threading.Thread(target=heartbeat_loop, args=(instance_token, stop_event), daemon=True).start()
    threading.Thread(target=automation_loop,args=(instance_token,stop_event),daemon=True).start()
    last_recovery = 0.0
    pending_release = None
    while not stop_event.is_set():
        job_id = None
        try:
            if pending_release is not None:
                with SessionLocal.begin() as db:
                    release_claim(db, pending_release, instance_token)
                pending_release = None
            if time.monotonic() - last_recovery >= 10:
                with SessionLocal.begin() as db:
                    recover_abandoned_jobs(db)
                with SessionLocal.begin() as db:
                    tournament.serialize(db)
                    tournament.pump(db)
                last_recovery = time.monotonic()
            with SessionLocal.begin() as db:
                job = claim(db, instance_token); job_id = job.id if job else None
            if job_id: process(job_id, instance_token)
            else: time.sleep(1)
        except OperationalError:
            # An external PostgreSQL connection can drop during a query.
            # Discard pooled sockets and return our unfinished job once connected.
            if job_id:
                pending_release = job_id
            engine.dispose()
            time.sleep(3)

def create_daily_pairings(day, cutoff=None):
    with SessionLocal.begin() as db:
        return daily.pair(db, day.isoformat() if isinstance(day, date) else day)

if __name__ == "__main__": main()
