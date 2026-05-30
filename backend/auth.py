import os
import pathlib
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from supabase import create_client, Client

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
    token = creds.credentials
    try:
        res = _get_supabase().auth.get_user(token)
        if res and res.user:
            meta = res.user.user_metadata or {}
            name = meta.get("full_name") or meta.get("name") or ""
            return {"sub": res.user.id, "email": res.user.email or "", "name": name}
    except Exception:
        pass
    raise HTTPException(status_code=401, detail="Invalid or expired token")
