"""HTTP boundary for tournament services; organizer auth stays in main.py."""
import json
import os
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt
from sqlalchemy import select

from .db import SessionLocal
from .models import Tournament, TournamentRound, OrganizerAction, GameAttempt, Match
from .security import authorized_player
from . import tournament as service

router = APIRouter()


class TournamentInput(BaseModel):
    name: str = Field(default="OTTv2 Tournament", max_length=120)
    max_group_size: StrictInt = Field(default=4, gt=0)
    qualifiers: StrictInt = Field(default=2, gt=0)
    registration_open: datetime
    registration_close: datetime
    appeal_contact: str = Field(default="", max_length=500)
    incident_policy: str = Field(default="", max_length=2000)
    auto_run: StrictBool = True
    auto_submission_minutes: StrictInt = Field(default=1440,gt=0,le=525600)


class AutomationInput(BaseModel):
    auto_run: StrictBool
    auto_submission_minutes: StrictInt = Field(default=1440,gt=0,le=525600)


class ForceInput(BaseModel):
    force: StrictBool = False
    force_reason: str = Field(default="", max_length=2000)


class WindowInput(ForceInput):
    opens_at: datetime
    closes_at: datetime


class BotInput(BaseModel):
    language: str
    source: str = Field(min_length=1)


class ActionInput(ForceInput):
    batch: int | None = Field(default=None, gt=0)
    encounter_id: int | None = None


class NoticeInput(BaseModel):
    message: str = Field(default="", max_length=4000)
    kind: Literal["notice", "rules_update"] = "notice"


class CloseInput(BaseModel):
    reason: str = Field(default="", max_length=2000)


