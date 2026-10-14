import base64, hashlib, hmac, json, os, re, secrets, time
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from pathlib import Path
from .db import Base, SessionLocal, engine
from .models import EventControl, Job, Match, Player, Submission
from .event_schedule import as_vietnam_time, get_event_state
from .security import authorized_player, check_registration, digest, hash_password, new_token, verify_password

app = FastAPI(title="OanTuTi Bot League", version="0.2.0", description="OTTv2: game chiến thuật bàn cờ 9×9",
              docs_url=None, redoc_url=None, openapi_url=None)
VN = ZoneInfo(os.getenv("EVENT_TIMEZONE", "Asia/Ho_Chi_Minh"))
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")

@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE players ADD COLUMN IF NOT EXISTS password_hash VARCHAR(128)"))

class Register(BaseModel): name: str = Field(min_length=2, max_length=60); password: str = Field(min_length=8, max_length=128)
class Login(BaseModel): name: str = Field(min_length=2, max_length=60); password: str = Field(min_length=1, max_length=128)
class ClaimAccount(BaseModel): name: str = Field(min_length=2, max_length=60); registration_token: str; password: str = Field(min_length=8, max_length=128)
class Submit(BaseModel): language: str; source: str = Field(min_length=1)
class Challenge(BaseModel): opponent_id: int
class EventScheduleInput(BaseModel): start_at: datetime; end_at: datetime; enabled: bool
class AdminLogin(BaseModel): username: str = Field(min_length=1, max_length=80); password: str = Field(min_length=1, max_length=256)

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

def latest_ready(db, player_id):
    return db.scalar(select(Submission).where(Submission.player_id == player_id, Submission.status == "ready").order_by(Submission.id.desc()))

@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")

@app.get("/admin")
def admin_page():
    return FileResponse(WEB_DIR / "admin.html")

