import base64, hashlib, hmac, json, os, re, secrets, time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from pathlib import Path
from .db import SessionLocal
from .game_setup import new_game
from .models import (EventControl, Job, Match, Player, Submission, WorkerNode,
                     PracticeRequest, PracticeSubmissionRequest, RoundSubmission)
from .event_schedule import as_vietnam_time, get_event_state, submission_day_bounds
from .security import authorized_player, check_registration, digest, hash_password, new_token, verify_password
from .schema import ensure_schema
from . import tournament
from .tournament_api import router as tournament_router
from . import daily

app = FastAPI(title="OanTuTi Bot League", version="0.2.0", description="OTTv2: game chiến thuật bàn cờ 9×9",
              docs_url=None, redoc_url=None, openapi_url=None)
VN = ZoneInfo(os.getenv("EVENT_TIMEZONE", "Asia/Ho_Chi_Minh"))
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")
app.include_router(tournament_router)
app.include_router(daily.router)

@app.on_event("startup")
def startup():
    ensure_schema()

class Register(BaseModel): name: str = Field(min_length=2, max_length=60); password: str = Field(min_length=8, max_length=128)
class Login(BaseModel): name: str = Field(min_length=2, max_length=60); password: str = Field(min_length=1, max_length=128)
class ClaimAccount(BaseModel): name: str = Field(min_length=2, max_length=60); registration_token: str; password: str = Field(min_length=8, max_length=128)
class Submit(BaseModel): language: str; source: str = Field(min_length=1)
class Challenge(BaseModel): opponent_id: int
class PracticeInput(BaseModel): opponent_id: str = Field(min_length=1, max_length=40); submission_id: int
class EventScheduleInput(BaseModel): start_at: datetime; end_at: datetime; enabled: bool
class AdminLogin(BaseModel): username: str = Field(min_length=1, max_length=80); password: str = Field(min_length=1, max_length=256)
class VoidMatchInput(BaseModel): reason: str = Field(default="", max_length=500)

def mark_match_void(match: Match, reason: str):
    detail = json.loads(match.detail) if match.detail else {}
    detail["void_reason"] = reason.strip()
    detail["voided_at"] = datetime.now(timezone.utc).isoformat()
    match.detail = json.dumps(detail, ensure_ascii=False)
    match.status = "void"

def match_for_teams_on_day(db, team_ids, day, exclude_id=None):
    query = select(Match.id).where(
        Match.event_day == day,
        Match.kind.in_(["daily", "rematch"]),
        Match.status != "void",
        (Match.player_a.in_(team_ids)) | (Match.player_b.in_(team_ids)),
    )
    if exclude_id is not None:
        query = query.where(Match.id != exclude_id)
    return db.scalar(query)

def matches_engine_enabled():
    return os.getenv("MATCHES_ENABLED", "false").lower() == "true"

PRACTICE_LIMIT = 10
PRACTICE_OPPONENTS = {
    "first": ("Bot mẫu: First", "bot_first.py"),
    "greedy": ("Bot mẫu: Greedy", "bot_greedy.py"),
    "random": ("Bot mẫu: Random", "bot_random.py"),
}

def practice_opponent_rows(db):
    """Return stable hidden Player/Submission records backing built-in bots."""
    db.execute(text("SELECT pg_advisory_xact_lock(74620148)"))
    examples = Path(__file__).resolve().parents[1] / "examples"
    rows = {}
    for key, (name, filename) in PRACTICE_OPPONENTS.items():
        player_name = f"🤖 Practice Bot · {name}"
        player = db.scalar(select(Player).where(Player.name == player_name))
        if player is None:
            player = Player(name=player_name, token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest())
            db.add(player); db.flush()
        source = (examples / filename).read_text(encoding="utf-8")
        submission = db.scalar(select(Submission).where(Submission.player_id == player.id).order_by(Submission.id).limit(1))
        if submission is None:
            submission = Submission(player_id=player.id, language="python", source=source, status="ready")
            db.add(submission); db.flush()
        elif submission.source != source or submission.language != "python":
            submission.source, submission.language, submission.status = source, "python", "ready"
            submission.artifact, submission.compile_error = None, None
        rows[key] = (player, submission)
    return rows

