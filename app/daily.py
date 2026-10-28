"""Daily opt-in, quotas, scheduling and ratings share the Tournament lock."""
import hashlib
import json
import re
import random
import secrets
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Query
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy import select, func, case, union_all

from . import tournament
from .db import SessionLocal
from .event_schedule import VN
from .models import DailyControl, DailyDraw, DailyEntry, DailySearch, EloEvent, Job, Match, Player, Submission
from .security import authorized_player, bearer_scheme
from .tournament_api import organizer

router = APIRouter()
MATCH_LIMIT = 5
SUBMISSION_LIMIT = 5

# These published revisions differ only in Daily quotas/search policy, not in
# board rules or the bot protocol. Keep stored snapshots intact. Unknown future
# revisions remain isolated until their compatibility is reviewed explicitly.
DAILY_RULES_COMPATIBILITY = {
    "d7eae1c8d7e494b7db39a38a76b5ffb4c264318aefc0c21cb992acdfedf150f8": "daily-board-functions-v1",
    "76094ec355024039facbb4c3791d303d21852e629bb01c95bde5833ba5719fd8": "daily-board-functions-v1",
}


def today():
    return datetime.now(VN).date().isoformat()


def enabled(db):
    control = db.get(DailyControl, 1)
    return bool(control and control.enabled)


def tournament_busy(db, players):
    if not players:
        return False
    return bool(db.scalar(select(tournament.Encounter.id)
        .join(tournament.TournamentRound, tournament.Encounter.round_id == tournament.TournamentRound.id)
        .join(tournament.Tournament, tournament.TournamentRound.tournament_id == tournament.Tournament.id)
        .where(tournament.Tournament.status.not_in(["complete", "cancelled"]),
               tournament.Encounter.status.not_in(["finalized", "cancelled"]),
               (tournament.Encounter.player_a.in_(players)) | (tournament.Encounter.player_b.in_(players)))
        .limit(1)))


def active_match(db, players, day=None, exclude=None):
    q = select(Match).where(Match.kind == "daily", Match.status != "void",
        (Match.player_a.in_(players)) | (Match.player_b.in_(players)))
    if day is not None:
        q = q.where(Match.event_day == day)
    else:
        q = q.where(Match.status.in_(["queued", "running", "failed"]))
    if exclude is not None:
        q = q.where(Match.id != exclude)
    return db.scalar(q.order_by(Match.id.desc()).limit(1))


def entry_for(db, pid, day):
    return db.scalar(select(DailyEntry).where(DailyEntry.player_id == pid, DailyEntry.day == day)
                     .order_by(DailyEntry.id.desc()).limit(1))


def used_matches(db, pid, day):
    return db.scalar(select(func.count(Match.id)).where(Match.kind == "daily", Match.event_day == day,
        Match.status != "void", (Match.player_a == pid) | (Match.player_b == pid))) or 0


def waiting_search(db, pid, day=None):
    q = select(DailySearch).where(DailySearch.player_id == pid, DailySearch.status == "waiting")
    if day is not None:
        q = q.where(DailySearch.day == day)
    return db.scalar(q.limit(1))


def expire_searches(db):
    for search in db.scalars(select(DailySearch).where(DailySearch.status == "waiting", DailySearch.day != today())):
        search.status = "expired"
    db.flush()


def state(db, pid):
    day = today()
    entry = entry_for(db, pid, day)
    sub = db.get(Submission, entry.submission_id) if entry else None
    match = active_match(db, [pid])
    busy = tournament_busy(db, [pid])
    entries = db.scalars(select(DailyEntry).where(DailyEntry.player_id == pid, DailyEntry.day == day)
                         .order_by(DailyEntry.id.desc())).all()
    submissions = [db.get(Submission, e.submission_id) for e in entries]
    search = waiting_search(db, pid, day)
    used = used_matches(db, pid, day)
    return {"day": day, "enabled": enabled(db), "timezone": "Asia/Ho_Chi_Minh",
        "opted_in": bool(search), "tournament_busy": busy,
        "match_limit": MATCH_LIMIT, "matches_used": used, "matches_remaining": max(0, MATCH_LIMIT-used),
        "submission_limit": SUBMISSION_LIMIT, "submissions_used": len(entries),
        "submissions_remaining": max(0, SUBMISSION_LIMIT-len(entries)),
        "can_submit": enabled(db) and len(entries) < SUBMISSION_LIMIT,
        "can_search": enabled(db) and used < MATCH_LIMIT and not busy and not match and not search,
        "search": {"id": search.id, "submission_id": db.get(DailyEntry, search.entry_id).submission_id} if search else None,
        "submissions": [{"id": s.id, "language": s.language, "status": s.status, "compile_error": s.compile_error} for s in submissions],
        "submission": {"id": sub.id, "status": sub.status, "compile_error": sub.compile_error} if sub else None,
        "match": {"id": match.id, "status": match.status, "day": match.event_day} if match else None}


