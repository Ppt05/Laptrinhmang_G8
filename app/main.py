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
from .models import EventControl, Job, Match, Player, Submission, WorkerNode
from .event_schedule import as_vietnam_time, get_event_state, submission_day_bounds
from .security import authorized_player, check_registration, digest, hash_password, new_token, verify_password
from .schema import ensure_schema
from . import tournament
from .tournament_api import router as tournament_router

app = FastAPI(title="OanTuTi Bot League", version="0.2.0", description="OTTv2: game chiến thuật bàn cờ 9×9",
              docs_url=None, redoc_url=None, openapi_url=None)
VN = ZoneInfo(os.getenv("EVENT_TIMEZONE", "Asia/Ho_Chi_Minh"))
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")
app.include_router(tournament_router)

@app.on_event("startup")
def startup():
    ensure_schema()

class Register(BaseModel): name: str = Field(min_length=2, max_length=60); password: str = Field(min_length=8, max_length=128)
class Login(BaseModel): name: str = Field(min_length=2, max_length=60); password: str = Field(min_length=1, max_length=128)
class ClaimAccount(BaseModel): name: str = Field(min_length=2, max_length=60); registration_token: str; password: str = Field(min_length=8, max_length=128)
class Submit(BaseModel): language: str; source: str = Field(min_length=1)
class Challenge(BaseModel): opponent_id: int
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
        Match.status != "void",
        (Match.player_a.in_(team_ids)) | (Match.player_b.in_(team_ids)),
    )
    if exclude_id is not None:
        query = query.where(Match.id != exclude_id)
    return db.scalar(query)

def matches_engine_enabled():
    return os.getenv("MATCHES_ENABLED", "false").lower() == "true"

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

@app.get("/api/meta")
def meta():
    with SessionLocal() as db:
        active = db.scalar(select(tournament.Tournament).where(tournament.Tournament.status.not_in(["complete", "cancelled"])))
        return {"name": "OanTuTi Bot League", "protocol_version": 2,
                "modes": {"tournament": True, "daily": False, "challenge": False, "practice": False},
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
        return {"voided_match_id": m.id, "status": "void"}

@app.get("/health")
def health(): return {"ok": True}

@app.post("/api/admin/login")
def admin_login(body: AdminLogin):
    expected_username = os.getenv("ADMIN_USERNAME", "admin")
    expected_password = os.getenv("ADMIN_PASSWORD") or os.getenv("ADMIN_API_KEY", "")
    if not expected_password:
        raise HTTPException(503, "Chưa cấu hình tài khoản ban tổ chức")
    if not secrets.compare_digest(body.username, expected_username) or not secrets.compare_digest(body.password, expected_password):
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
        return [{"id": x.id, "language": x.language, "status": x.status, "compile_error": x.compile_error,
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
            result = "void" if m.status == "void" else "pending" if m.status != "done" else "draw" if m.winner_id is None else "win" if m.winner_id == player_id else "loss"
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

