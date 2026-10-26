"""Tournament lifecycle. Call mutations inside a transaction with serialize().

Match remains one game; only Encounter owns tournament points and advancement.
Rules snapshots, bot locks and recorded random draws are immutable after lock.
"""
import hashlib
import itertools
import json
import os
import random
import re
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from .models import (Tournament, Enrollment, TournamentGroup, GroupMember,
                     TournamentRound, RoundSubmission, LockedBot, Encounter,
                     RecordedDraw, OrganizerAction, EloEvent, GameAttempt,
                     Player, Submission, Match, Job, WorkerNode)

QUEUED_JOBS = ("queued", "tournament_queued")
RUNNING_JOBS = ("running", "tournament_running")
ACTIVE_JOBS = QUEUED_JOBS + RUNNING_JOBS
QUEUE_GUARD = "worker_queue_guard"


def round_paused(db, r):
    return bool(db.scalar(select(OrganizerAction.id).where(OrganizerAction.round_id == r.id,
        OrganizerAction.kind == "pause_round", OrganizerAction.hold.is_(True),
        OrganizerAction.resolved_at.is_(None)).limit(1)))


def round_job(r, kind, payload):
    return Job(kind=kind, round_id=r.id, priority=0, status="tournament_queued", payload=dump(payload))


def control_round(db, t, r):
    check(t.status not in ("complete", "cancelled") and r.status not in ("finalized", "cancelled"),
          "Vòng hoặc giải đã kết thúc")


def select_worker(db, t, r, worker_id, actor):
    control_round(db, t, r)
    if worker_id is not None:
        node = db.get(WorkerNode, worker_id)
        check(node and node.round_control_version >= 1, "Worker chưa hỗ trợ điều khiển vòng; cần cập nhật worker", 400)
        check(node.last_seen >= utcnow() - timedelta(seconds=30), "Worker đang ngoại tuyến", 409)
    if worker_id == r.worker_id:
        return
    check(not db.scalar(select(Job.id).where(Job.round_id == r.id, Job.status.in_(RUNNING_JOBS)).limit(1)),
          "Dừng vòng trước khi đổi worker đang chạy")
    before = r.worker_id
    r.worker_id = worker_id
    audit(db, t, "select_worker", {"before": before, "worker_id": worker_id}, actor, r)


def pause_round(db, t, r, actor):
    control_round(db, t, r)
    if round_paused(db, r):
        return
    try:
        jobs = db.scalars(select(Job).where(Job.round_id == r.id, Job.status.in_(ACTIVE_JOBS))
            .order_by(Job.id).with_for_update(nowait=True).execution_options(populate_existing=True)).all()
    except OperationalError as error:
        if getattr(error.orig, "sqlstate", None) == "55P03":
            raise HTTPException(409, "Worker đang lưu kết quả; thử dừng lại sau") from error
        raise
    for job in jobs:
        payload = json.loads(job.payload)
        if job.kind == "match":
            match = db.get(Match, payload["match_id"])
            attempt = db.scalar(select(GameAttempt).where(GameAttempt.match_id == match.id,
                                                         GameAttempt.attempt == job.attempts))
            if attempt and attempt.status == "running":
                attempt.status, attempt.finished_at = "abandoned", utcnow()
                attempt.detail = "Round stopped by organizer; unfinished result discarded"
            if match.status != "done":
                match.status, match.worker_id, match.detail, match.winner_id = "scheduled", None, None, None
                match.points_a = match.points_b = 0
        job.status, job.worker_id, job.claim_token, job.claimed_at = "cancelled", None, None, None
    audit(db, t, "pause_round", {"cancelled_jobs": [j.id for j in jobs]}, actor, r, hold=True)
    t.auto_status = "Đã dừng vòng; chọn worker rồi bấm Tiếp tục vòng."


def resume_round(db, t, r, actor):
    control_round(db, t, r)
    if not round_paused(db, r):
        return
    for action in db.scalars(select(OrganizerAction).where(OrganizerAction.round_id == r.id,
            OrganizerAction.kind == "pause_round", OrganizerAction.resolved_at.is_(None))):
        action.resolved_at = utcnow()
    db.flush()
    # Compilation that was interrupted keeps the same submission and quota slot.
    for link in db.scalars(select(RoundSubmission).where(RoundSubmission.round_id == r.id)):
        submission = db.get(Submission, link.submission_id)
        if submission.status in ("queued", "running"):
            db.add(round_job(r, "compile", {"submission_id": submission.id, "round_id": r.id}))
    audit(db, t, "resume_round", {"worker_id": r.worker_id}, actor, r)
    t.auto_status = None
    db.flush()
    pump(db)


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=lambda obj: obj.isoformat())


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    if value.tzinfo is None:
        raise HTTPException(400, "Thời gian phải có múi giờ")
    return value.astimezone(timezone.utc)


def check(condition, message, code=409):
    if not condition:
        raise HTTPException(code, message)


def serialize(db):
    # Shared with the legacy pairing/administration lock during rollout.
    db.execute(text("SELECT pg_advisory_xact_lock(74620146)"))


def get_tournament(db, tid):
    t = db.get(Tournament, tid)
    check(t is not None, "Không tìm thấy giải", 404)
    return t


def get_round(db, tid, rid):
    r = db.get(TournamentRound, rid)
    check(r is not None and r.tournament_id == tid, "Không tìm thấy vòng", 404)
    return r


def audit(db, t, kind, evidence, actor, r=None, e=None, hold=False):
    action = OrganizerAction(tournament_id=t.id, kind=kind, evidence=dump(evidence),
                             actor=actor, round_id=r.id if r else None,
                             encounter_id=e.id if e else None, hold=hold)
    db.add(action)
    return action


PUBLIC_NOTICE_KINDS = ("notice", "rules_update", "hold", "decision", "withdraw", "close_tournament",
                       "disqualify", "forfeit", "cancel", "retry_game")


def notice_view(action):
    return {"id": action.id, "kind": action.kind, "reason": json.loads(action.evidence).get("reason"),
            "round_id": action.round_id, "encounter_id": action.encounter_id, "actor": action.actor,
            "created_at": action.created_at, "appeal_until": action.appeal_until}


def visible_notices(db, t):
    return db.scalars(select(OrganizerAction).where(
        OrganizerAction.tournament_id == t.id, OrganizerAction.kind.in_(PUBLIC_NOTICE_KINDS),
        OrganizerAction.notice_hidden_at.is_(None)).order_by(OrganizerAction.id)).all()


def publish_notice(db, t, message, kind, actor):
    message = message.strip()
    check(len(message) <= 4000, "Thông báo tối đa 4000 ký tự", 400)
    check(kind in ("notice", "rules_update"), "Loại thông báo không hợp lệ", 400)
    if not message:
        return None
    action = audit(db, t, kind, {"reason": message}, actor)
    db.flush()
    return action


def remove_notices(db, t, actor, notice_id=None):
    query = select(OrganizerAction).where(OrganizerAction.tournament_id == t.id,
        OrganizerAction.kind.in_(PUBLIC_NOTICE_KINDS))
    if notice_id is not None:
        query = query.where(OrganizerAction.id == notice_id)
    rows = db.scalars(query.with_for_update()).all()
    check(notice_id is None or bool(rows), "Không tìm thấy thông báo", 404)
    removed = [action for action in rows if action.notice_hidden_at is None]
    now = utcnow()
    for action in removed:
        action.notice_hidden_at = now
    if removed:
        audit(db, t, "remove_notice" if notice_id is not None else "clear_notices",
              {"notice_ids": [action.id for action in removed]}, actor)
    return len(removed)


def held(db, t, r=None, e=None):
    rows = db.scalars(select(OrganizerAction).where(
        OrganizerAction.tournament_id == t.id, OrganizerAction.kind != QUEUE_GUARD,
        ((OrganizerAction.hold.is_(True) & OrganizerAction.resolved_at.is_(None)) |
         (OrganizerAction.appeal_until > utcnow())))).all()
    return any((a.round_id is None or r is None or a.round_id == r.id) and
               (a.encounter_id is None or e is None or a.encounter_id == e.id) for a in rows)