def submit(db, pid, language, source, key):
    tournament.serialize(db)
    tournament.check(key and key.strip() and len(key) <= 128, "Cần Idempotency-Key hợp lệ", 400)
    day = today()
    previous = db.scalar(select(DailyEntry).where(DailyEntry.player_id == pid, DailyEntry.day == day,
                                                  DailyEntry.request_key == key))
    language = language.lower()
    if previous:
        s = db.get(Submission, previous.submission_id)
        tournament.check(previous.request_key == key and s.language == language and s.source == source,
                         "Idempotency-Key đã được dùng cho nội dung khác", 409)
        return s
    tournament.check(enabled(db), "Daily đang tắt")
    used = db.scalar(select(func.count(DailyEntry.id)).where(DailyEntry.player_id == pid, DailyEntry.day == day)) or 0
    tournament.check(used < SUBMISSION_LIMIT, f"Đã dùng hết {SUBMISSION_LIMIT} lượt nộp Daily hôm nay", 429)
    tournament.check(language in ("python", "cpp"), "Ngôn ngữ phải là python hoặc cpp", 400)
    try:
        size = len(source.encode("utf-8"))
    except UnicodeEncodeError:
        size = 0
    tournament.check(0 < size <= 100000 and "\x00" not in source, "Mã nguồn UTF-8 tối đa 100 KB", 400)
    env = tournament.environment()
    tournament.check(re.fullmatch(r"(?:.+@)?sha256:[0-9a-f]{64}", env["image"]),
                     "BTC cần cố định image sandbox trước khi nhận Daily", 409)
    rules = (Path(__file__).resolve().parents[1] / "GAMERULE.md").read_text(encoding="utf-8")
    s = Submission(player_id=pid, language=language, source=source)
    db.add(s); db.flush()
    db.add(DailyEntry(player_id=pid, day=day, submission_id=s.id, request_key=key,
                      rules_digest=hashlib.sha256(rules.encode()).hexdigest(), environment=tournament.dump(env)))
    db.add(Job(kind="compile", priority=100, payload=json.dumps({"submission_id": s.id, "daily_validation": True})))
    db.flush()
    return s


def find_match(db, pid, sid, key):
    tournament.serialize(db)
    tournament.check(key and key.strip() and len(key) <= 128, "Cần Idempotency-Key hợp lệ", 400)
    expire_searches(db)
    previous = db.scalar(select(DailySearch).where(DailySearch.player_id == pid, DailySearch.request_key == key))
    if previous:
        tournament.check(db.get(DailyEntry, previous.entry_id).submission_id == sid,
                         "Idempotency-Key đã dùng cho bot khác")
        return previous
    tournament.check(enabled(db), "Daily đang tắt")
    tournament.check(not tournament_busy(db, [pid]), "Đội đang chờ hoặc thi Tournament")
    tournament.check(not active_match(db, [pid]) and not waiting_search(db, pid), "Đội đã tìm trận hoặc còn trận chưa xử lý")
    tournament.check(used_matches(db, pid, today()) < MATCH_LIMIT, "Đã dùng hết 5 trận Daily hôm nay", 429)
    entry = db.scalar(select(DailyEntry).join(Submission, DailyEntry.submission_id == Submission.id)
        .where(DailyEntry.player_id == pid, DailyEntry.day == today(), DailyEntry.submission_id == sid,
               Submission.status == "ready"))
    tournament.check(entry is not None, "Chọn bot Daily sẵn sàng của đội, nộp trong hôm nay")
    search = DailySearch(player_id=pid, day=today(), entry_id=entry.id, request_key=key)
    db.add(search); db.flush()
    return search


def cancel_search(db, pid):
    tournament.serialize(db)
    expire_searches(db)
    search = waiting_search(db, pid)
    if search:
        search.status = "cancelled"
    else:
        tournament.check(not active_match(db, [pid]), "Trận đã ghép, không thể hủy tìm trận")
    return {"status": "cancelled"}


