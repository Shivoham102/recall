import os
import pathlib
import secrets
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv

# Load .env from project root (dev only — not present in shipped builds)
load_dotenv(pathlib.Path(__file__).parent.parent / ".env")

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 30

_bearer = HTTPBearer()

# Path where the generated secret is persisted between backend restarts
_SECRET_FILE = pathlib.Path.home() / "AppData" / "Local" / "Recall" / "jwt_secret.txt"


def _secret() -> str:
    # 1. Prefer explicit env var (set in dev via .env)
    if s := os.environ.get("JWT_SECRET"):
        return s
    # 2. Shipped app: load from AppData or generate once on first run
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


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    try:
        return decode_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
