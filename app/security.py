import hashlib, hmac, os, secrets
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from .db import SessionLocal
from .models import Player

bearer_scheme = HTTPBearer(auto_error=False)

def digest(token): return hashlib.sha256(token.encode()).hexdigest()

def hash_password(password):
    salt = secrets.token_bytes(16)
    rounds = 310_000
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2${rounds}${salt.hex()}${hashed.hex()}"

def verify_password(password, encoded):
    if not encoded:
        return False
    try:
        scheme, rounds, salt, expected = encoded.split("$")
        if scheme != "pbkdf2":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def authorized_player(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Cần gửi Authorization: Bearer <token>")
    token = credentials.credentials
    with SessionLocal() as db:
        players = db.scalars(select(Player)).all()
        for p in players:
            if hmac.compare_digest(p.token_hash, digest(token)):
                return p.id
    raise HTTPException(401, "Token không hợp lệ")

def new_token(): return secrets.token_urlsafe(32)

def check_registration(code):
    expected = os.getenv("REGISTRATION_CODE", "")
    if expected and not hmac.compare_digest(code or "", expected):
        raise HTTPException(403, "Mã đăng ký không hợp lệ")
    if os.getenv("REGISTRATION_OPEN", "true").lower() != "true":
        raise HTTPException(403, "Đăng ký đang đóng")