def pairing_plan(players, history, seed):
    """FIFO; prefer new opponents, then the smallest side imbalance; seeded ties."""
    rng = random.Random(seed)
    balance = {p: 0 for p in players}
    meetings = {}
    for a, b in history:
        balance[a] = balance.get(a, 0) + 1
        balance[b] = balance.get(b, 0) - 1
        key = tuple(sorted((a, b)))
        meetings[key] = meetings.get(key, 0) + 1
    pool, pairs = list(players), []
    while len(pool) >= 2:
        a = pool.pop(0)
        def costs(b):
            ab = abs(balance[a]+1) + abs(balance[b]-1)
            ba = abs(balance[a]-1) + abs(balance[b]+1)
            return meetings.get(tuple(sorted((a, b))), 0), min(ab, ba)
        best = min(costs(b) for b in pool)
        b = rng.choice([b for b in pool if costs(b) == best]); pool.remove(b)
        ab = abs(balance[a]+1) + abs(balance[b]-1)
        ba = abs(balance[a]-1) + abs(balance[b]+1)
        south, north = ((a, b) if ab < ba or (ab == ba and rng.choice([True, False])) else (b, a))
        pairs.append((south, north))
    return pairs, pool


def pair(db, day=None):
    tournament.serialize(db)
    expire_searches(db)
    day = day or today()
    if not enabled(db) or day != today():
        return []
    searches = db.scalars(select(DailySearch).where(DailySearch.day == day, DailySearch.status == "waiting")
                          .order_by(DailySearch.id)).all()
    candidates = []
    for search in searches:
        e = db.get(DailyEntry, search.entry_id)
        if used_matches(db, e.player_id, day) >= MATCH_LIMIT:
            search.status = "expired"
        elif (db.get(Submission, e.submission_id).status == "ready" and not active_match(db, [e.player_id])
              and not tournament_busy(db, [e.player_id])):
            candidates.append((e, search))
    # Environment/rules snapshots must agree even when an update occurs midday.
    pools = {}
    for e, search in candidates:
        rules = DAILY_RULES_COMPATIBILITY.get(e.rules_digest, e.rules_digest)
        pools.setdefault((rules, e.environment), []).append((e, search))
    created = []
    for pool in pools.values():
        if len(pool) < 2:
            continue
        choices = [e.player_id for e, _ in pool]
        lookup = {e.player_id: (e, search) for e, search in pool}
        seed = secrets.token_hex(32)
        history = [(m.player_a, m.player_b) for m in db.scalars(select(Match).where(
            Match.kind == "daily", Match.event_day == day, Match.status != "void").order_by(Match.id))]
        planned, waiting = pairing_plan(choices, history, seed)
        results = []
        for south, north in planned:
            (a, search_a), (b, search_b) = lookup[south], lookup[north]
            m = Match(player_a=a.player_id, player_b=b.player_id, submission_a=a.submission_id,
                      submission_b=b.submission_id, event_day=day, kind="daily", status="queued")
            db.add(m); db.flush()
            db.add(Job(kind="match", priority=100, payload=json.dumps({"match_id": m.id})))
            created.append(m.id)
            results.append({"match_id": m.id, "south": a.player_id, "north": b.player_id})
            search_a.status = search_b.status = "paired"
            search_a.match_id = search_b.match_id = m.id
        db.add(DailyDraw(day=day, choices=json.dumps(choices), seed=seed,
                        method="fifo-opponent-side-balance-v2",
                        result=json.dumps({"matches": results, "waiting": waiting, "history": history})))
    return created


def after_game(db, match):
    if match.kind != "daily":
        return
    event = db.scalar(select(EloEvent).where(EloEvent.match_id == match.id))
    if match.status == "done":
        if event is None:
            db.add(EloEvent(match_id=match.id, player_a=match.player_a, player_b=match.player_b,
                            score=match.points_a))
        else:
            event.active, event.score = True, match.points_a
    elif event:
        event.active = False
    tournament.recalculate_elo(db)


def leaderboard(db, day=None):
    if day:
        try:
            date.fromisoformat(day)
        except ValueError:
            tournament.check(False, "Ngày phải có dạng YYYY-MM-DD", 400)
    q = select(Match).where(Match.kind == "daily", Match.status == "done")
    if day:
        q = q.where(Match.event_day == day)
    rows = {}
    for m in db.scalars(q):
        for pid, score in ((m.player_a, m.points_a), (m.player_b, m.points_b)):
            row = rows.setdefault(pid, {"player_id": pid, "points": 0, "matches": 0, "wins": 0})
            row["points"] += score; row["matches"] += 1; row["wins"] += int(score == 1)
    ratings = tournament.ratings(db)
    for pid, row in rows.items():
        row.update(name=db.get(Player, pid).name, elo=ratings.get(pid, 1000))
    return sorted(rows.values(), key=lambda r: (-r["elo"], -r["points"], r["player_id"]))


