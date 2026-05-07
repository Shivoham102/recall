import os
import pathlib
import secrets
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from supabase import create_client, Client

# Load .env from project root (dev only — not present in shipped builds)
load_dotenv(pathlib.Path(__file__).parent.parent / ".env")

_bearer = HTTPBearer()

_supabase_client: Client | None = None


def _get_supabase() -> Client:
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_ANON_KEY"],
        )
    return _supabase_client


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Verify JWT — accepts both Supabase Auth JWTs and legacy custom JWTs.
    Tries Supabase first; falls back to local decode so existing sessions keep working
    during the auth migration."""
    token = creds.credentials

    # 1. Try Supabase Auth JWT (new path)
    try:
        res = _get_supabase().auth.get_user(token)
        if res and res.user:
            return {"sub": res.user.id, "email": res.user.email or ""}
    except Exception:
        pass

    # 2. Fall back to legacy custom JWT (old path — kept while sessions migrate)
    try:
        return decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Legacy helpers (used only by routes/auth.py — not mounted in new deployment) ──

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 30

_SECRET_FILE = pathlib.Path.home() / "AppData" / "Local" / "Recall" / "jwt_secret.txt"


def _secret() -> str:
    if s := os.environ.get("JWT_SECRET"):
        return s
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_text().strip()
    new_secret = secrets.token_hex(32)
    _SECRET_FILE.write_text(new_secret)
    return new_secret


def create_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS),
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