def force_stage(db, t, actor, action, r=None, reason="", encounter_ids=None, before=None):
    """Release applicable holds explicitly; preserve their previous values in audit."""
    check(t.status not in ("complete", "cancelled"), "Giải đã kết thúc")
    now = utcnow()
    released = []
    for entry in db.scalars(select(OrganizerAction).where(OrganizerAction.tournament_id == t.id)):
        if entry.kind in (QUEUE_GUARD, "pause_round"):
            continue
        if r and entry.round_id not in (None, r.id):
            continue
        if encounter_ids is not None and entry.encounter_id is not None and entry.encounter_id not in encounter_ids:
            continue
        if not ((entry.hold and not entry.resolved_at) or (entry.appeal_until and entry.appeal_until > now)):
            continue
        released.append({"id": entry.id, "resolved_at": entry.resolved_at, "appeal_until": entry.appeal_until})
        if entry.hold and not entry.resolved_at:
            entry.resolved_at = now
        if entry.appeal_until and entry.appeal_until > now:
            entry.appeal_until = now
    audit(db, t, "force_stage", {"action": action, "reason": reason, "released_holds": released,
          "encounter_ids": encounter_ids, "before": before or {}}, actor, r)
    db.flush()


def configuration(n, m, q):
    check(type(m) is int and type(q) is int and m > 0 and q > 0,
          "M và Q phải là số nguyên dương", 400)
    check(n >= 2, "Cần ít nhất 2 đội", 400)
    if n == 2:
        check(m >= 2 and q == 2, "Giải 2 đội cần M ít nhất 2 và Q=2; cả hai đội đi tiếp", 400)
        return [2]
    g = (n + m - 1) // m
    sizes = [n // g + (i < n % g) for i in range(g)]
    check(min(sizes) > q and g * q >= 2,
          "Mỗi bảng phải có hơn Q đội; tổng suất đi tiếp ít nhất 2", 400)
    return sizes


def environment():
    # These are the existing sandbox limits. The image must be pinned by digest.
    return {"image": os.getenv("SANDBOX_IMAGE", "oantuti-sandbox:dev"),
            "protocol_version": 2, "bot_entrypoint": "functions-v1", "python": os.getenv("SANDBOX_PYTHON_VERSION", "3.11"),
            "cpp": os.getenv("SANDBOX_CPP_VERSION", "g++-12"),
            "memory_mb": 256, "cpus": 1, "pids": 32, "tmp_mb": 32,
            "move_timeout": 1.5, "first_timeout": 15, "max_moves": 200,
            "compile_timeout": 35, "compiler_timeout": 25}


def create(db, data, actor):
    check(not db.scalar(select(Tournament.id).where(
        Tournament.status.not_in(["complete", "cancelled"]))), "Một giải khác đang hoạt động")
    start, end = aware(data["registration_open"]), aware(data["registration_close"])
    check(start < end, "Hạn đăng ký phải sau giờ mở", 400)
    check(data["max_group_size"] > data["qualifiers"] > 0 or data["max_group_size"] == data["qualifiers"] == 2,
          "M phải lớn hơn Q > 0; giải 2 đội cho phép M=Q=2", 400)
    rules = (Path(__file__).resolve().parents[1] / "GAMERULE.md").read_text()
    env = environment()
    check(re.fullmatch(r"(?:.+@)?sha256:[0-9a-f]{64}", env["image"]),
          "Đặt SANDBOX_IMAGE bằng image@sha256:digest hoặc sha256:image-id trước khi mở giải", 400)
    t = Tournament(name=data.get("name", "OTTv2 Tournament"), max_group_size=data["max_group_size"], qualifiers=data["qualifiers"],
                   registration_open=start, registration_close=end,
                   appeal_contact=data.get("appeal_contact", ""), incident_policy=data.get("incident_policy", ""),
                   auto_run=data.get("auto_run",True),auto_submission_minutes=data.get("auto_submission_minutes",1440),
                   rules_digest=hashlib.sha256(rules.encode()).hexdigest(), rules_text=rules,
                   environment=dump(env))
    db.add(t)
    db.flush()
    audit(db, t, "create", data, actor)
    audit(db, t, QUEUE_GUARD, {}, actor, hold=True)
    return t


def close_tournament(db, t, actor, reason=""):
    reason = reason.strip()
    check(len(reason) <= 2000, "Lý do đóng giải tối đa 2000 ký tự", 400)
    if t.status in ("complete", "cancelled"):
        return
    rounds = db.scalars(select(TournamentRound).where(TournamentRound.tournament_id == t.id)).all()
    round_ids = [r.id for r in rounds]
    encounters = db.scalars(select(Encounter).where(Encounter.round_id.in_(round_ids))).all()
    matches = db.scalars(select(Match).where(Match.encounter_id.in_([e.id for e in encounters]))).all()
    check(not any(m.status == "running" for m in matches), "Đợi ván đang chạy hoàn tất rồi đóng giải")
    match_ids = {m.id for m in matches}
    submission_ids = set(db.scalars(select(RoundSubmission.submission_id).where(RoundSubmission.round_id.in_(round_ids))))
    job_ids = []
    for job in db.scalars(select(Job).where(Job.status.in_(ACTIVE_JOBS))):
        payload = json.loads(job.payload)
        if ((job.kind == "match" and payload.get("match_id") in match_ids) or
                (job.kind == "compile" and payload.get("submission_id") in submission_ids)):
            check(job.status not in RUNNING_JOBS, "Đợi worker hoàn tất công việc đang chạy rồi đóng giải")
            job_ids.append(job.id)
    # Claims do not use the organizer lock. Lock queued jobs without waiting:
    # a worker may have claimed one since the read above, or be saving results.
    try:
        jobs = db.scalars(select(Job).where(Job.id.in_(job_ids)).order_by(Job.id)
                          .with_for_update(nowait=True).execution_options(populate_existing=True)).all()
    except OperationalError as error:
        if getattr(error.orig, "sqlstate", None) == "55P03":
            raise HTTPException(409, "Worker đang nhận hoặc lưu công việc; thử đóng giải lại sau") from error
        raise
    check(not any(job.status in RUNNING_JOBS for job in jobs), "Đợi worker hoàn tất công việc đang chạy rồi đóng giải")
    cancelled_ids = []
    for job in jobs:
        if job.status in QUEUED_JOBS:
            job.status = "cancelled"
            cancelled_ids.append(job.id)
    for submission in db.scalars(select(Submission).where(Submission.id.in_(submission_ids),
                                                         Submission.status.in_(["queued", "running"]))):
        submission.status = "cancelled"
    for match in matches:
        if match.status not in ("done", "void"):
            match.status = "void"
    for encounter in encounters:
        if encounter.status not in ("finalized", "provisional", "cancelled"):
            encounter.status, encounter.reason = "cancelled", reason
    for r in rounds:
        if r.status != "finalized":
            r.status = "cancelled"
    before = t.status
    t.status, t.result, t.champion_id = "cancelled", "closed_by_organizer", None
    audit(db, t, "close_tournament", {"reason": reason, "previous_status": before,
                                    "cancelled_job_ids": cancelled_ids}, actor)


def join(db, t, pid, now=None):
    existing = enrollment(db, t, pid)
    if existing:
        return existing
    now = now or utcnow()
    check(t.status == "registration" and t.registration_open <= now <= t.registration_close,
          "Đăng ký giải chưa mở hoặc đã khóa")
    e = Enrollment(tournament_id=t.id, player_id=pid, joined_at=now)
    db.add(e)
    return e


def enrollment(db, t, pid):
    return db.scalar(select(Enrollment).where(Enrollment.tournament_id == t.id, Enrollment.player_id == pid))


def draw(db, t, key, choices, reason, actor):
    existing = db.scalar(select(RecordedDraw).where(
        RecordedDraw.tournament_id == t.id, RecordedDraw.key == key))
    if existing:
        check(json.loads(existing.choices) == list(choices), "Tập bốc thăm đã khóa khác dữ liệu hiện tại")
        return json.loads(existing.result)
    seed = secrets.token_hex(32)
    result = list(choices)
    random.Random(int(seed, 16)).shuffle(result)
    db.add(RecordedDraw(tournament_id=t.id, key=key, choices=dump(list(choices)),
                        reason=reason, seed=seed, result=dump(result), actor=actor))
    db.flush()
    return result


def ratings(db):
    result = {}
    for event in db.scalars(select(EloEvent).where(EloEvent.active.is_(True)).order_by(EloEvent.id)):
        result[event.player_a], result[event.player_b] = float(event.after_a), float(event.after_b)
    return result


def tournament_leaderboard(db, t):
    """Replay this tournament's active encounter events from 1000, without changing global Elo."""
    players=db.scalars(select(Player).join(Enrollment,Enrollment.player_id==Player.id)
                       .where(Enrollment.tournament_id==t.id).order_by(Player.id)).all()
    values={p.id:1000. for p in players}
    counts={p.id:0 for p in players}
    wins={p.id:0 for p in players}
    events=db.scalars(select(EloEvent).join(Encounter,EloEvent.encounter_id==Encounter.id)
                      .join(TournamentRound,Encounter.round_id==TournamentRound.id)
                      .where(TournamentRound.tournament_id==t.id,EloEvent.active.is_(True))
                      .order_by(EloEvent.id))
    for event in events:
        a,b=values.get(event.player_a,1000.),values.get(event.player_b,1000.)
        delta=32*(event.score-1/(1+10**((b-a)/400)))
        values[event.player_a],values[event.player_b]=a+delta,b-delta
        for pid,score in ((event.player_a,event.score),(event.player_b,1-event.score)):
            counts[pid]=counts.get(pid,0)+1
            wins[pid]=wins.get(pid,0)+int(score==1)
    return sorted([{"player_id":p.id,"name":p.name,"points":values[p.id],"elo":values[p.id],
                    "matches":counts[p.id],"wins":wins[p.id]} for p in players],
                  key=lambda row:(-row["elo"],row["player_id"]))


def recalculate_elo(db):
    db.flush()
    values = {}
    for event in db.scalars(select(EloEvent).order_by(EloEvent.id)):
        if not event.active:
            continue
        a, b = values.get(event.player_a, 1000.), values.get(event.player_b, 1000.)
        delta = 32 * (event.score - 1 / (1 + 10 ** ((b - a) / 400)))
        event.before_a, event.before_b = a, b
        event.after_a, event.after_b = a + delta, b - delta
        values[event.player_a], values[event.player_b] = a + delta, b - delta
    return values


def rate(db, e):
    event = db.scalar(select(EloEvent).where(EloEvent.encounter_id == e.id))
    eligible = not e.administrative and e.playing_result is not None
    if event:
        event.active = eligible
        if eligible:
            event.score = e.playing_result
    elif eligible:
        db.add(EloEvent(encounter_id=e.id, player_a=e.player_a, player_b=e.player_b, score=e.playing_result))
    recalculate_elo(db)


def lock_roster(db, t, actor, *, force=False, force_reason=""):
    check(t.status == "registration", "Danh sách đăng ký đã khóa")
    if force:
        force_stage(db, t, actor, "lock-roster", reason=force_reason)
    check(not held(db, t), "Còn quyết định hoặc khiếu nại chưa giải quyết")
    entries = db.scalars(select(Enrollment).where(
        Enrollment.tournament_id == t.id, Enrollment.status == "enrolled").order_by(Enrollment.player_id)).all()
    sizes = configuration(len(entries), t.max_group_size, t.qualifiers)
    elo = ratings(db)
    by_elo = defaultdict(list)
    for e in entries:
        e.frozen_elo = elo.get(e.player_id, 1000.)
        by_elo[e.frozen_elo].append(e.player_id)
    ordered = []
    for value in sorted(by_elo, reverse=True):
        ids = sorted(by_elo[value])
        ordered.extend(draw(db, t, f"elo-tie:{value}", ids, "Equal registration Elo", actor) if len(ids) > 1 else ids)
    groups = [[] for _ in sizes]
    g = len(sizes)
    for pot, start in enumerate(range(0, len(ordered), g)):
        available = [i for i, size in enumerate(sizes) if len(groups[i]) < size]
        order = draw(db, t, f"allocation-pot:{pot}", available, "Seed pot group allocation", actor)
        for pid, index in zip(ordered[start:start + g], order):
            groups[index].append(pid)
    t.allocation = dump({"algorithm": "seeded-pots-v1", "input": ordered, "sizes": sizes, "groups": groups})
    t.status = "allocated"
    audit(db, t, "lock_roster", json.loads(t.allocation), actor)


def confirm_groups(db, t, actor, *, force=False, force_reason=""):
    check(t.status == "allocated", "Cần khóa danh sách và chia bảng trước")
    if force:
        force_stage(db, t, actor, "confirm-groups", reason=force_reason)
    for number, members in enumerate(json.loads(t.allocation)["groups"], 1):
        group = TournamentGroup(tournament_id=t.id, number=number)
        db.add(group)
        db.flush()
        for pid in members:
            db.add(GroupMember(tournament_id=t.id, group_id=group.id, player_id=pid))
    db.add(TournamentRound(tournament_id=t.id, level=0, rules_digest=t.rules_digest, environment=t.environment))
    t.status = "groups"
    audit(db, t, "confirm_groups", {}, actor)


def participants(db, t, r):
    if r.level == 0:
        return db.scalars(select(GroupMember.player_id).where(GroupMember.tournament_id == t.id).order_by(GroupMember.player_id)).all()
    nodes = json.loads(t.bracket)["levels"][str(r.level)]
    return sorted({pid for node in nodes for pid in (node["a"], node["b"]) if pid is not None})


def open_window(db, t, r, start, end, actor, *, force=False, force_reason=""):
    check(t.status not in ("complete", "cancelled"), "Giải đã kết thúc")
    check(r.status == "planned" or (force and r.status == "submission"), "Cửa sổ đã mở hoặc bot đã khóa")
    start, end = aware(start), aware(end)
    check(start < end and end > utcnow(), "Cửa sổ nộp không hợp lệ", 400)
    if force:
        force_stage(db, t, actor, "open-window", r, force_reason,
                    before={"opens_at": r.opens_at, "closes_at": r.closes_at, "status": r.status})
        start = min(start, utcnow())
    check(not held(db, t, r), "Có khiếu nại hoặc hoãn lịch chưa giải quyết")
    r.opens_at, r.closes_at, r.status = start, end, "submission"
    audit(db, t, "open_window", {"opens_at": start.isoformat(), "closes_at": end.isoformat()}, actor, r)


def submit(db, t, r, pid, language, source, request_key, now=None):
    lang = language.lower()
    check(lang in ("python", "cpp"), "language phải là cpp hoặc python", 400)
    try:
        size = len(source.encode("utf-8"))
    except UnicodeEncodeError:
        raise HTTPException(400, "Mã nguồn phải là UTF-8")
    check(0 < size <= 100000 and "\x00" not in source, "Mã nguồn phải là UTF-8, tối đa 100000 byte", 400)
    check(request_key and len(request_key) <= 128, "Cần Idempotency-Key (tối đa 128 ký tự)", 400)
    old = db.scalar(select(RoundSubmission).where(RoundSubmission.round_id == r.id,
        RoundSubmission.player_id == pid, RoundSubmission.request_key == request_key))
    if old:
        s = db.get(Submission, old.submission_id)
        check(s.language == lang and s.source == source, "Idempotency-Key đã dùng cho nội dung khác")
        return old
    now = now or utcnow()
    entry = enrollment(db, t, pid)
    check(t.status not in ("complete", "cancelled") and entry and entry.status == "enrolled" and
          pid in participants(db, t, r), "Đội không đủ điều kiện trong vòng này", 403)
    check(r.status == "submission" and r.opens_at <= now <= r.closes_at, "Ngoài cửa sổ nộp bot")
    used = db.scalar(select(func.count()).select_from(RoundSubmission).where(
        RoundSubmission.round_id == r.id, RoundSubmission.player_id == pid))
    check(used < 3, "Mỗi đội tối đa 3 lần nộp mỗi vòng", 429)
    s = Submission(player_id=pid, language=lang, source=source, created_at=now)
    db.add(s)
    db.flush()
    entry = RoundSubmission(round_id=r.id, player_id=pid, submission_id=s.id, attempt=used + 1, request_key=request_key)
    db.add(entry)
    db.add(round_job(r, "compile", {"submission_id": s.id, "round_id": r.id}))
    return entry


def round_robin(ids):
    """Circle schedule, with an actual rest for odd groups."""
    ring = list(ids)
    if len(ring) % 2:
        ring.append(None)
    for batch in range(1, len(ring)):
        for a, b in zip(ring[:len(ring)//2], reversed(ring[len(ring)//2:])):
            if a is not None and b is not None:
                yield batch, a, b
        ring = [ring[0], ring[-1], *ring[1:-1]]


def bot(db, rid, pid):
    return db.scalar(select(LockedBot).where(LockedBot.round_id == rid, LockedBot.player_id == pid)) if pid else None


def eligible(db, t, r, pid):
    e = enrollment(db, t, pid) if pid else None
    b = bot(db, r.id, pid)
    return bool(e and e.status == "enrolled" and b and b.submission_id)


def publish(e, r, now=None):
    e.status = "provisional"
    e.published_at = now or utcnow()
    e.appeal_until = e.published_at + timedelta(minutes=30) if r.level == 0 else None


def administrative(db, t, r, e, a_ok, b_ok, reason):
    e.administrative = True
    e.winner_id = e.player_a if a_ok and not b_ok else e.player_b if b_ok and not a_ok else None
    e.playing_result = None
    e.points_a, e.points_b = (3 if a_ok and not b_ok else 0), (3 if b_ok and not a_ok else 0)
    e.game_points_a, e.game_points_b = (2 if a_ok and not b_ok else 0), (2 if b_ok and not a_ok else 0)
    if r.level:
        e.points_a = e.points_b = e.game_points_a = e.game_points_b = 0
    e.extra_scores, e.reason = "[]", reason
    for m in games(db, e):
        check(m.status != "running", "Đợi ván đang chạy kết thúc trước khi áp dụng quyết định")
        if m.status in ("scheduled", "queued", "failed"):
            m.status = "void"
            for j in db.scalars(select(Job).where(Job.kind == "match", Job.status.in_(ACTIVE_JOBS)).with_for_update()):
                if json.loads(j.payload).get("match_id") == m.id:
                    check(j.status not in RUNNING_JOBS, "Worker đang giữ ván; đợi worker trả kết quả")
                    j.status = "cancelled"
    publish(e, r)
    rate(db, e)


def create_encounter(db, t, r, a, b, key, actor, group=None, batch=1, playoff=None, slot=0):
    e = Encounter(round_id=r.id, player_a=a, player_b=b, key=key, group_id=group,
                  batch=batch, playoff=playoff, slot=slot)
    db.add(e)
    db.flush()
    if not r.level and any(enrollment(db, t, pid).status != "enrolled" for pid in (a, b)):
        e.excluded, e.administrative, e.status, e.reason = True, True, "cancelled", "withdrawn_or_disqualified_before_group_lock"
        return e
    if not eligible(db, t, r, a) or not eligible(db, t, r, b):
        administrative(db, t, r, e, eligible(db, t, r, a), eligible(db, t, r, b), "missing_bot_or_ineligible")
    else:
        e.first_south = (draw(db, t, f"south:{e.id}", [a, b], "Knockout first South", actor)[0]
                         if r.level else a)
        add_games(db, r, e, 1, 6 if r.level else 2)
    return e


def lock_bots(db, t, r, actor, now=None, *, force=False, force_reason=""):
    now = now or utcnow()
    check(r.status == "submission" and (force or now > r.closes_at), "Cần đóng cửa sổ trước khi khóa bot")
    entries = db.scalars(select(RoundSubmission).where(RoundSubmission.round_id == r.id)).all()
    check(not any(db.get(Submission, e.submission_id).status in ("queued", "running", "infrastructure_error") for e in entries),
          "Còn bài đúng hạn đang xử lý; chờ xử lý xong trước khi khóa")
    if force:
        force_stage(db, t, actor, "lock-bots", r, force_reason,
                    before={"opens_at": r.opens_at, "closes_at": r.closes_at})
        r.closes_at = min(r.closes_at, now - timedelta(microseconds=1))
        r.opens_at = min(r.opens_at, r.closes_at - timedelta(microseconds=1))
    check(not held(db, t, r), "Có khiếu nại hoặc hoãn lịch chưa giải quyết")
    previous = db.scalar(select(TournamentRound).where(TournamentRound.tournament_id == t.id,
        TournamentRound.level < r.level).order_by(TournamentRound.level.desc()).limit(1))
    for pid in participants(db, t, r):
        chosen = sorted((db.get(Submission, e.submission_id) for e in entries if e.player_id == pid),
                        key=lambda s: (s.created_at, s.id), reverse=True)
        ready = next((s for s in chosen if s.status == "ready"), None)
        prior = bot(db, previous.id, pid) if previous else None
        inherited = bool(not ready and prior and prior.submission_id and
                         previous.rules_digest == r.rules_digest and previous.environment == r.environment)
        sid = ready.id if ready else prior.submission_id if inherited else None
        db.add(LockedBot(round_id=r.id, player_id=pid, submission_id=sid,
                         reason="latest_ready" if ready else "inherited_compatible" if inherited else "missing_compatible_tournament_bot"))
    r.status = "locked"
    db.flush()
    if r.level == 0:
        for group in db.scalars(select(TournamentGroup).where(TournamentGroup.tournament_id == t.id).order_by(TournamentGroup.number)):
            ids = db.scalars(select(GroupMember.player_id).where(GroupMember.group_id == group.id).order_by(GroupMember.id)).all()
            for batch, a, b in round_robin(ids):
                create_encounter(db, t, r, a, b, f"group:{group.id}:{min(a,b)}:{max(a,b)}", actor,
                                 group=group.id, batch=batch)
    else:
        bracket = json.loads(t.bracket)
        for node in bracket["levels"][str(r.level)]:
            a, b = node["a"], node["b"]
            if a and b:
                e = create_encounter(db, t, r, a, b, f"slot:{node['slot']}", actor, slot=node["slot"])
                node["encounter_id"] = e.id
            else:
                pid = a or b
                node["winner"] = pid if eligible(db, t, r, pid) else None
                node["bye"] = True
        t.bracket = dump(bracket)
    audit(db, t, "lock_bots", {}, actor, r)


def games(db, e):
    return db.scalars(select(Match).where(Match.encounter_id == e.id).order_by(Match.game_number)).all()


def add_games(db, r, e, start, count):
    # Main games alternate. First extra pair reverses main game's first side.
    for n in range(start, start + count):
        south = e.first_south if (n <= 6 and n % 2 == 1) or n in (8, 9) else (
            e.player_b if e.first_south == e.player_a else e.player_a)
        north = e.player_b if south == e.player_a else e.player_a
        sa, sb = bot(db, r.id, south), bot(db, r.id, north)
        db.add(Match(encounter_id=e.id, game_number=n, player_a=south, player_b=north,
                     submission_a=sa.submission_id, submission_b=sb.submission_id,
                     event_day=utcnow().astimezone(timezone(timedelta(hours=7))).date().isoformat(),
                     kind="tournament", status="scheduled"))


def start_batch(db, t, r, batch, actor, *, force=False, force_reason=""):
    check(r.status in ("locked", "running"), "Bot chưa khóa hoặc vòng đã công bố")
    if batch is None:
        batch = db.scalar(select(func.min(Encounter.batch)).where(
            Encounter.round_id == r.id, Encounter.status == "scheduled"))
        check(batch is not None, "Không còn lượt chưa chạy trong vòng này")
    selected = db.scalars(select(Encounter).where(Encounter.round_id == r.id, Encounter.batch == batch)).all()
    check(selected, "Không tìm thấy lượt thi", 404)
    if force:
        force_stage(db, t, actor, "start-batch", r, force_reason,
                    before={"batch": batch, "encounter_ids": [e.id for e in selected]})
    check(not held(db, t, r), "Có khiếu nại hoặc hoãn lịch chưa giải quyết")
    for e in selected:
        if e.status == "scheduled":
            e.status = "pending"
    r.status = "running"
    audit(db, t, "start_batch", {"batch": batch}, actor, r)
    pump(db)
    return batch


def pump(db):
    """Queue only one game per encounter, and no overlapping rated teams.

    Completed encounter Elo/appeals are finalized before another encounter for
    the same team starts. This also makes recovery safe across many workers.
    """
    active = db.scalars(select(Encounter).join(TournamentRound, Encounter.round_id == TournamentRound.id)
        .join(Tournament, TournamentRound.tournament_id == Tournament.id)
        .where(Encounter.status.in_(["running", "paused", "provisional"]),
               Tournament.status.not_in(["complete", "cancelled"])).order_by(Encounter.id)).all()
    busy = {pid for e in active for pid in (e.player_a, e.player_b) if pid}
    for e in db.scalars(select(Encounter).where(Encounter.status == "pending").order_by(Encounter.id)):
        r = db.get(TournamentRound, e.round_id)
        t = db.get(Tournament, r.tournament_id)
        if held(db, t, r, e) or t.status in ("cancelled", "complete"):
            continue
        if e.player_a in busy or e.player_b in busy:
            continue
        # Preserve running legacy games during upgrade; do not overlap them.
        if db.scalar(select(Match.id).where(Match.encounter_id.is_(None), Match.kind != "practice", Match.status == "running",
            (Match.player_a.in_([e.player_a, e.player_b])) | (Match.player_b.in_([e.player_a, e.player_b])))):
            continue
        e.status = "running"
        active.append(e)
        busy.update((e.player_a, e.player_b))
    for e in active:
        if e.status != "running":
            continue
        r = db.get(TournamentRound, e.round_id)
        t = db.get(Tournament, r.tournament_id)
        if held(db, t, r, e) or t.status in ("cancelled", "complete"):
            continue
        rows = games(db, e)
        next_game = next((m for m in rows if m.status != "done"), None)
        if next_game and next_game.status == "scheduled":
            next_game.status = "queued"
            db.add(round_job(r, "match", {"match_id": next_game.id}))


def aggregate(db, e, actor="worker"):
    if e.administrative or e.status in ("provisional", "finalized", "cancelled"):
        return
    rows = games(db, e)
    if any(m.status == "failed" for m in rows):
        e.status, e.reason = "paused", "infrastructure_error; organizer retry required"
        return
    if not rows or any(m.status != "done" for m in rows):
        return
    r = db.get(TournamentRound, e.round_id)
    t = db.get(Tournament, r.tournament_id)
    def score(subset):
        a = sum(m.points_a if m.player_a == e.player_a else m.points_b for m in subset)
        b = sum(m.points_b if m.player_a == e.player_a else m.points_a for m in subset)
        return a, b
    a, b = score(rows[:6] if r.level else rows)
    e.game_points_a, e.game_points_b = a, b
    decisive_a, decisive_b = a, b
    extra = []
    if r.level and a == b:
        for start in (6, 8):
            if len(rows) <= start:
                add_games(db, r, e, start + 1, 2)
                return
            x, y = score(rows[start:start + 2])
            extra.append([x, y])
            decisive_a, decisive_b = x, y
            if x != y:
                break
    e.extra_scores = dump(extra)
    e.playing_result = 1. if decisive_a > decisive_b else 0. if decisive_a < decisive_b else .5
    e.winner_id = e.player_a if e.playing_result == 1 else e.player_b if e.playing_result == 0 else None
    if r.level and e.winner_id is None:
        e.winner_id = draw(db, t, f"advancement:{e.id}", [e.player_a, e.player_b], "Two drawn knockout extra pairs", actor)[0]
        e.reason = "lottery_advancement; playing_draw"
    else:
        e.reason = "played"
    if not r.level:
        e.points_a, e.points_b = (3, 0) if a > b else (0, 3) if a < b else (1, 1)
    publish(e, r)
    rate(db, e)


def after_game(db, match):
    if match.encounter_id:
        e = db.get(Encounter, match.encounter_id)
        aggregate(db, e)
        db.flush()
        pump(db)


def finalize_encounters(db, t, r, actor, eid=None, now=None, *, force=False, force_reason=""):
    now = now or utcnow()
    rows = db.scalars(select(Encounter).where(Encounter.round_id == r.id).order_by(Encounter.id)).all()
    selected = [e for e in rows if e.id == eid] if eid else [e for e in rows if e.status == "provisional"]
    check(selected, "Không có kết quả tạm để chốt")
    check(all(e.status == "provisional" for e in selected), "Chỉ chốt trận đã có đủ kết quả")
    if force:
        force_stage(db, t, actor, "finalize-encounters", r, force_reason,
                    encounter_ids=[e.id for e in selected],
                    before={"appeal_until": {e.id: e.appeal_until for e in selected}})
    for e in selected:
        check(r.level > 0 or force or e.appeal_until <= now, "Chưa hết hạn khiếu nại 30 phút")
        check(not held(db, t, r, e), "Khiếu nại đang được xử lý")
        e.status = "finalized"
    audit(db, t, "finalize_encounters", {"encounters": [e.id for e in selected]}, actor, r)
    db.flush()
    pump(db)


def stats(ids, encounters):
    rows = {pid: {"player_id": pid, "points": 0, "game_points": 0, "wins": 0, "encounters": 0} for pid in ids}
    for e in encounters:
        if e.excluded or e.status not in ("provisional", "finalized"):
            continue
        for pid, pts, gpts in ((e.player_a, e.points_a, e.game_points_a), (e.player_b, e.points_b, e.game_points_b)):
            if pid in rows:
                row = rows[pid]
                row["points"] += pts
                row["game_points"] += gpts
                row["wins"] += int(e.winner_id == pid)
                row["encounters"] += 1
    return rows


def ranking_blocks(ids, encounters):
    """Five criteria; head-to-head set stays the original points-tied set."""
    rows = stats(ids, encounters)
    ties = defaultdict(list)
    for pid in ids:
        ties[rows[pid]["points"]].append(pid)
    keys = {}
    for same_points in ties.values():
        head = stats(same_points, [e for e in encounters if e.player_a in same_points and e.player_b in same_points])
        for pid in same_points:
            row = rows[pid]
            keys[pid] = (row["points"], head[pid]["points"], head[pid]["game_points"], row["game_points"], row["wins"])
    ordered = sorted(ids, key=lambda pid: keys[pid], reverse=True)
    blocks = [list(block) for _, block in itertools.groupby(ordered, key=lambda pid: keys[pid])]
    return rows, blocks


def tie_key(prefix, ids):
    # Keep DB keys bounded even for large valid groups; full choices are
    # retained in RecordedDraw and the allocation/qualification snapshots.
    return prefix + ":" + hashlib.sha256(dump(sorted(ids)).encode()).hexdigest()[:32]


def resolve_group(db, t, r, group, actor):
    ids = db.scalars(select(GroupMember.player_id).where(GroupMember.group_id == group.id).order_by(GroupMember.player_id)).all()
    live = [pid for pid in ids if enrollment(db, t, pid).status == "enrolled"]
    good = [pid for pid in live if eligible(db, t, r, pid)]
    missing = [pid for pid in live if pid not in good]
    encounters = db.scalars(select(Encounter).where(Encounter.group_id == group.id).order_by(Encounter.id)).all()
    main = [e for e in encounters if e.playoff is None]
    check(all(e.status in ("finalized", "cancelled") for e in main), "Cần chốt tất cả kết quả vòng bảng")
    rows, blocks = ranking_blocks(good, main)
    ordered = []
    waiting = False
    for block in blocks:
        if len(block) == 1:
            ordered += block
            continue
        key = tie_key(f"playoff:{group.id}", block)
        if len(ordered) < t.qualifiers:
            playoffs = [e for e in encounters if e.playoff == key]
            if not playoffs:
                base = max([e.batch for e in encounters] + [0])
                for batch, a, b in round_robin(block):
                    create_encounter(db, t, r, a, b, f"{key}:{min(a,b)}:{max(a,b)}", actor,
                                     group=group.id, batch=base + batch, playoff=key)
                waiting = True
                ordered += block
                continue
            if any(e.status != "finalized" for e in playoffs):
                waiting = True
                ordered += block
                continue
            _, extra_blocks = ranking_blocks(block, playoffs)
            for extra in extra_blocks:
                ordered += (draw(db, t, tie_key(f"playoff-final:{group.id}", extra), sorted(extra),
                                 "Final playoff ranking tie", actor) if len(extra) > 1 else extra)
        else:
            ordered += draw(db, t, tie_key(f"below-Q:{group.id}", block), sorted(block), "Non-qualifying ranking tie", actor)
    if len(missing) > 1:
        missing = draw(db, t, tie_key(f"missing:{group.id}", missing), sorted(missing), "Missing bot display order", actor)
    return ordered + missing, rows, waiting


def qualify(db, t, r, actor, *, force=False, force_reason=""):
    check(r.level == 0 and r.status in ("locked", "running"), "Chỉ phân hạng sau vòng bảng")
    if force:
        force_stage(db, t, actor, "qualify", r, force_reason)
        if db.scalar(select(Encounter.id).where(Encounter.round_id == r.id, Encounter.status == "provisional").limit(1)):
            finalize_encounters(db, t, r, actor, force=True, force_reason=force_reason)
    check(not held(db, t, r), "Còn khiếu nại hoặc quyết định chưa xử lý")
    order, waiting = {}, False
    for group in db.scalars(select(TournamentGroup).where(TournamentGroup.tournament_id == t.id).order_by(TournamentGroup.number)):
        ids, rows, pending = resolve_group(db, t, r, group, actor)
        order[str(group.id)] = {"order": ids, "stats": rows}
        waiting |= pending
    if waiting:
        audit(db, t, "create_playoffs", {}, actor, r)
        return {"playoffs_required": True}
    r.qualification = dump(order)
    r.status = "provisional"
    r.published_at, r.appeal_until = utcnow(), utcnow() + timedelta(minutes=30)
    audit(db, t, "publish_qualification", order, actor, r)
    return {"playoffs_required": False, "appeal_until": r.appeal_until}


def seed_order(size):
    positions = [1, 2]
    while len(positions) < size:
        total = len(positions) * 2
        positions = [value for s in positions for value in (s, total + 1 - s)]
    return positions


def seed_qualifiers(db, t, r, actor):
    seeds = []
    saved = json.loads(r.qualification)
    for group in db.scalars(select(TournamentGroup).where(TournamentGroup.tournament_id == t.id)):
        ids = json.loads(group.final_order)
        good = [pid for pid in ids if eligible(db, t, r, pid)][:t.qualifiers]
        for pid in good:
            place = ids.index(pid) + 1
            row = saved[str(group.id)]["stats"].get(str(pid), {"encounters": 0, "points": 0, "game_points": 0, "wins": 0})
            count = row["encounters"]
            key = (place, -Fraction(str(row["points"])) / count if count else Fraction(0),
                   -Fraction(str(row["game_points"])) / (2 * count) if count else Fraction(0),
                   -Fraction(row["wins"], count) if count else Fraction(0))
            seeds.append((key, pid))
    seeds.sort(key=lambda pair: pair[0])
    result = []
    for _, pairs in itertools.groupby(seeds, key=lambda pair: pair[0]):
        ids = sorted(pid for key, pid in pairs)
        result += draw(db, t, tie_key("knockout-seeding", ids), ids, "Equal knockout seeding ratios", actor) if len(ids) > 1 else ids
    return result


def confirm_bracket(db, t, actor, *, force=False, force_reason=""):
    check(t.status == "qualified", "Cần chốt vòng bảng trước khi xác nhận nhánh")
    if force:
        force_stage(db, t, actor, "confirm-bracket", reason=force_reason)
    check(not held(db, t), "Còn quyết định hoặc khiếu nại chưa giải quyết")
    r = db.scalar(select(TournamentRound).where(TournamentRound.tournament_id == t.id, TournamentRound.level == 0))
    seeds = seed_qualifiers(db, t, r, actor)
    if len(seeds) < 2:
        t.status, t.champion_id = "complete", seeds[0] if seeds else None
        t.result = "sole_eligible_qualifier" if seeds else "no_champion"
    else:
        size = 1 << (len(seeds) - 1).bit_length()
        slots = [seeds[s - 1] if s <= len(seeds) else None for s in seed_order(size)]
        nodes = [{"slot": i // 2, "a": slots[i], "b": slots[i+1], "winner": None,
                  "bye": False, "encounter_id": None} for i in range(0, size, 2)]
        t.bracket = dump({"seeds": seeds, "size": size, "levels": {"1": nodes}})
        t.status = "knockout"
        db.add(TournamentRound(tournament_id=t.id, level=1, rules_digest=t.rules_digest,
                               environment=t.environment, worker_id=r.worker_id))
    audit(db, t, "confirm_bracket", {"seeds": seeds}, actor)


def finalize_round(db, t, r, actor, now=None, *, force=False, force_reason=""):
    now = now or utcnow()
    check(r.status != "finalized", "Vòng đã chốt")
    if force:
        force_stage(db, t, actor, "finalize", r, force_reason, before={"appeal_until": r.appeal_until})
        if db.scalar(select(Encounter.id).where(Encounter.round_id == r.id, Encounter.status == "provisional").limit(1)):
            finalize_encounters(db, t, r, actor, force=True, force_reason=force_reason)
    check(not held(db, t, r), "Khiếu nại đang được xử lý")
    encounters = db.scalars(select(Encounter).where(Encounter.round_id == r.id)).all()
    check(all(e.status in ("finalized", "cancelled") for e in encounters), "Còn trận chưa chốt")
    if not r.level:
        check(r.status == "provisional" and r.qualification and (force or r.appeal_until <= now), "Cần công bố phân hạng và chờ 30 phút")
        for gid, data in json.loads(r.qualification).items():
            db.get(TournamentGroup, int(gid)).final_order = dump(data["order"])
        t.status = "qualified"
    else:
        check(r.status in ("locked", "running"), "Chưa khóa bot vòng này")
        bracket = json.loads(t.bracket)
        nodes = bracket["levels"][str(r.level)]
        for node in nodes:
            if node["encounter_id"]:
                node["winner"] = db.get(Encounter, node["encounter_id"]).winner_id
            if node["winner"] and enrollment(db, t, node["winner"]).status != "enrolled":
                node["winner"] = None
        if len(nodes) == 1:
            t.status, t.champion_id = "complete", nodes[0]["winner"]
            t.result = "champion" if t.champion_id else "no_champion"
        else:
            winners = [node["winner"] for node in nodes]
            bracket["levels"][str(r.level + 1)] = [
                {"slot": i//2, "a": winners[i], "b": winners[i+1], "winner": None,
                 "bye": False, "encounter_id": None} for i in range(0, len(winners), 2)]
            db.add(TournamentRound(tournament_id=t.id, level=r.level + 1, rules_digest=r.rules_digest,
                                   environment=r.environment, worker_id=r.worker_id))
        t.bracket = dump(bracket)
    r.status = "finalized"
    audit(db, t, "finalize_round", {}, actor, r)


def retry_game(db, t, r, match_id, reason, actor):
    m = db.get(Match, match_id)
    e = db.get(Encounter, m.encounter_id) if m and m.encounter_id else None
    check(e and e.round_id == r.id, "Không tìm thấy ván trong vòng", 404)
    check(e.status == "paused" and m.status == "failed", "Chỉ retry ván lỗi hạ tầng; giữ nguyên ván hợp lệ")
    check(not held(db, t, r, e), "Cần xử lý quyết định hoãn trước")
    check(r.status != "finalized" and t.status not in ("complete", "cancelled"), "Vòng hoặc giải đã kết thúc")
    m.status, m.worker_id = "scheduled", None
    e.status, e.reason = "running", reason
    audit(db, t, "retry_game", {"match_id": m.id, "reason": reason}, actor, r, e)
    db.flush()
    pump(db)


def exception(db, t, r, kind, data, actor):
    reason = data.get("reason", "").strip()
    check(t.status not in ("complete", "cancelled"), "Giải đã kết thúc")
    e = db.get(Encounter, data.get("encounter_id")) if data.get("encounter_id") else None
    check(not e or e.round_id == r.id, "Trận không thuộc vòng", 404)
    if kind in ("hold", "decision"):
        if kind == "decision":
            prior = db.get(OrganizerAction, data.get("action_id"))
            check(prior and prior.tournament_id == t.id and prior.kind not in (QUEUE_GUARD, "pause_round") and prior.hold and not prior.resolved_at, "Không tìm thấy khiếu nại đang mở", 404)
            prior.resolved_at = utcnow()
        action = audit(db, t, kind, data, actor, r, e, hold=kind == "hold")
        db.flush()
        return {"action_id": action.id}
    if kind == "retry":
        retry_game(db, t, r, data.get("match_id"), reason, actor)
    elif kind == "compile_retry":
        s = db.get(Submission, data.get("submission_id"))
        link = db.scalar(select(RoundSubmission).where(RoundSubmission.round_id == r.id,
                                                       RoundSubmission.submission_id == data.get("submission_id")))
        check(link and s and s.status == "infrastructure_error" and r.status == "submission", "Không có lỗi hạ tầng biên dịch để retry")
        s.status, s.compile_error = "queued", None
        db.add(round_job(r, "compile", {"submission_id": s.id, "round_id": r.id}))
    elif kind == "forfeit":
        check(e and r.status != "finalized", "Cần trận chưa chốt vòng")
        losers = data.get("player_ids", [])
        check(losers and set(losers) <= {e.player_a, e.player_b}, "Cần đội bỏ cuộc trong trận", 400)
        administrative(db, t, r, e, e.player_a not in losers and eligible(db, t, r, e.player_a),
                       e.player_b not in losers and eligible(db, t, r, e.player_b), reason)
        r.qualification = None
        if r.status == "provisional":
            r.status = "running"
    elif kind in ("withdraw", "disqualify"):
        pid = data.get("player_id")
        entry = enrollment(db, t, pid)
        check(entry and entry.status == "enrolled", "Đội không còn tham gia")
        all_encounters = db.scalars(select(Encounter).join(TournamentRound, Encounter.round_id == TournamentRound.id).where(
            TournamentRound.tournament_id == t.id, (Encounter.player_a == pid) | (Encounter.player_b == pid))).all()
        for encounter in all_encounters:
            check(not any(m.status == "running" for m in games(db, encounter)), "Đợi worker hoàn tất ván đang chạy")
        entry.status = "withdrawn" if kind == "withdraw" else "disqualified"
        group_round = db.scalar(select(TournamentRound).where(TournamentRound.tournament_id == t.id, TournamentRound.level == 0))
        for encounter in all_encounters:
            er = db.get(TournamentRound, encounter.round_id)
            if er.level == 0 and er.status != "finalized":
                encounter.excluded = True
                if encounter.status not in ("finalized", "provisional"):
                    administrative(db, t, er, encounter, False, False, reason)
                if encounter.status == "provisional" and encounter.playing_result is not None:
                    # Keep completed legitimate Elo when removing group points.
                    encounter.status = "finalized"
            elif er.level and er.status != "finalized":
                administrative(db, t, er, encounter, encounter.player_a != pid and eligible(db, t, er, encounter.player_a),
                               encounter.player_b != pid and eligible(db, t, er, encounter.player_b), reason)
        if group_round and group_round.status == "provisional":
            group_round.status, group_round.qualification = "running", None
    elif kind == "correct_game":
        m = db.get(Match, data.get("match_id"))
        check(e and m and m.encounter_id == e.id and m.status == "done" and not e.administrative,
              "Cần ván đã hoàn tất hợp lệ trong trận")
        check(r.status != "finalized", "Vòng đã chốt; không sửa suất đi tiếp của vòng đã chốt")
        score = data.get("south_score")
        check(score in (0, .5, 1), "south_score phải là 0, 0.5 hoặc 1", 400)
        check(not any(g.status in ("running", "queued") for g in games(db, e)), "Đợi các ván đang chạy/chờ hoàn tất")
        m.points_a, m.points_b = score, 1-score
        m.winner_id = m.player_a if score == 1 else m.player_b if score == 0 else None
        detail = json.loads(m.detail or "{}")
        detail["correction"] = data
        m.detail = dump(detail)
        # A correction can remove the need for an extra pair. Preserve its
        # replay, exclude it from aggregation, and never invent game results.
        e.status = "running"
        e.reason = reason
        event = db.scalar(select(EloEvent).where(EloEvent.encounter_id == e.id))
        if event:
            event.active = False
            recalculate_elo(db)
        r.qualification = None
        if r.status == "provisional":
            r.status = "running"
        aggregate(db, e, actor)
    elif kind == "cancel":
        scope = data.get("scope")
        check(scope in ("round", "tournament"), "Chỉ hủy toàn bộ vòng hoặc giải", 400)
        rounds = db.scalars(select(TournamentRound).where(TournamentRound.tournament_id == t.id)).all() if scope == "tournament" else [r]
        for target in rounds:
            check(target.status != "finalized", "Không hủy vòng đã chốt")
            for encounter in db.scalars(select(Encounter).where(Encounter.round_id == target.id)):
                affected = games(db, encounter)
                check(not any(m.status == "running" for m in affected), "Đợi worker kết thúc job đang chạy")
                ids = {m.id for m in affected}
                for job in db.scalars(select(Job).where(Job.kind == "match", Job.status.in_(ACTIVE_JOBS)).with_for_update()):
                    if json.loads(job.payload)["match_id"] in ids:
                        check(job.status not in RUNNING_JOBS, "Đợi worker kết thúc job đang chạy")
                        job.status = "cancelled"
                for m in affected:
                    if m.status != "done":
                        m.status = "void"
                encounter.status, encounter.reason = "cancelled", reason
                event = db.scalar(select(EloEvent).where(EloEvent.encounter_id == encounter.id))
                if event:
                    event.active = False
            target.status = "cancelled"
        t.status, t.result = "cancelled", reason
        recalculate_elo(db)
    else:
        check(False, "Thao tác ngoại lệ không hợp lệ", 400)
    action = audit(db, t, kind, data, actor, r, e)
    if kind == "disqualify":
        # Discipline can affect a replacement or a vacant slot even after
        # group finalization; it has its own published appeal interval.
        action.round_id = None
        action.appeal_until = utcnow() + timedelta(minutes=30)
    db.flush()
    pump(db)
    return {"ok": True}


def _automation_step(db,t,now):
    """Advance one normal lifecycle action; return whether another action may be due."""
    actor="scheduler"
    if t.status in ("complete","cancelled"):
        return False,"Giải đã kết thúc."
    if held(db,t):
        paused = db.scalar(select(TournamentRound).where(TournamentRound.tournament_id == t.id,
            TournamentRound.status.not_in(("finalized", "cancelled"))).order_by(TournamentRound.level.desc()).limit(1))
        if paused and round_paused(db, paused):
            return False,"Đã dừng vòng; chọn worker rồi bấm Tiếp tục vòng."
        return False,"Tạm dừng tự động: còn hold hoặc khiếu nại chưa giải quyết."
    if t.status=="registration":
        if now<=t.registration_close:
            return False,"Chờ hết hạn đăng ký để tự khóa danh sách và xác nhận bảng."
        lock_roster(db,t,actor)
        return True,"Đã tự khóa đăng ký."
    if t.status=="allocated":
        confirm_groups(db,t,actor)
        return True,"Đã tự xác nhận bảng."
    if t.status=="qualified":
        confirm_bracket(db,t,actor)
        return True,"Đã tự xác nhận nhánh knockout."
    check(t.status in ("groups","knockout"),"Trạng thái giải cần BTC kiểm tra")
    r=db.scalar(select(TournamentRound).where(TournamentRound.tournament_id==t.id)
                .order_by(TournamentRound.level.desc()).limit(1))
    check(r is not None,"Chưa có vòng thi để tự điều hành")
    if r.status=="planned":
        start=r.opens_at or now
        end=r.closes_at or start+timedelta(minutes=t.auto_submission_minutes)
        open_window(db,t,r,start,end,actor)
        return True,"Đã tự mở cửa sổ nộp bot mới."
    if r.status=="submission":
        check(r.closes_at is not None,"Cửa sổ nộp chưa có giờ đóng")
        if now<=r.closes_at:
            return False,"Chờ hết hạn nộp bot; sau đó tự khóa bot và chạy các lượt."
        failed_check=db.scalar(select(Submission.id).join(RoundSubmission,RoundSubmission.submission_id==Submission.id)
            .where(RoundSubmission.round_id==r.id,Submission.status=="infrastructure_error").limit(1))
        if failed_check:
            return False,"Tạm dừng tự động: lỗi hạ tầng kiểm tra bot, cần BTC xử lý và retry."
        lock_bots(db,t,r,actor,now=now)
        return True,"Đã tự khóa bot."
    if r.status=="provisional" and r.level==0:
        if r.appeal_until and now<r.appeal_until:
            return False,"Chờ hết 30 phút khiếu nại phân hạng để chốt bảng và tạo nhánh."
        finalize_round(db,t,r,actor,now=now)
        return True,"Đã tự chốt vòng bảng."
    check(r.status in ("locked","running"),"Trạng thái vòng cần BTC kiểm tra")
    encounters=db.scalars(select(Encounter).where(Encounter.round_id==r.id).order_by(Encounter.id)).all()
    if any(e.status=="paused" for e in encounters):
        return False,"Tạm dừng tự động: có lỗi hạ tầng, cần BTC xử lý và retry."
    due=next((e for e in encounters if e.status=="provisional" and
              (r.level > 0 or (e.appeal_until and e.appeal_until<=now))),None)
    if due:
        finalize_encounters(db,t,r,actor,eid=due.id,now=now)
        return True,"Đã tự chốt encounter knockout không có hold." if r.level else "Đã tự chốt encounter hết hạn khiếu nại."
    batch=min((e.batch for e in encounters if e.status=="scheduled"),default=None)
    if batch is not None:
        start_batch(db,t,r,batch,actor)
        return True,"Đã tự bắt đầu lượt thi."
    if any(e.status not in ("finalized","cancelled") for e in encounters):
        return False,("Đang thi liên tục; chỉ mở cửa sổ nộp vòng sau khi tất cả suất đi tiếp đã được xác định."
                      if r.level else "Đang thi hoặc chờ hết hạn khiếu nại encounter; hệ thống sẽ tự tiếp tục.")
    if r.level==0:
        result=qualify(db,t,r,actor)
        return True,"Đã tạo lượt phân hạng phụ." if result["playoffs_required"] else "Đã công bố phân hạng."
    finalize_round(db,t,r,actor,now=now)
    return True,"Đã tự chốt vòng loại trực tiếp."


def auto_tick(db,now=None):
    """Run due actions once across workers, preserving deadline and appeal checks."""
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(74620146)")):
        return False
    now=now or utcnow()
    tournaments=db.scalars(select(Tournament).where(Tournament.auto_run.is_(True),
        Tournament.status.not_in(["complete","cancelled"])).order_by(Tournament.id)).all()
    for t in tournaments:
        for _ in range(64):
            try:
                # A blocked action must not leave partial bot locks or advancement.
                with db.begin_nested():
                    changed,note=_automation_step(db,t,now)
                    db.flush()
            except HTTPException as error:
                t.auto_status=str(error.detail)
                break
            t.auto_status=note
            if not changed:
                break
        db.flush()
    return True


def team_status(db, t, r, pid):
    entry = enrollment(db, t, pid)
    links = db.scalars(select(RoundSubmission).where(RoundSubmission.round_id == r.id,
                                                   RoundSubmission.player_id == pid).order_by(RoundSubmission.id)).all() if r else []
    submissions = []
    for link in links:
        s = db.get(Submission, link.submission_id)
        submissions.append({"id": s.id, "attempt": link.attempt, "status": s.status,
                            "compile_error": s.compile_error, "created_at": s.created_at})
    locked = bot(db, r.id, pid) if r else None
    ready = [s for s in submissions if s["status"] == "ready"]
    now=utcnow()
    reason=""
    if t.status in ("complete","cancelled"):
        reason="Giải đã kết thúc; không nhận thêm bot."
    elif not entry:
        reason="Bạn chưa tham gia giải Tournament này."
    elif entry.status!="enrolled":
        reason="Đội đã rút hoặc bị truất quyền khỏi giải."
    elif not r:
        reason="Ban tổ chức chưa xác nhận vòng thi."
    elif pid not in participants(db,t,r):
        reason="Đội không có suất tham dự vòng thi này."
    elif r.status=="planned":
        reason="Ban tổ chức chưa mở cửa sổ nộp bot cho vòng này."
    elif r.status!="submission":
        reason="Bot của vòng này đã khóa; cửa sổ nộp đã kết thúc."
    elif len(links)>=3:
        reason="Đã dùng hết 3 lượt nộp bot của vòng này."
    elif now<r.opens_at:
        reason="Chưa tới giờ mở cửa sổ nộp bot."
    elif now>r.closes_at:
        reason="Đã hết hạn nộp bot của vòng này."
    return {"enrolled": bool(entry), "enrollment_status": entry.status if entry else None,
            "can_submit":not reason,"submission_block_reason":reason,
            "round_id": r.id if r else None, "attempts_used": len(links), "attempts_max": 3,
            "selected_submission": ready[-1]["id"] if ready else None,
            "locked_submission": locked.submission_id if locked else None,
            "eligibility_reason": locked.reason if locked else "awaiting_bot_lock" if entry else "not_enrolled",
            "eligible": eligible(db, t, r, pid) if r else False, "submissions": submissions,
            "elo": ratings(db).get(pid, 1000)}


def public_view(db, t):
    rounds = db.scalars(select(TournamentRound).where(TournamentRound.tournament_id == t.id).order_by(TournamentRound.level)).all()
    names = {p.id: p.name for p in db.scalars(select(Player))}
    elo = ratings(db)
    encounters = []
    for r in rounds:
        for e in db.scalars(select(Encounter).where(Encounter.round_id == r.id).order_by(Encounter.id)):
            encounters.append({"id": e.id, "round_id": r.id, "level": r.level, "group_id": e.group_id,
                "playoff": e.playoff, "batch": e.batch, "slot": e.slot,
                "player_a": e.player_a, "player_b": e.player_b, "team_a": names.get(e.player_a), "team_b": names.get(e.player_b),
                "status": e.status, "administrative": e.administrative, "excluded": e.excluded,
                "winner_id": e.winner_id, "playing_result": e.playing_result,
                "encounter_points": [e.points_a, e.points_b], "game_points": [e.game_points_a, e.game_points_b],
                "extra_scores": json.loads(e.extra_scores), "reason": e.reason, "appeal_until": e.appeal_until,
                "games": [{"id": m.id, "number": m.game_number, "south": m.player_a, "north": m.player_b,
                           "submission_south": m.submission_a, "submission_north": m.submission_b,
                           "status": m.status, "points": [m.points_a, m.points_b],
                           "replay_url": f"/api/matches/{m.id}"} for m in games(db, e)]})
    group_round = next((r for r in rounds if r.level == 0), None)
    groups = []
    for group in db.scalars(select(TournamentGroup).where(TournamentGroup.tournament_id == t.id).order_by(TournamentGroup.number)):
        ids = db.scalars(select(GroupMember.player_id).where(GroupMember.group_id == group.id)).all()
        entries = db.scalars(select(Encounter).where(Encounter.group_id == group.id, Encounter.playoff.is_(None))).all()
        rows, blocks = ranking_blocks(ids, entries)
        order = json.loads(group.final_order) if group.final_order else (
            json.loads(group_round.qualification)[str(group.id)]["order"] if group_round.qualification else [pid for block in blocks for pid in block])
        if not group.final_order and not group_round.qualification:
            order.sort(key=lambda pid: (enrollment(db, t, pid).status != "enrolled", not eligible(db, t, group_round, pid)))
        shown = []
        eligible_count = 0
        for pid in order:
            good = eligible(db, t, group_round, pid)
            if good:
                eligible_count += 1
            shown.append({**rows[pid], "name": names.get(pid), "eligible": good,
                          "enrollment_status": enrollment(db, t, pid).status,
                          "qualified": bool(group.final_order and good and eligible_count <= t.qualifiers),
                          "elo": elo.get(pid, 1000)})
        groups.append({"id": group.id, "number": group.number, "finalized": bool(group.final_order), "standings": shown})
    actions = db.scalars(select(OrganizerAction).where(OrganizerAction.tournament_id == t.id).order_by(OrganizerAction.id)).all()
    return {"id": t.id, "name": t.name, "status": t.status,
        "max_group_size": t.max_group_size, "qualifiers": t.qualifiers,
        "registration_open": t.registration_open, "registration_close": t.registration_close,
        "appeal_contact": t.appeal_contact, "incident_policy": t.incident_policy,
        "auto_run":t.auto_run,"auto_submission_minutes":t.auto_submission_minutes,"auto_status":t.auto_status,
        "rules_digest": t.rules_digest, "environment": json.loads(t.environment),
        "allocation": json.loads(t.allocation) if t.allocation else None,
        "roster": [{"player_id": e.player_id, "name": names.get(e.player_id), "status": e.status,
                    "frozen_elo": e.frozen_elo} for e in db.scalars(select(Enrollment).where(Enrollment.tournament_id == t.id))],
        "rounds": [{"id": r.id, "level": r.level, "status": r.status, "opens_at": r.opens_at,
                    "closes_at": r.closes_at, "appeal_until": r.appeal_until,
                    "worker_id": r.worker_id, "paused": round_paused(db, r),
                    "rules_digest": r.rules_digest, "environment": json.loads(r.environment)} for r in rounds],
        "groups": groups, "encounters": encounters,
        "bracket": json.loads(t.bracket) if t.bracket else None,
        "champion_id": t.champion_id, "champion": names.get(t.champion_id), "result": t.result,
        "notices": [notice_view(a) for a in actions
                    if a.kind in PUBLIC_NOTICE_KINDS and a.notice_hidden_at is None],
        "holds": [{"id": a.id, "round_id": a.round_id, "encounter_id": a.encounter_id,
                   "actor": a.actor, "created_at": a.created_at, "resolved_at": a.resolved_at,
                   "reason": json.loads(a.evidence).get("reason"), "appeal_until": a.appeal_until}
                  for a in actions if a.kind != QUEUE_GUARD and (a.hold or a.appeal_until)],
        "draws": [{"key": d.key, "reason": d.reason, "choices": json.loads(d.choices), "method": d.method,
                   "seed": d.seed, "result": json.loads(d.result), "actor": d.actor, "created_at": d.created_at}
                  for d in db.scalars(select(RecordedDraw).where(RecordedDraw.tournament_id == t.id))]}
