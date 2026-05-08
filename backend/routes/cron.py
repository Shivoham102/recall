import os
from fastapi import APIRouter, Header, HTTPException, Request
from tools.google_services import refresh_style_profiles_weekly

router = APIRouter()


def _is_authorized(request: Request, authorization: str | None, x_cron_secret: str | None) -> bool:
    secret = os.environ.get("CRON_SECRET", "").strip()
    if not secret:
        return False

    bearer_ok = authorization == f"Bearer {secret}"
    header_ok = x_cron_secret == secret
    vercel_signature_ok = request.headers.get("x-vercel-cron-secret") == secret
    return bearer_ok or header_ok or vercel_signature_ok


@router.get("/jobs/refresh-email-style-profiles")
async def refresh_email_style_profiles(
    request: Request,
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None),
):
    if not _is_authorized(request, authorization, x_cron_secret):
        raise HTTPException(status_code=401, detail="Unauthorized cron request")

    result = await refresh_style_profiles_weekly({"max_users": 500})
    return {
        "ok": True,
        **result,
    }