def practice_state(db, player_id):
    today = datetime.now(VN).date().isoformat()
    used = db.scalar(select(func.count(Match.id)).where(
        Match.kind == "practice", Match.player_a == player_id, Match.event_day == today)) or 0
    active = db.scalar(select(Match).where(
        Match.kind == "practice", Match.player_a == player_id,
        Match.status.in_(["queued", "running"])).order_by(Match.id.desc()).limit(1))
    compatible_ids = compatible_practice_submissions(db, player_id)
    submissions = db.scalars(select(Submission).where(
        Submission.id.in_(compatible_ids), Submission.status == "ready").order_by(Submission.id.desc())).all() if compatible_ids else []
    return {
        "day": today, "limit": PRACTICE_LIMIT, "used": used, "remaining": max(0, PRACTICE_LIMIT - used),
        "active_match": ({"id": active.id, "status": active.status} if active else None),
        "submissions": [{"id": s.id, "language": s.language, "created_at": s.created_at.isoformat() if s.created_at else None}
                        for s in submissions],
    }

def compatible_practice_submissions(db, player_id):
    rules = (Path(__file__).resolve().parents[1] / "GAMERULE.md").read_text(encoding="utf-8")
    current_digest = hashlib.sha256(rules.encode()).hexdigest()
    current_environment = tournament.environment()
    links = db.execute(select(RoundSubmission.submission_id, tournament.TournamentRound.rules_digest,
                              tournament.TournamentRound.environment)
                       .join(tournament.TournamentRound, RoundSubmission.round_id == tournament.TournamentRound.id)
                       .where(RoundSubmission.player_id == player_id)).all()
    compatible = {submission_id for submission_id, digest, environment in links
                  if digest == current_digest and json.loads(environment) == current_environment}
    practice_submissions = db.scalars(select(PracticeSubmissionRequest).where(
        PracticeSubmissionRequest.player_id == player_id,
        PracticeSubmissionRequest.rules_digest == current_digest)).all()
    compatible.update(request.submission_id for request in practice_submissions
                      if json.loads(request.environment) == current_environment)
    return compatible

def make_admin_token(username: str) -> str:
    expires = int(time.time()) + 8 * 60 * 60
    payload = base64.urlsafe_b64encode(json.dumps({"sub": username, "exp": expires}, separators=(",", ":")).encode()).decode().rstrip("=")
    secret = os.getenv("ADMIN_API_KEY", "").encode()
    signature = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"