def organizer(x_admin_key: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    # Import at request time avoids a router/main import cycle.
    from .main import require_admin
    require_admin(x_admin_key, authorization)
    return os.getenv("ADMIN_USERNAME", "admin")


@router.get("/api/tournaments")
def tournaments():
    with SessionLocal() as db:
        return [{"id": t.id, "name": t.name, "status": t.status, "champion_id": t.champion_id,
                 "result": t.result} for t in db.scalars(select(Tournament).order_by(Tournament.id.desc()))]


@router.get("/api/tournaments/{tid}")
def tournament_detail(tid: int):
    with SessionLocal() as db:
        return service.public_view(db, service.get_tournament(db, tid))


@router.get("/api/tournaments/{tid}/rules", response_class=PlainTextResponse)
def tournament_rules(tid: int):
    with SessionLocal() as db:
        return service.get_tournament(db, tid).rules_text


@router.get("/api/tournaments/{tid}/{view}")
def tournament_section(tid: int, view: str):
    service.check(view in ("standings", "encounters", "bracket", "results"), "Không tìm thấy trang", 404)
    with SessionLocal() as db:
        data = service.public_view(db, service.get_tournament(db, tid))
        if view == "standings":
            return data["groups"]
        if view == "results":
            return {key: data[key] for key in ("status", "champion", "champion_id", "result", "groups", "bracket")}
        return data[view]


@router.post("/api/tournaments/{tid}/join")
def join(tid: int, player_id: int = Depends(authorized_player)):
    received = service.utcnow()
    with SessionLocal.begin() as db:
        service.serialize(db)
        entry = service.join(db, service.get_tournament(db, tid), player_id, received)
        db.flush()
        return {"player_id": entry.player_id, "status": entry.status}


@router.get("/api/tournaments/{tid}/team/status")
def team_status(tid: int, round_id: int | None = None, player_id: int = Depends(authorized_player)):
    with SessionLocal() as db:
        t = service.get_tournament(db, tid)
        r = service.get_round(db, tid, round_id) if round_id else db.scalar(select(TournamentRound).where(
            TournamentRound.tournament_id == tid).order_by(TournamentRound.level.desc()).limit(1))
        return service.team_status(db, t, r, player_id)


@router.post("/api/tournaments/{tid}/rounds/{rid}/submissions")
def submit(tid: int, rid: int, body: BotInput, idempotency_key: str | None = Header(default=None),
           player_id: int = Depends(authorized_player)):
    # Capture receipt time before waiting for quota serialization.
    received = service.utcnow()
    with SessionLocal.begin() as db:
        service.serialize(db)
        t, r = service.get_tournament(db, tid), service.get_round(db, tid, rid)
        entry = service.submit(db, t, r, player_id, body.language, body.source, idempotency_key, received)
        db.flush()
        return {"submission_id": entry.submission_id, "attempt": entry.attempt, "status": "accepted"}


@router.post("/api/admin/tournaments")
def create(body: TournamentInput, actor: str = Depends(organizer)):
    with SessionLocal.begin() as db:
        service.serialize(db)
        t = service.create(db, body.model_dump(), actor)
        return {"id": t.id, "status": t.status}


@router.get("/api/admin/tournaments/{tid}/notices")
def notices(tid: int, actor: str = Depends(organizer)):
    with SessionLocal() as db:
        return [service.notice_view(a) for a in service.visible_notices(db, service.get_tournament(db, tid))]


@router.post("/api/admin/tournaments/{tid}/notices", status_code=201)
def publish_notice(tid: int, body: NoticeInput, actor: str = Depends(organizer)):
    with SessionLocal.begin() as db:
        service.serialize(db)
        action = service.publish_notice(db, service.get_tournament(db, tid), body.message, body.kind, actor)
        return service.notice_view(action) if action else Response(status_code=204)


@router.delete("/api/admin/tournaments/{tid}/notices")
def clear_notices(tid: int, actor: str = Depends(organizer)):
    with SessionLocal.begin() as db:
        service.serialize(db)
        return {"cleared": service.remove_notices(db, service.get_tournament(db, tid), actor)}


@router.delete("/api/admin/tournaments/{tid}/notices/{notice_id}")
def remove_notice(tid: int, notice_id: int, actor: str = Depends(organizer)):
    with SessionLocal.begin() as db:
        service.serialize(db)
        return {"removed": service.remove_notices(db, service.get_tournament(db, tid), actor, notice_id)}


@router.post("/api/admin/tournaments/{tid}/close")
def close_tournament(tid: int, body: CloseInput | None = None, actor: str = Depends(organizer)):
    with SessionLocal.begin() as db:
        service.serialize(db)
        t = service.get_tournament(db, tid)
        service.close_tournament(db, t, actor, body.reason if body else "")
        return {"id": t.id, "status": t.status, "result": t.result}


@router.post("/api/admin/tournaments/{tid}/{action}")
def tournament_action(tid: int, action: str, body: ForceInput | None = None, actor: str = Depends(organizer)):
    with SessionLocal.begin() as db:
        service.serialize(db)
        t = service.get_tournament(db, tid)
        actions = {"lock-roster": service.lock_roster, "confirm-groups": service.confirm_groups,
                   "confirm-bracket": service.confirm_bracket}
        service.check(action in actions, "Thao tác không hợp lệ", 404)
        options = body.model_dump() if body else {}
        actions[action](db, t, actor, **options)
        return {"id": t.id, "status": t.status}


@router.put("/api/admin/tournaments/{tid}/configuration")
def configure(tid: int, body: TournamentInput, actor: str = Depends(organizer)):
    with SessionLocal.begin() as db:
        service.serialize(db)
        t = service.get_tournament(db, tid)
        service.check(t.status == "registration", "Cấu hình đã khóa")
        start, end = service.aware(body.registration_open), service.aware(body.registration_close)
        service.check(start < end and (body.max_group_size > body.qualifiers or body.max_group_size == body.qualifiers == 2), "Cấu hình không hợp lệ", 400)
        for key, value in body.model_dump().items():
            if key in ("auto_run","auto_submission_minutes") and key not in body.model_fields_set:
                continue
            setattr(t, key, value)
        service.audit(db, t, "configure", body.model_dump(), actor)
        return {"ok": True}


@router.post("/api/admin/tournaments/{tid}/enrollments/{pid}/withdraw")
def withdraw_enrollment(tid: int, pid: int, body: dict[str, Any] | None = None, actor: str = Depends(organizer)):
    with SessionLocal.begin() as db:
        service.serialize(db)
        t = service.get_tournament(db, tid)
        r = db.scalar(select(TournamentRound).where(TournamentRound.tournament_id == tid).order_by(TournamentRound.level.desc()).limit(1))
        return service.exception(db, t, r, "withdraw", {**(body or {}), "player_id": pid}, actor)


@router.put("/api/admin/tournaments/{tid}/automation")
def automation(tid: int, body: AutomationInput, actor: str = Depends(organizer)):
    with SessionLocal.begin() as db:
        service.serialize(db)
        t=service.get_tournament(db,tid)
        service.check(t.status not in ("complete","cancelled"),"Giải đã kết thúc")
        t.auto_run,t.auto_submission_minutes=body.auto_run,body.auto_submission_minutes
        t.auto_status=None
        service.audit(db,t,"configure_automation",body.model_dump(),actor)
        return {"auto_run":t.auto_run,"auto_submission_minutes":t.auto_submission_minutes}


@router.put("/api/admin/tournaments/{tid}/rounds/{rid}/window")
def window(tid: int, rid: int, body: WindowInput, actor: str = Depends(organizer)):
    with SessionLocal.begin() as db:
        service.serialize(db)
        t, r = service.get_tournament(db, tid), service.get_round(db, tid, rid)
        service.open_window(db, t, r, body.opens_at, body.closes_at, actor,
                            force=body.force, force_reason=body.force_reason)
        return {"id": r.id, "status": r.status}


@router.post("/api/admin/tournaments/{tid}/rounds/{rid}/{action}")
def round_action(tid: int, rid: int, action: str, body: ActionInput, actor: str = Depends(organizer)):
    with SessionLocal.begin() as db:
        service.serialize(db)
        t, r = service.get_tournament(db, tid), service.get_round(db, tid, rid)
        options = {"force": body.force, "force_reason": body.force_reason}
        if action == "lock-bots":
            service.lock_bots(db, t, r, actor, **options)
        elif action == "start-batch":
            batch = service.start_batch(db, t, r, body.batch, actor, **options)
            return {"id": r.id, "status": r.status, "batch": batch}
        elif action == "finalize-encounters":
            service.finalize_encounters(db, t, r, actor, body.encounter_id, **options)
        elif action == "qualify":
            return service.qualify(db, t, r, actor, **options)
        elif action == "finalize":
            service.finalize_round(db, t, r, actor, **options)
        else:
            service.check(False, "Thao tác không hợp lệ", 404)
        return {"id": r.id, "status": r.status}


@router.post("/api/admin/tournaments/{tid}/rounds/{rid}/exceptions/{kind}")
def exception(tid: int, rid: int, kind: str, body: dict[str, Any], actor: str = Depends(organizer)):
    with SessionLocal.begin() as db:
        service.serialize(db)
        return service.exception(db, service.get_tournament(db, tid), service.get_round(db, tid, rid), kind, body, actor)


@router.get("/api/admin/tournaments/{tid}/audit")
def audit(tid: int, actor: str = Depends(organizer)):
    with SessionLocal() as db:
        service.get_tournament(db, tid)
        rounds = db.scalars(select(TournamentRound.id).where(TournamentRound.tournament_id == tid)).all()
        matches = db.scalars(select(Match.id).join(service.Encounter, Match.encounter_id == service.Encounter.id).where(service.Encounter.round_id.in_(rounds))).all()
        return {"actions": [{"id": a.id, "actor": a.actor, "kind": a.kind, "evidence": json.loads(a.evidence),
                             "hold": a.hold, "resolved_at": a.resolved_at, "appeal_until": a.appeal_until,
                             "notice_hidden_at": a.notice_hidden_at, "created_at": a.created_at}
                            for a in db.scalars(select(OrganizerAction).where(OrganizerAction.tournament_id == tid).order_by(OrganizerAction.id))],
                "attempts": [{"match_id": a.match_id, "attempt": a.attempt, "worker_id": a.worker_id,
                              "status": a.status, "detail": a.detail, "started_at": a.started_at, "finished_at": a.finished_at}
                             for a in db.scalars(select(GameAttempt).where(GameAttempt.match_id.in_(matches)).order_by(GameAttempt.id))]}
