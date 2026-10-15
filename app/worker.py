import base64, json, os, re, secrets, selectors, struct, subprocess, tempfile, threading, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from .db import SessionLocal, engine
from .models import Job, Match, Submission, WorkerNode
from .game_setup import coord, new_game, square
from .event_schedule import get_event_state, submission_day_bounds
from .schema import ensure_schema

DATA = Path(os.getenv("APP_DATA", "/app/data")); ARTIFACTS = DATA / "artifacts"
IMAGE = os.getenv("SANDBOX_IMAGE", "oantuti-sandbox:dev")
WORKER_ID = os.getenv("WORKER_ID", "may-1")
# OTTv2 tactical board game: coordinates use a1=(0,0), i9=(8,8).
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}

def legal_moves(game, side):
    occupied = {(p["x"], p["y"]): p for p in game["pieces"]}
    result = []
    for p in game["pieces"]:
        if p["side"] != side:
            continue
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == dy == 0:
                    continue
                x, y = p["x"] + dx, p["y"] + dy
                if not (0 <= x < 9 and 0 <= y < 9):
                    continue
                other = occupied.get((x, y))
                if other and (other["side"] == side or BEATS[p["type"]] != other["type"]):
                    continue
                result.append({"piece_id": p["id"], "from": square(p["x"], p["y"]), "to": square(x, y),
                               "capture": other["id"] if other else None})
    return result

def apply_move(game, move):
    nxt = json.loads(json.dumps(game))
    piece = next(p for p in nxt["pieces"] if p["id"] == move["piece_id"])
    x, y = coord(move["to"])
    captured = next((p for p in nxt["pieces"] if p["x"] == x and p["y"] == y), None)
    if captured:
        nxt["pieces"].remove(captured)
    piece["x"], piece["y"] = x, y
    goal = (0, 0) if piece["side"] == "north" else (8, 8)
    if (x, y) == goal:
        nxt["winner"], nxt["win_reason"] = piece["side"], "goal"
    elif captured and not any(p["side"] == captured["side"] and p["type"] == captured["type"] for p in nxt["pieces"]):
        nxt["winner"], nxt["win_reason"] = piece["side"], "elimination"
    else:
        nxt["turn"] = "north" if piece["side"] == "south" else "south"
    nxt["move_number"] += 1
    return nxt

def position_key(game):
    pieces = sorted((p["id"], p["side"], p["type"], p["x"], p["y"]) for p in game["pieces"])
    return json.dumps([game["turn"], pieces], separators=(",", ":"))

def sandbox_args():
    cmd = ["docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m,mode=1777", "--user", "10001:10001", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "256m", "--cpus", "1", "--pids-limit", "32"]
    return cmd

def docker_cmd(mode, lang):
    cmd = sandbox_args() + [IMAGE, mode, lang]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)

def compile_submission(db: Session, s: Submission):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    proc = docker_cmd("compile", s.language)
    try:
        out, _ = proc.communicate(s.source.encode(), timeout=35)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        s.status = "failed"
        s.compile_error = "Biên dịch vượt thời gian 35 giây"
        return
    result = json.loads(out.decode("utf-8", "replace").splitlines()[-1])
    if not result.get("ok"):
        s.status = "failed"; s.compile_error = result.get("error", "Biên dịch thất bại")[:6000]
    else:
        data = base64.b64decode(result["artifact"], validate=True)
        target = ARTIFACTS / f"submission-{s.id}.bin"; target.write_bytes(data)
        s.artifact = target.name; s.status = "ready"; s.compile_error = None

def ensure_local_artifact(db: Session, s: Submission):
    # Artifacts live in each computer's Docker volume. A match assigned to a
    # second computer can rebuild its own copy from the source stored in DB.
    if s.status != "ready":
        return False
    if not s.artifact or not (ARTIFACTS / s.artifact).is_file():
        compile_submission(db, s)
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
        proc.kill()
        proc.wait(timeout=2)
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
            if remaining <= 0 or not sel.select(remaining):
                return None
            chunk = os.read(stream.fileno(), 256)
            if not chunk:
                return None
            pending.extend(chunk)