@app.get("/api/meta")
def meta():
    with SessionLocal() as db:
        return {"name": "OanTuTi Bot League", "protocol_version": 2,
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

@app.get("/health")
def health(): return {"ok": True}

@app.post("/api/admin/login")
def admin_login(body: AdminLogin):
    expected_username = os.getenv("ADMIN_USERNAME", "admin")
    expected_password = os.getenv("ADMIN_PASSWORD", "")
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
def submit(body: Submit, player_id: int = Depends(authorized_player)):
    lang = body.language.lower()
    if lang not in ("cpp", "python"): raise HTTPException(400, "language phải là cpp hoặc python")
    if len(body.source.encode("utf-8")) > int(os.getenv("MAX_SOURCE_BYTES", "100000")): raise HTTPException(413, "Mã nguồn vượt giới hạn 100 KB")
    with SessionLocal() as db:
        s = Submission(player_id=player_id, language=lang, source=body.source); db.add(s); db.flush()
        db.add(Job(kind="compile", payload=json.dumps({"submission_id": s.id}))); db.commit()
        return {"submission_id": s.id, "status": "queued"}

@app.get("/api/submissions")
def submissions(player_id: int = Depends(authorized_player)):
    with SessionLocal() as db:
        rows = db.scalars(select(Submission).where(Submission.player_id == player_id).order_by(Submission.id.desc())).all()
        return [{"id": x.id, "language": x.language, "status": x.status, "compile_error": x.compile_error,
                 "created_at": x.created_at.isoformat() if x.created_at else None} for x in rows]

@app.get("/api/my/matches")
def my_matches(player_id: int = Depends(authorized_player)):
    with SessionLocal() as db:
        rows = db.scalars(select(Match).where((Match.player_a == player_id) | (Match.player_b == player_id)).order_by(Match.id.desc()).limit(100)).all()
        output = []
        for m in rows:
            opponent_id = m.player_b if m.player_a == player_id else m.player_a
            opponent = db.get(Player, opponent_id)
            pts = m.points_a if m.player_a == player_id else m.points_b
            result = "pending" if m.status != "done" else "draw" if m.winner_id is None else "win" if m.winner_id == player_id else "loss"
            detail = json.loads(m.detail) if m.detail else {}
            output.append({"id": m.id, "opponent": opponent.name if opponent else "Đội đối thủ", "kind": m.kind,
                           "event_day": m.event_day, "status": m.status, "result": result, "points": pts,
                           "win_reason": detail.get("win_reason"), "move_count": len(detail.get("turns", []))})
        return output

@app.post("/api/challenges")
def challenge(body: Challenge, player_id: int = Depends(authorized_player)):
    with SessionLocal() as db:
        state = get_event_state(db, matches_engine_enabled())
    if not state["matches_enabled"]:
        if state["window_active"] and not state["worker_enabled"]:
            raise HTTPException(503, "Đã đến lịch thi đấu nhưng máy chủ chạy bot chưa được cấu hình")
        raise HTTPException(400, "Hiện không nằm trong khung giờ thi đấu")
    if body.opponent_id == player_id: raise HTTPException(400, "Không thể thách đấu chính mình")
    with SessionLocal() as db:
        a, b = latest_ready(db, player_id), latest_ready(db, body.opponent_id)
        if not a or not b: raise HTTPException(409, "Cả hai đội cần có bot đã biên dịch thành công")
        participants = [player_id, body.opponent_id]
        active = db.scalar(select(Match.id).where(Match.status.in_(["queued", "running"]), (Match.player_a.in_(participants)) | (Match.player_b.in_(participants))))
        if active: raise HTTPException(409, "Bạn đã có một trận đang chờ hoặc đang chạy")
        today = datetime.now(VN).date().isoformat()
        m = Match(player_a=player_id, player_b=body.opponent_id, submission_a=a.id, submission_b=b.id, event_day=today, kind="challenge")
        db.add(m); db.flush(); db.add(Job(kind="match", payload=json.dumps({"match_id": m.id}))); db.commit()
        return {"match_id": m.id, "status": "queued"}

@app.get("/api/matches/{match_id}")
def get_match(match_id: int):
    with SessionLocal() as db:
        m = db.get(Match, match_id)
        if not m: raise HTTPException(404, "Không tìm thấy trận")
        return {"id": m.id, "player_a": m.player_a, "player_b": m.player_b, "kind": m.kind, "status": m.status, "winner_id": m.winner_id, "points_a": m.points_a, "points_b": m.points_b, "detail": json.loads(m.detail) if m.detail else None}

@app.get("/api/leaderboard")
def leaderboard():
    with SessionLocal() as db:
        players = db.scalars(select(Player).order_by(Player.id)).all()
        rows = []
        for p in players:
            matches = db.scalars(select(Match).where(Match.status == "done", (Match.player_a == p.id) | (Match.player_b == p.id))).all()
            pts = sum(m.points_a if m.player_a == p.id else m.points_b for m in matches)
            wins = sum(m.winner_id == p.id for m in matches)
            rows.append({"player_id": p.id, "name": p.name, "points": pts, "wins": wins, "matches": len(matches)})
        return sorted(rows, key=lambda x: (-x["points"], -x["wins"], x["player_id"]))

@app.post("/api/admin/pair-daily")
def pair_daily(x_admin_key: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_key, authorization)
    with SessionLocal() as db:
        state = get_event_state(db, matches_engine_enabled())
    if not state["matches_enabled"]:
        raise HTTPException(503, "Chưa thể ghép trận: cần lịch đang mở và worker chạy bot")
    now = datetime.now(VN)
    day = now.date().isoformat()
    with SessionLocal() as db:
        if db.scalar(select(Match.id).where(Match.event_day == day, Match.kind == "daily")): return {"created": 0, "reason": "daily pairing already started"}
        players = db.scalars(select(Player).order_by(Player.id)).all()
        eligible = [(p, latest_ready(db, p.id)) for p in players]
        eligible = [(p, s) for p, s in eligible if s]
        secrets.SystemRandom().shuffle(eligible)
        pairs = list(zip(eligible[::2], eligible[1::2])); created = 0
        for (a, sa), (b, sb) in pairs:
            m = Match(player_a=a.id, player_b=b.id, submission_a=sa.id, submission_b=sb.id, event_day=day, kind="daily")
            db.add(m); db.flush(); db.add(Job(kind="match", payload=json.dumps({"match_id": m.id}))); created += 1
        db.commit(); return {"created": created, "unpaired": len(eligible) % 2, "day": day}
