import base64, json, os, selectors, subprocess, tempfile, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import Base, SessionLocal, engine
from .models import Job, Match, Submission

DATA = Path(os.getenv("APP_DATA", "/app/data")); ARTIFACTS = DATA / "artifacts"
IMAGE = os.getenv("SANDBOX_IMAGE", "oantuti-sandbox:dev")
# OTTv2 tactical board game: coordinates use a1=(0,0), i9=(8,8).
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
FILES = "abcdefghi"
INITIAL = [
    ("N01", "north", "paper", "e8"), ("N02", "north", "rock", "f8"),
    ("N03", "north", "scissors", "e7"), ("N04", "north", "paper", "f7"),
    ("N05", "north", "rock", "g7"), ("N06", "north", "scissors", "f6"),
    ("N07", "north", "paper", "g6"), ("N08", "north", "rock", "h6"),
    ("N09", "north", "scissors", "g5"), ("N10", "north", "paper", "h5"),
    ("S01", "south", "paper", "b5"), ("S02", "south", "scissors", "c5"),
    ("S03", "south", "rock", "b4"), ("S04", "south", "paper", "c4"),
    ("S05", "south", "scissors", "d4"), ("S06", "south", "rock", "c3"),
    ("S07", "south", "paper", "d3"), ("S08", "south", "scissors", "e3"),
    ("S09", "south", "rock", "d2"), ("S10", "south", "paper", "e2"),
]

def coord(square):
    return FILES.index(square[0]), int(square[1:]) - 1

def square(x, y):
    return f"{FILES[x]}{y + 1}"

def new_game():
    return {"pieces": [{"id": i, "side": side, "type": typ, "x": coord(pos)[0], "y": coord(pos)[1]}
                       for i, side, typ, pos in INITIAL], "turn": "south", "move_number": 1,
            "winner": None, "win_reason": None}

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
    out, _ = proc.communicate(s.source.encode(), timeout=35)
    result = json.loads(out.decode("utf-8", "replace").splitlines()[-1])
    if not result.get("ok"):
        s.status = "failed"; s.compile_error = result.get("error", "Biên dịch thất bại")[:6000]
    else:
        data = base64.b64decode(result["artifact"], validate=True)
        target = ARTIFACTS / f"submission-{s.id}.bin"; target.write_bytes(data)
        s.artifact = target.name; s.status = "ready"; s.compile_error = None

def start_bot(s):
    artifact = ARTIFACTS / s.artifact
    create = sandbox_args()
    create[1] = "create"  # The selected file is copied into a private container before it starts.
    create.remove("--rm")
    create.remove("-i")
    create += ["-i", "-e", "PYTHONUNBUFFERED=1", IMAGE, "run", s.language]
    container_id = subprocess.check_output(create, text=True, timeout=15).strip()
    try:
        subprocess.run(["docker", "cp", str(artifact), f"{container_id}:/tmp/bot/program"], check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        proc = subprocess.Popen(["docker", "start", "-ai", container_id], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        return proc, container_id
    except Exception:
        subprocess.run(["docker", "rm", "-f", container_id], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise

def read_line_timeout(stream, timeout):
    sel = selectors.DefaultSelector(); sel.register(stream, selectors.EVENT_READ)
    try:
        if not sel.select(timeout): return None
        return stream.readline(256)
    finally: sel.close()

def play_match(db: Session, m: Match):
    a, b = db.get(Submission, m.submission_a), db.get(Submission, m.submission_b)
    if not a or not b or a.status != "ready" or b.status != "ready":
        m.status = "failed"; m.detail = json.dumps({"error": "Bot chưa sẵn sàng"}); return
    (pa, ca), (pb, cb) = start_bot(a), start_bot(b)
    game = new_game(); turns = []; side_a, side_b = "south", "north"
    try:
        max_moves = int(os.getenv("MATCH_MAX_MOVES", "200"))
        timeout = float(os.getenv("MATCH_MOVE_TIMEOUT_SECONDS", "1.5"))
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
            player_proc.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode()); player_proc.stdin.flush()
            raw = read_line_timeout(player_proc.stdout, timeout)
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
            subprocess.run(["docker", "rm", "-f", cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def claim(db):
    job = db.scalar(select(Job).where(Job.status == "queued").order_by(Job.id).with_for_update(skip_locked=True).limit(1))
    if job: job.status = "running"; job.attempts += 1; db.flush(); return job

def process(job_id):
    with SessionLocal.begin() as db:
        job = db.get(Job, job_id)
        try:
            payload = json.loads(job.payload)
            if job.kind == "compile":
                s = db.get(Submission, payload["submission_id"]); compile_submission(db, s)
            elif job.kind == "match":
                m = db.get(Match, payload["match_id"]); m.status = "running"; play_match(db, m)
            job.status = "done"
        except Exception as exc:
            job.status = "failed"
            if job.kind == "match":
                m = db.get(Match, json.loads(job.payload).get("match_id"));
                if m: m.status = "failed"; m.detail = json.dumps({"error": str(exc)[:1000]})
            elif job.kind == "compile":
                s = db.get(Submission, json.loads(job.payload).get("submission_id"));
                if s: s.status = "failed"; s.compile_error = str(exc)[:6000]

def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    tz = ZoneInfo(os.getenv("EVENT_TIMEZONE", "Asia/Ho_Chi_Minh"))
    last_pair_day = None
    while True:
        now = datetime.now(tz)
        start = datetime.strptime(os.getenv("AUTO_MATCH_START", "19:00"), "%H:%M").time()
        if os.getenv("MATCHES_ENABLED", "false").lower() == "true" and now.time() >= start and last_pair_day != now.date().isoformat():
            try:
                create_daily_pairings(now.date().isoformat())
                last_pair_day = now.date().isoformat()
            except Exception:
                # Retry next loop; the database check makes pairing idempotent.
                time.sleep(2)
        with SessionLocal.begin() as db:
            job = claim(db); job_id = job.id if job else None
        if job_id: process(job_id)
        else: time.sleep(1)

def create_daily_pairings(day):
    import random
    with SessionLocal.begin() as db:
        if db.scalar(select(Match.id).where(Match.event_day == day, Match.kind == "daily")):
            return
        players = db.execute(select(Submission.player_id, Submission.id).where(Submission.status == "ready").order_by(Submission.id.desc())).all()
        newest = {}
        for pid, sid in players:
            newest.setdefault(pid, sid)
        eligible = list(newest.items()); random.SystemRandom().shuffle(eligible)
        for (pa, sa), (pb, sb) in zip(eligible[::2], eligible[1::2]):
            m = Match(player_a=pa, player_b=pb, submission_a=sa, submission_b=sb, event_day=day, kind="daily")
            db.add(m); db.flush(); db.add(Job(kind="match", payload=json.dumps({"match_id": m.id})))

if __name__ == "__main__": main()