class BotInput(BaseModel):
    language: str
    source: str = Field(min_length=1)


class ToggleInput(BaseModel):
    enabled: StrictBool


class OptInInput(BaseModel):
    opted_in: StrictBool


class SearchInput(BaseModel):
    submission_id: int = Field(gt=0)


@router.post("/api/daily/queue", status_code=202)
def search(body: SearchInput, pid: int = Depends(authorized_player),
           idempotency_key: str | None = Header(default=None)):
    with SessionLocal.begin() as db:
        s = find_match(db, pid, body.submission_id, idempotency_key)
        return {"search_id": s.id, "status": s.status, "match_id": s.match_id}


@router.delete("/api/daily/queue")
def cancel(pid: int = Depends(authorized_player)):
    with SessionLocal.begin() as db:
        return cancel_search(db, pid)


@router.get("/api/daily/state")
def get_state(pid: int = Depends(authorized_player)):
    with SessionLocal() as db:
        return state(db, pid)


@router.post("/api/daily/submissions", status_code=202)
def post_submission(body: BotInput, pid: int = Depends(authorized_player),
                    idempotency_key: str | None = Header(default=None)):
    with SessionLocal.begin() as db:
        s = submit(db, pid, body.language, body.source, idempotency_key)
        return {"submission_id": s.id, "status": s.status}


@router.put("/api/daily/opt-in")
def opt_in(body: OptInInput, pid: int = Depends(authorized_player)):
    with SessionLocal.begin() as db:
        tournament.check(not body.opted_in, "Chọn bot và dùng nút Tìm trận Daily")
        cancel_search(db, pid)
        return {"opted_in": False}


@router.post("/api/daily/matches/{mid}/retry", status_code=202)
def retry(mid: int, pid: int = Depends(authorized_player)):
    with SessionLocal.begin() as db:
        tournament.serialize(db)
        m = db.get(Match, mid)
        tournament.check(m and m.kind == "daily" and pid in (m.player_a, m.player_b), "Không tìm thấy trận Daily", 404)
        tournament.check(m.status == "failed", "Chỉ chạy lại trận lỗi hạ tầng")
        tournament.check(not tournament_busy(db, [m.player_a, m.player_b]), "Một đội đang bận Tournament")
        tournament.check(not active_match(db, [m.player_a, m.player_b], exclude=mid), "Một đội có Daily khác chưa hoàn tất")
        m.status, m.detail = "queued", None
        db.add(Job(kind="match", priority=100, payload=json.dumps({"match_id": m.id})))
        return {"match_id": m.id, "status": m.status}