def play_match(db: Session, m: Match):
    a, b = db.get(Submission, m.submission_a), db.get(Submission, m.submission_b)
    if not a or not b or not ensure_local_artifact(db, a) or not ensure_local_artifact(db, b):
        m.status = "failed"; m.detail = json.dumps({"error": "Bot chưa sẵn sàng"}); return
    pa, ca = start_bot(a)
    try:
        pb, cb = start_bot(b)
    except Exception:
        pa.kill(); pa.wait(timeout=2)
        raise
    game = new_game(); turns = []; side_a, side_b = "south", "north"
    output_buffers = {pa.stdout: bytearray(), pb.stdout: bytearray()}
    try:
        max_moves = int(os.getenv("MATCH_MAX_MOVES", "200"))
        timeout = float(os.getenv("MATCH_MOVE_TIMEOUT_SECONDS", "1.5"))
        first_move_timeout = float(os.getenv("MATCH_FIRST_MOVE_TIMEOUT_SECONDS", "15"))
        repetitions = {position_key(game): 1}
        while not game["winner"] and game["move_number"] <= max_moves:
            side = game["turn"]
            player_proc = pa if side == side_a else pb
            choices = legal_moves(game, side)
            if not choices:
                game["win_reason"] = "no_legal_moves_draw"
                break
            payload = {"protocol_version": 2, "move_number": game["move_number"], "side": side,
                       "board_size": 9, "pieces": game["pieces"], "legal_moves": choices}
            try:
                player_proc.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
                player_proc.stdin.flush()
                # Each bot's first turn also includes Docker/Python startup time.
                raw = read_line_timeout(player_proc.stdout, first_move_timeout if game["move_number"] <= 2 else timeout,
                                        output_buffers[player_proc.stdout])
            except (BrokenPipeError, OSError):
                raw = None
            try:
                reply = json.loads(raw.decode("utf-8")) if raw else None
                selected = next((mv for mv in choices if mv["piece_id"] == reply.get("piece_id") and mv["to"] == reply.get("to")), None) if isinstance(reply, dict) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                selected = None
            if selected is None:
                game["winner"], game["win_reason"] = ("north" if side == "south" else "south"), "invalid_move_or_timeout"
                turns.append({"move_number": game["move_number"], "side": side, "error": "invalid_move_or_timeout"})
                break
            turns.append({"move_number": game["move_number"], "side": side, **selected})
            game = apply_move(game, selected)
            if not game["winner"]:
                key = position_key(game)
                repetitions[key] = repetitions.get(key, 0) + 1
                if repetitions[key] >= 3:
                    game["win_reason"] = "threefold_repetition_draw"
                    break
        if not game["winner"] and not game["win_reason"]:
            game["win_reason"] = "move_limit_draw"
        winner = game["winner"]
        if winner == side_a: m.points_a = 1; m.winner_id = m.player_a; m.points_b = 0
        elif winner == side_b: m.points_b = 1; m.winner_id = m.player_b; m.points_a = 0
        else: m.points_a = m.points_b = 0.5; m.winner_id = None
        m.detail = json.dumps({"protocol_version": 2, "winner_side": winner, "win_reason": game["win_reason"],
                               "turns": turns, "final_pieces": game["pieces"]})
        m.status = "done"
    finally:
        for p, cid in ((pa, ca), (pb, cb)):
            try: p.kill(); p.wait(timeout=2)
            except Exception: pass
            for pipe in (p.stdin, p.stdout):
                if pipe:
                    pipe.close()
            if cid:
                subprocess.run(["docker", "rm", "-f", cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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
            job.status = "queued"
            job.worker_id = None
            job.claim_token = None
            job.claimed_at = None

def claim(db, instance_token):
    job = db.scalar(select(Job).where(Job.status == "queued")
                    .order_by(Job.id).with_for_update(skip_locked=True).limit(1))
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
        db.flush()
        return job

def release_claim(db, job_id, instance_token):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job and job.status == "running" and job.claim_token == instance_token:
        job.status = "queued"
        job.worker_id = None
        job.claim_token = None
        job.claimed_at = None

class LostClaim(Exception):
    pass

def process(job_id, instance_token):
    try:
        with SessionLocal.begin() as db:
            job = db.get(Job, job_id)
            if not job or job.status != "running" or job.claim_token != instance_token:
                return
            try:
                payload = json.loads(job.payload)
                if job.kind == "compile":
                    s = db.get(Submission, payload["submission_id"]); compile_submission(db, s)
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
                    if s: s.status = "failed"; s.compile_error = str(exc)[:6000]
            # A recovered job may already belong to another machine. Lock and
            # re-read its ownership before committing any result or score.
            current = db.scalar(select(Job).where(Job.id == job_id)
                                .with_for_update().execution_options(populate_existing=True))
            if not current or current.claim_token != instance_token or current.worker_id != WORKER_ID:
                raise LostClaim()
            current.claim_token = None
            current.claimed_at = None
    except LostClaim:
        return

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
    tz = ZoneInfo(os.getenv("EVENT_TIMEZONE", "Asia/Ho_Chi_Minh"))
    next_pair_check = 0.0
    completed_final_deadline = None
    last_recovery = 0.0
    pending_release = None
    while not stop_event.is_set():
        job_id = None
        try:
            if pending_release is not None:
                with SessionLocal.begin() as db:
                    release_claim(db, pending_release, instance_token)
                pending_release = None
            now = datetime.now(tz)
            with SessionLocal() as db:
                state = get_event_state(db, os.getenv("MATCHES_ENABLED", "false").lower() == "true", now)
            start_time = datetime.fromisoformat(state["start_at"]).time().replace(tzinfo=None) if state["start_at"] else None
            end_at = datetime.fromisoformat(state["end_at"]) if state["end_at"] else None
            normal_pairing = bool(state["matches_enabled"] and start_time and now.time() >= start_time)
            # On the final day the window can close at exactly the pairing time.
            # Finish pairing submissions made before the deadline even if the
            # worker polls (or reconnects) after that instant.
            final_pairing = bool(state["enabled"] and state["worker_enabled"] and end_at
                                 and start_time and end_at.time().replace(tzinfo=None) >= start_time
                                 and now >= end_at and end_at != completed_final_deadline)
            if (os.getenv("AUTO_PAIR_ENABLED", "true").lower() == "true"
                    and (normal_pairing or final_pairing) and time.monotonic() >= next_pair_check):
                next_pair_check = time.monotonic() + 3
                try:
                    pair_day = end_at.date().isoformat() if final_pairing else now.date().isoformat()
                    cutoff = end_at.astimezone(timezone.utc) if final_pairing else None
                    finished = create_daily_pairings(pair_day, cutoff=cutoff)
                    if final_pairing and finished:
                        completed_final_deadline = end_at
                except Exception:
                    # Retry later; the database lock keeps concurrent workers idempotent.
                    time.sleep(2)
            if time.monotonic() - last_recovery >= 10:
                with SessionLocal.begin() as db:
                    recover_abandoned_jobs(db)
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
    import random
    day_start, day_end = submission_day_bounds(date.fromisoformat(day))
    if cutoff is not None:
        day_end = min(day_end, cutoff)
    with SessionLocal.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(74620146)"))
        # Let every source submitted before pairing finish compilation first.
        if db.scalar(select(Submission.id).where(
            Submission.created_at >= day_start,
            Submission.created_at < day_end,
            Submission.status == "queued",
        )):
            return False
        players = db.execute(select(Submission.player_id, Submission.id).where(
            Submission.status == "ready",
            Submission.created_at >= day_start,
            Submission.created_at < day_end,
        ).order_by(Submission.id.desc())).all()
        newest = {}
        for pid, sid in players:
            newest.setdefault(pid, sid)
        already_matched = set()
        for player_a, player_b in db.execute(select(Match.player_a, Match.player_b).where(
            Match.event_day == day, Match.status != "void",
        )):
            already_matched.update((player_a, player_b))
        # Existing pairs are excluded, but unmatched teams can join later today.
        eligible = [(pid, sid) for pid, sid in newest.items() if pid not in already_matched]
        random.SystemRandom().shuffle(eligible)
        if len(eligible) < 2:
            return True
        for (pa, sa), (pb, sb) in zip(eligible[::2], eligible[1::2]):
            m = Match(player_a=pa, player_b=pb, submission_a=sa, submission_b=sb, event_day=day, kind="daily")
            db.add(m); db.flush(); db.add(Job(kind="match", payload=json.dumps({"match_id": m.id})))
        return True

if __name__ == "__main__": main()