def require_admin(key=None, authorization=None):
    expected_key = os.getenv("ADMIN_API_KEY", "")
    if expected_key and secrets.compare_digest(key or "", expected_key):
        return
    token = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
    try:
        payload, signature = token.rsplit(".", 1)
        expected_signature = hmac.new(expected_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        valid = (expected_key and secrets.compare_digest(signature, expected_signature)
                 and claims.get("sub") == os.getenv("ADMIN_USERNAME", "admin")
                 and int(claims.get("exp", 0)) > int(time.time()))
    except (ValueError, TypeError, json.JSONDecodeError):
        valid = False
    if not valid:
        raise HTTPException(401, "Phiên đăng nhập ban tổ chức không hợp lệ hoặc đã hết hạn")

def latest_ready(db, player_id, day):
    day_start, day_end = submission_day_bounds(day)
    return db.scalar(select(Submission).where(
        Submission.player_id == player_id,
        Submission.status == "ready",
        Submission.created_at >= day_start,
        Submission.created_at < day_end,
    ).order_by(Submission.id.desc()))

def worker_online(node):
    return bool(node and node.last_seen and node.last_seen >= datetime.now(timezone.utc) - timedelta(seconds=30))

@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")

@app.get("/admin")
def admin_page():
    return FileResponse(WEB_DIR / "admin.html")

@app.get("/local")
def local_lab_guide():
    # Instructions only: submitted code is executed by the user's loopback app.
    return FileResponse(WEB_DIR / "local-guide.html")

@app.get("/api/meta")
def meta():
    with SessionLocal() as db:
        active = db.scalar(select(tournament.Tournament).where(tournament.Tournament.status.not_in(["complete", "cancelled"])))
        return {"name": "OanTuTi Bot League", "protocol_version": 2,
                "modes": {"tournament": True, "daily": daily.enabled(db), "challenge": False, "practice": True},
                "tournament": {"id": active.id, "status": active.status} if active else None,
                **get_event_state(db, matches_engine_enabled())}

@app.get("/api/admin/event")
def admin_get_event(x_admin_key: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_key, authorization)
    with SessionLocal() as db:
        control = db.get(EventControl, 1)
        return {"start_at": control.start_at if control else None,
                "end_at": control.end_at if control else None,
                "enabled": bool(control and control.enabled),
                "event": get_event_state(db, matches_engine_enabled())}

@app.put("/api/admin/event")
def admin_set_event(body: EventScheduleInput, x_admin_key: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_key, authorization)
    start, end = as_vietnam_time(body.start_at), as_vietnam_time(body.end_at)
    if end <= start:
        raise HTTPException(400, "Thời điểm kết thúc phải sau thời điểm bắt đầu")
    with SessionLocal.begin() as db:
        control = db.get(EventControl, 1)
        if control is None:
            control = EventControl(id=1)
            db.add(control)
        control.start_at, control.end_at, control.enabled = start.isoformat(), end.isoformat(), body.enabled
    with SessionLocal() as db:
        control = db.get(EventControl, 1)
        return {"start_at": control.start_at, "end_at": control.end_at,
                "enabled": control.enabled, "event": get_event_state(db, matches_engine_enabled())}

@app.get("/api/admin/workers")
def admin_workers(x_admin_key: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_key, authorization)
    with SessionLocal() as db:
        return [{"id": node.id, "online": worker_online(node), "last_seen": node.last_seen.isoformat()}
                for node in db.scalars(select(WorkerNode).order_by(WorkerNode.id)).all()]

@app.get("/api/admin/matches")
def admin_matches(x_admin_key: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_key, authorization)
    today = datetime.now(VN).date()
    day_start, day_end = submission_day_bounds(today)
    with SessionLocal() as db:
        matches = db.scalars(select(Match).order_by(Match.id.desc()).limit(50)).all()
        names = {p.id: p.name for p in db.scalars(select(Player)).all()}
        output = []
        for m in matches:
            submissions_today = all(
                submission and submission.created_at and day_start <= submission.created_at < day_end
                for submission in (db.get(Submission, m.submission_a), db.get(Submission, m.submission_b))
            )
            can_replay = bool(m.event_day == today.isoformat() and submissions_today
                              and not match_for_teams_on_day(db, (m.player_a, m.player_b), today.isoformat(), exclude_id=m.id))
            output.append({"id": m.id, "team_a": names.get(m.player_a, str(m.player_a)),
                           "team_b": names.get(m.player_b, str(m.player_b)), "worker_id": m.worker_id,
                           "status": m.status, "kind": m.kind, "event_day": m.event_day,
                           "winner": names.get(m.winner_id) if m.winner_id else None,
                           "replay_of": m.replay_of, "can_replay": can_replay})
        return output

@app.post("/api/admin/matches/{match_id}/void-and-replay")
def admin_void_and_replay(match_id: int, body: VoidMatchInput,
                          x_admin_key: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_key, authorization)
    with SessionLocal.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(74620146)"))
        m = db.scalar(select(Match).where(Match.id == match_id).with_for_update())
        if not m:
            raise HTTPException(404, "Không tìm thấy trận")
        if m.kind == "daily":
            raise HTTPException(409, "Daily: retry cùng ID trận lỗi hoặc hủy để trả lượt; không tạo rematch")
        if m.encounter_id:
            raise HTTPException(409, "Dùng điều khiển Tournament để xử lý ván của giải")
        if m.status not in ("done", "failed"):
            raise HTTPException(409, "Chỉ có thể hủy trận đã kết thúc hoặc bị lỗi")
        if db.scalar(select(Match.id).where(Match.replay_of == match_id)):
            raise HTTPException(409, "Trận này đã được tạo trận đấu lại")
        today = datetime.now(VN).date()
        day_start, day_end = submission_day_bounds(today)
        original_submissions = (db.get(Submission, m.submission_a), db.get(Submission, m.submission_b))
        if (m.event_day != today.isoformat() or any(
            not submission or not submission.created_at or
            not day_start <= submission.created_at < day_end
            for submission in original_submissions
        )):
            raise HTTPException(409, "Bot hôm trước đã hết hạn; hãy hủy điểm trận cũ, không thể đấu lại bằng mã cũ")
        participants = (m.player_a, m.player_b)
        db.scalars(select(Player).where(Player.id.in_(participants)).order_by(Player.id).with_for_update()).all()
        if match_for_teams_on_day(db, participants, today.isoformat(), exclude_id=match_id):
            raise HTTPException(409, "Một trong hai đội đã có trận khác hôm nay; không thể đấu lại")
        mark_match_void(m, body.reason)
        replay = Match(player_a=m.player_a, player_b=m.player_b,
                       submission_a=m.submission_a, submission_b=m.submission_b,
                       event_day=today.isoformat(), kind="rematch", replay_of=m.id)
        db.add(replay); db.flush()
        db.add(Job(kind="match", payload=json.dumps({"match_id": replay.id})))
        return {"voided_match_id": m.id, "replay_match_id": replay.id, "status": "queued"}

@app.post("/api/admin/matches/{match_id}/void")
def admin_void_match(match_id: int, body: VoidMatchInput,
                     x_admin_key: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_key, authorization)
    with SessionLocal.begin() as db:
        tournament.serialize(db)
        m = db.scalar(select(Match).where(Match.id == match_id).with_for_update())
        if not m:
            raise HTTPException(404, "Không tìm thấy trận")
        if m.encounter_id:
            raise HTTPException(409, "Dùng điều khiển Tournament để xử lý ván của giải")
        if m.status not in ("done", "failed"):
            raise HTTPException(409, "Chỉ có thể hủy trận đã kết thúc hoặc bị lỗi")
        if db.scalar(select(Match.id).where(Match.replay_of == match_id)):
            raise HTTPException(409, "Trận này đã được tạo trận đấu lại")
        mark_match_void(m, body.reason)
        daily.after_game(db, m)
        return {"voided_match_id": m.id, "status": "void"}

@app.get("/health")
def health(): return {"ok": True}

@app.post("/api/admin/login")
def admin_login(body: AdminLogin):
    expected_username = os.getenv("ADMIN_USERNAME", "admin")
    expected_password = os.getenv("ADMIN_PASSWORD") or os.getenv("ADMIN_API_KEY", "")
    if not expected_password:
        raise HTTPException(503, "Chưa cấu hình tài khoản ban tổ chức")
    if (not secrets.compare_digest(body.username.encode("utf-8"), expected_username.encode("utf-8")) or
            not secrets.compare_digest(body.password.encode("utf-8"), expected_password.encode("utf-8"))):
        raise HTTPException(401, "Tên đăng nhập hoặc mật khẩu không đúng")
    return {"access_token": make_admin_token(expected_username), "token_type": "bearer", "expires_in": 28800}

@app.post("/api/register")
def register(body: Register):
    check_registration()
    if not re.fullmatch(r"[\w .-]{2,60}", body.name.strip(), flags=re.UNICODE): raise HTTPException(400, "Tên đội có ký tự không hợp lệ")
    token = new_token()
    with SessionLocal() as db:
        if db.scalar(select(Player.id).where(func.lower(Player.name) == body.name.strip().lower())): raise HTTPException(409, "Tên đội đã được dùng")
        p = Player(name=body.name.strip(), token_hash=digest(token), password_hash=hash_password(body.password)); db.add(p); db.commit(); db.refresh(p)
        return {"player_id": p.id, "name": p.name, "token": token}

@app.post("/api/login")
def login(body: Login):
    with SessionLocal.begin() as db:
        p = db.scalar(select(Player).where(func.lower(Player.name) == body.name.strip().lower()))
        if not p or not verify_password(body.password, p.password_hash):
            raise HTTPException(401, "Tên đội hoặc mật khẩu không đúng")
        token = new_token()
        p.token_hash = digest(token)
        return {"player_id": p.id, "name": p.name, "token": token}

@app.post("/api/account/claim")
def claim_account(body: ClaimAccount):
    with SessionLocal.begin() as db:
        p = db.scalar(select(Player).where(func.lower(Player.name) == body.name.strip().lower()))
        if not p or p.password_hash or not secrets.compare_digest(p.token_hash, digest(body.registration_token)):
            raise HTTPException(401, "Không thể xác nhận đội cũ bằng token này")
        p.password_hash = hash_password(body.password)
        token = new_token(); p.token_hash = digest(token)
        return {"player_id": p.id, "name": p.name, "token": token}

@app.get("/api/me")
def me(player_id: int = Depends(authorized_player)):
    with SessionLocal() as db:
        p = db.get(Player, player_id)
        return {"player_id": p.id, "name": p.name}

@app.get("/api/practice/opponents")
def practice_opponents():
    return [{"id": key, "name": name, "description": description} for key, (name, description) in {
        "first": ("First", "Chọn nước hợp lệ đầu tiên."),
        "greedy": ("Greedy", "Ưu tiên ăn quân và tiến về đích."),
        "random": ("Random", "Chọn ngẫu nhiên một nước hợp lệ."),
    }.items()]

@app.get("/api/practice/state")
def get_practice_state(player_id: int = Depends(authorized_player)):
    with SessionLocal() as db:
        return practice_state(db, player_id)

@app.post("/api/practice", status_code=202)
def create_practice(body: PracticeInput, player_id: int = Depends(authorized_player),
                    idempotency_key: str | None = Header(default=None)):
    if not idempotency_key or not idempotency_key.strip() or len(idempotency_key) > 128:
        raise HTTPException(400, "Cần Idempotency-Key hợp lệ (tối đa 128 ký tự)")
    idempotency_key = idempotency_key.strip()
    with SessionLocal.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(74620149)"))
        previous = db.scalar(select(PracticeRequest).where(
            PracticeRequest.player_id == player_id,
            PracticeRequest.idempotency_key == idempotency_key))
        if previous:
            if previous.opponent_id != body.opponent_id or previous.submission_id != body.submission_id:
                raise HTTPException(409, "Idempotency-Key đã được dùng cho lựa chọn Practice khác")
            match = db.get(Match, previous.match_id)
            return {"match_id": match.id, "status": match.status}
        if body.opponent_id not in PRACTICE_OPPONENTS:
            raise HTTPException(400, "Bot mẫu không hợp lệ")
        submission = db.get(Submission, body.submission_id)
        if (not submission or submission.player_id != player_id or submission.status != "ready" or
                submission.id not in compatible_practice_submissions(db, player_id)):
            raise HTTPException(409, "Chọn bot ready thuộc đội của bạn, tương thích với luật và môi trường hiện tại")
        today = datetime.now(VN).date().isoformat()
        used = db.scalar(select(func.count(Match.id)).where(
            Match.kind == "practice", Match.player_a == player_id, Match.event_day == today)) or 0
        if used >= PRACTICE_LIMIT:
            raise HTTPException(429, "Bạn đã dùng hết 10 trận Practice hôm nay")
        active_id = db.scalar(select(Match.id).where(
            Match.kind == "practice", Match.player_a == player_id,
            Match.status.in_(["queued", "running"])).limit(1))
        if active_id:
            raise HTTPException(409, "Bạn đang có một trận Practice chờ hoặc đang chạy")
        opponent, bot_submission = practice_opponent_rows(db)[body.opponent_id]
        match = Match(player_a=player_id, player_b=opponent.id,
                      submission_a=submission.id, submission_b=bot_submission.id,
                      event_day=today, kind="practice", status="queued")
        db.add(match); db.flush()
        db.add(Job(kind="match", priority=200, payload=json.dumps({"match_id": match.id})))
        db.add(PracticeRequest(player_id=player_id, idempotency_key=idempotency_key,
                               opponent_id=body.opponent_id, submission_id=submission.id, match_id=match.id))
        return {"match_id": match.id, "status": "queued"}

@app.post("/api/practice/{match_id}/retry", status_code=202)
def retry_practice(match_id: int, player_id: int = Depends(authorized_player)):
    with SessionLocal.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(74620149)"))
        match = db.scalar(select(Match).where(Match.id == match_id).with_for_update())
        if not match or match.kind != "practice" or match.player_a != player_id:
            raise HTTPException(404, "Không tìm thấy trận Practice")
        if match.status != "failed":
            raise HTTPException(409, "Chỉ có thể chạy lại trận lỗi hạ tầng")
        active_id = db.scalar(select(Match.id).where(
            Match.kind == "practice", Match.player_a == player_id,
            Match.status.in_(["queued", "running"]), Match.id != match_id).limit(1))
        if active_id:
            raise HTTPException(409, "Đội đang có một trận Practice chờ hoặc đang chạy")
        match.status, match.detail = "queued", None
        db.add(Job(kind="match", priority=200, payload=json.dumps({"match_id": match.id})))
        return {"match_id": match.id, "status": match.status}

@app.post("/api/practice/submissions", status_code=202)
def submit_practice_bot(body: Submit, player_id: int = Depends(authorized_player),
                        idempotency_key: str | None = Header(default=None)):
    language = body.language.lower()
    if language not in ("python", "cpp"):
        raise HTTPException(400, "language phải là cpp hoặc python")
    try:
        size = len(body.source.encode("utf-8"))
    except UnicodeEncodeError:
        raise HTTPException(400, "Mã nguồn phải là UTF-8")
    if not 0 < size <= 100000 or "\x00" in body.source:
        raise HTTPException(400, "Mã nguồn phải là UTF-8, tối đa 100000 byte")
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(400, "Cần Idempotency-Key (tối đa 128 ký tự)")
    rules = (Path(__file__).resolve().parents[1] / "GAMERULE.md").read_text(encoding="utf-8")
    rules_digest = hashlib.sha256(rules.encode()).hexdigest()
    environment = tournament.environment()
    with SessionLocal.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(74620200, :player_id)"), {"player_id": player_id})
        previous = db.scalar(select(PracticeSubmissionRequest).where(
            PracticeSubmissionRequest.player_id == player_id,
            PracticeSubmissionRequest.idempotency_key == idempotency_key))
        if previous:
            submission = db.get(Submission, previous.submission_id)
            if submission.language != language or submission.source != body.source:
                raise HTTPException(409, "Idempotency-Key đã dùng cho nội dung khác")
            return {"submission_id": submission.id, "status": submission.status}
        submission = Submission(player_id=player_id, language=language, source=body.source)
        db.add(submission)
        db.flush()
        db.add(PracticeSubmissionRequest(player_id=player_id, idempotency_key=idempotency_key,
                                         submission_id=submission.id, rules_digest=rules_digest,
                                         environment=tournament.dump(environment)))
        db.add(Job(kind="compile", priority=100,
                   payload=json.dumps({"submission_id": submission.id, "practice_validation": True})))
        return {"submission_id": submission.id, "status": submission.status}

@app.post("/api/submissions")
def submit(body: Submit, player_id: int = Depends(authorized_player), idempotency_key: str | None = Header(default=None)):
    received = tournament.utcnow()
    with SessionLocal.begin() as db:
        tournament.serialize(db)
        t = db.scalar(select(tournament.Tournament).where(tournament.Tournament.status.not_in(["complete", "cancelled"])))
        if not t:
            raise HTTPException(409, "Chưa mở giải Tournament")
        r = db.scalar(select(tournament.TournamentRound).where(tournament.TournamentRound.tournament_id == t.id)
                      .order_by(tournament.TournamentRound.level.desc()).limit(1))
        if not r:
            raise HTTPException(409, "Chưa mở vòng nộp bot")
        entry = tournament.submit(db, t, r, player_id, body.language, body.source, idempotency_key, received)
        return {"submission_id": entry.submission_id, "status": "queued"}

@app.get("/api/submissions")
def submissions(player_id: int = Depends(authorized_player)):
    day_start, day_end = submission_day_bounds(datetime.now(VN).date())
    with SessionLocal() as db:
        rows = db.scalars(select(Submission).where(Submission.player_id == player_id).order_by(Submission.id.desc())).all()
        daily_ids = set(db.scalars(select(daily.DailyEntry.submission_id).where(daily.DailyEntry.player_id == player_id)))
        practice_ids = set(db.scalars(select(PracticeSubmissionRequest.submission_id).where(PracticeSubmissionRequest.player_id == player_id)))
        return [{"id": x.id, "language": x.language, "status": x.status, "compile_error": x.compile_error,
                 "mode": "daily" if x.id in daily_ids else "practice" if x.id in practice_ids else "tournament",
                 "created_at": x.created_at.isoformat() if x.created_at else None,
                 "eligible_today": bool(x.status == "ready" and x.created_at and day_start <= x.created_at < day_end)}
                for x in rows]

@app.get("/api/my/matches")
def my_matches(player_id: int = Depends(authorized_player)):
    with SessionLocal() as db:
        rows = db.scalars(select(Match).where((Match.player_a == player_id) | (Match.player_b == player_id)).order_by(Match.id.desc()).limit(100)).all()
        output = []
        for m in rows:
            opponent_id = m.player_b if m.player_a == player_id else m.player_a
            opponent = db.get(Player, opponent_id)
            pts = 0 if m.status == "void" else m.points_a if m.player_a == player_id else m.points_b
            result = "void" if m.status == "void" else "failed" if m.status == "failed" else "pending" if m.status != "done" else "draw" if m.winner_id is None else "win" if m.winner_id == player_id else "loss"
            detail = json.loads(m.detail) if m.detail else {}
            output.append({"id": m.id, "opponent": opponent.name if opponent else "Đội đối thủ", "kind": m.kind,
                           "event_day": m.event_day, "status": m.status, "result": result, "points": pts,
                           "win_reason": detail.get("win_reason"), "void_reason": detail.get("void_reason"),
                           "move_count": sum(1 for turn in detail.get("turns", []) if turn.get("piece_id") and turn.get("to"))})
        return output

@app.post("/api/challenges")
def challenge(body: Challenge, player_id: int = Depends(authorized_player)):
    raise HTTPException(409, "Challenge đang tắt; chỉ triển khai Tournament")

@app.get("/api/replays")
def replays(offset: int = 0, limit: int = 30):
    if offset < 0 or limit < 1 or limit > 100:
        raise HTTPException(400, "offset hoặc limit không hợp lệ")
    with SessionLocal() as db:
        matches = db.scalars(select(Match).where(Match.status == "done")
                             .order_by(Match.id.desc()).offset(offset).limit(limit + 1)).all()
        names = {p.id: p.name for p in db.scalars(select(Player)).all()}
        items = []
        for m in matches[:limit]:
            detail = json.loads(m.detail) if m.detail else {}
            items.append({"id": m.id, "team_a": names.get(m.player_a, "Đội A"),
                          "team_b": names.get(m.player_b, "Đội B"),
                          "winner": names.get(m.winner_id) if m.winner_id else None,
                          "event_day": m.event_day, "kind": m.kind,
                          "move_count": sum(1 for turn in detail.get("turns", []) if turn.get("piece_id") and turn.get("to"))})
        return {"items": items, "has_more": len(matches) > limit}

@app.get("/api/matches/{match_id}")
def get_match(match_id: int):
    with SessionLocal() as db:
        m = db.get(Match, match_id)
        if not m: raise HTTPException(404, "Không tìm thấy trận")
        team_a, team_b = db.get(Player, m.player_a), db.get(Player, m.player_b)
        return {"id": m.id, "player_a": m.player_a, "player_b": m.player_b,
                "team_a": team_a.name if team_a else "Đội A", "team_b": team_b.name if team_b else "Đội B",
                "event_day": m.event_day, "kind": m.kind, "status": m.status, "worker_id": m.worker_id,
                "winner_id": m.winner_id, "points_a": m.points_a, "points_b": m.points_b,
                "initial_pieces": new_game()["pieces"],
                "detail": json.loads(m.detail) if m.detail else None}

@app.get("/api/leaderboard")
def leaderboard(tournament_id: int | None = None, day: str | None = None):
    with SessionLocal() as db:
        selected=(tournament.get_tournament(db,tournament_id) if tournament_id is not None else
                  db.scalar(select(tournament.Tournament).order_by(tournament.Tournament.id.desc()).limit(1)))
        return tournament.tournament_leaderboard(db,selected) if selected else []