@router.get("/api/daily/leaderboard")
def get_leaderboard(day: str | None = None, offset: int = Query(default=0, ge=0),
                    limit: int = Query(default=10, ge=1, le=100), locate_me: bool = False,
                    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    pid = authorized_player(credentials) if credentials else None
    with SessionLocal() as db:
        return leaderboard_page(db, day, offset, limit, pid, locate_me)


def leaderboard_page(db, day=None, offset=0, limit=10, player_id=None, locate_me=False):
    """Aggregate and rank in PostgreSQL; return only the requested page and own row."""
    tournament.check(offset >= 0 and 1 <= limit <= 100, "Phân trang không hợp lệ", 400)
    if day:
        try:
            date.fromisoformat(day)
        except ValueError:
            tournament.check(False, "Ngày phải có dạng YYYY-MM-DD", 400)
    conditions = [Match.kind == "daily", Match.status == "done"]
    if day:
        conditions.append(Match.event_day == day)
    scores = union_all(
        select(Match.player_a.label("pid"), Match.points_a.label("score")).where(*conditions),
        select(Match.player_b.label("pid"), Match.points_b.label("score")).where(*conditions),
    ).cte("daily_scores")
    totals = select(scores.c.pid, func.sum(scores.c.score).label("points"),
                    func.count().label("matches"),
                    func.sum(case((scores.c.score == 1, 1), else_=0)).label("wins"))\
        .group_by(scores.c.pid).cte("daily_totals")
    events = union_all(
        select(EloEvent.player_a.label("pid"), EloEvent.after_a.label("elo"), EloEvent.id.label("eid"))
            .where(EloEvent.active.is_(True)),
        select(EloEvent.player_b.label("pid"), EloEvent.after_b.label("elo"), EloEvent.id.label("eid"))
            .where(EloEvent.active.is_(True)),
    ).cte("rating_sides")
    latest = select(events.c.pid, events.c.elo).distinct(events.c.pid)\
        .order_by(events.c.pid, events.c.eid.desc()).cte("latest_rating")
    elo = func.coalesce(latest.c.elo, 1000)
    ranked = select(totals.c.pid.label("player_id"), Player.name, totals.c.points,
        totals.c.matches, totals.c.wins, elo.label("elo"),
        func.row_number().over(order_by=(elo.desc(), totals.c.points.desc(), totals.c.pid)).label("rank"))\
        .join(Player, Player.id == totals.c.pid).outerjoin(latest, latest.c.pid == totals.c.pid).cte("daily_ranks")
    total = db.scalar(select(func.count()).select_from(totals))
    mine = db.execute(select(ranked).where(ranked.c.player_id == player_id)).mappings().first() if player_id else None
    if locate_me and mine:
        offset = ((mine["rank"] - 1) // limit) * limit
    elif offset >= total:
        offset = ((total - 1) // limit) * limit if total else 0
    items = db.execute(select(ranked).order_by(ranked.c.rank).offset(offset).limit(limit)).mappings().all()
    return {"items": [dict(row) for row in items], "total": total, "offset": offset, "limit": limit,
            "has_previous": offset > 0, "has_next": offset + len(items) < total,
            "me": dict(mine) if mine else None, "signed_in": player_id is not None}


@router.post("/api/daily/submissions/{sid}/retry", status_code=202)
def retry_compile(sid: int, pid: int = Depends(authorized_player)):
    with SessionLocal.begin() as db:
        tournament.serialize(db)
        entry = db.scalar(select(DailyEntry).where(DailyEntry.submission_id == sid, DailyEntry.player_id == pid))
        tournament.check(entry is not None, "Không tìm thấy bài Daily", 404)
        tournament.check(entry.day == today(), "Bài Daily đã hết ngày")
        s = db.get(Submission, sid)
        tournament.check(s.status == "infrastructure_error", "Chỉ kiểm tra lại bài lỗi hạ tầng")
        s.status, s.compile_error = "queued", None
        db.add(Job(kind="compile", priority=100, payload=json.dumps({"submission_id": sid, "daily_validation": True})))
        return {"submission_id": sid, "status": s.status}


@router.get("/api/admin/daily/matches")
def admin_matches(offset: int = 0, limit: int = 10, actor: str = Depends(organizer)):
    tournament.check(offset >= 0 and 1 <= limit <= 100, "Phân trang không hợp lệ", 400)
    with SessionLocal() as db:
        names = {p.id: p.name for p in db.scalars(select(Player))}
        matches = db.scalars(select(Match).where(Match.kind == "daily").order_by(Match.id.desc())
                             .offset(offset).limit(limit+1)).all()
        return {"items": [{"id": m.id, "day": m.event_day, "team_a": names.get(m.player_a),
                 "team_b": names.get(m.player_b), "status": m.status,
                 "player_a": m.player_a, "player_b": m.player_b, "winner_id": m.winner_id,
                 "points_a": m.points_a, "points_b": m.points_b} for m in matches[:limit]],
                "has_more": len(matches) > limit}


@router.get("/api/admin/daily/draws")
def admin_draws(actor: str = Depends(organizer)):
    with SessionLocal() as db:
        names = {str(p.id): p.name for p in db.scalars(select(Player))}
        return [{"id": row.id, "day": row.day, "choices": json.loads(row.choices), "seed": row.seed,
                 "method": row.method, "result": json.loads(row.result), "reason": row.reason,
                 "actor": row.actor, "created_at": row.created_at.isoformat(),
                 "names": {str(pid): names.get(str(pid), f"Đội #{pid}") for pid in json.loads(row.choices)}}
                for row in db.scalars(select(DailyDraw).order_by(DailyDraw.id.desc()).limit(100))]


@router.get("/api/admin/daily")
def get_control(actor: str = Depends(organizer)):
    with SessionLocal() as db:
        return {"enabled": enabled(db)}


@router.put("/api/admin/daily")
def set_control(body: ToggleInput, actor: str = Depends(organizer)):
    with SessionLocal.begin() as db:
        tournament.serialize(db)
        if body.enabled:
            tournament.check(re.fullmatch(r"(?:.+@)?sha256:[0-9a-f]{64}", tournament.environment()["image"]),
                             "Cần cố định SANDBOX_IMAGE của API và worker trước khi bật Daily")
        control = db.get(DailyControl, 1)
        if control is None:
            control = DailyControl(id=1); db.add(control)
        control.enabled = body.enabled
        return {"enabled": control.enabled}
