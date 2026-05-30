from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user
from db import get_admin_db
from security.crypto import encrypt_for_storage


router = APIRouter()


class GoogleTokenBody(BaseModel):
    provider_token: str | None = None
    provider_refresh_token: str
    google_token_expiry: str | None = None


@router.post("/auth/google/tokens")
async def store_google_tokens(
    body: GoogleTokenBody,
    user: dict = Depends(get_current_user),
):
    if not body.provider_refresh_token.strip():
        raise HTTPException(status_code=422, detail="Missing provider_refresh_token")

    payload = {
        "id": user["sub"],
        "email": user.get("email", ""),
        "google_access_token": encrypt_for_storage(body.provider_token),
        "google_refresh_token": encrypt_for_storage(body.provider_refresh_token),
        "google_token_expiry": body.google_token_expiry,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        get_admin_db().table("users").upsert(payload, on_conflict="id").execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to store Google tokens") from exc

    name = user.get("name", "")
    if name:
        try:
            from supermemory_client import add_user_memory, make_memory_custom_id
            cid = make_memory_custom_id(user["sub"], "fact", f"This person's name is {name}.")
            await add_user_memory(
                user["sub"],
                f"This person's name is {name}.",
                category="fact",
                custom_id=cid,
            )
        except Exception as exc:
            print(f"[google_tokens] supermemory name store failed: {exc}")

    return {"ok": True}
