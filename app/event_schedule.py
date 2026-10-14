from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from .models import EventControl

VN = ZoneInfo("Asia/Ho_Chi_Minh")

def as_vietnam_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=VN)
    return value.astimezone(VN)

def get_event_state(db: Session, matches_engine_enabled: bool, now: datetime | None = None) -> dict:
    now = as_vietnam_time(now or datetime.now(VN))
    control = db.get(EventControl, 1)
    start = datetime.fromisoformat(control.start_at) if control and control.start_at else None
    end = datetime.fromisoformat(control.end_at) if control and control.end_at else None
    if start:
        start = as_vietnam_time(start)
    if end:
        end = as_vietnam_time(end)
    window_active = bool(control and control.enabled and start and end and start <= now < end)
    matches_enabled = window_active and matches_engine_enabled
    if matches_enabled:
        status = "open"
    elif window_active:
        status = "worker_unavailable"
    elif control and control.enabled and start and now < start:
        status = "scheduled"
    elif control and control.enabled and end and now >= end:
        status = "closed"
    elif control and not control.enabled and start and end:
        status = "closed"
    else:
        status = "not_scheduled"
    return {
        "status": status,
        "enabled": bool(control and control.enabled),
        "start_at": start.isoformat() if start else None,
        "end_at": end.isoformat() if end else None,
        "window_active": window_active,
        "matches_enabled": matches_enabled,
        "worker_enabled": matches_engine_enabled,
        "timezone": "Asia/Ho_Chi_Minh",
    }
