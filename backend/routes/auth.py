import os
import time
import pathlib
import httpx
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse
from google_auth_oauthlib.flow import Flow
from db import get_db
from auth import create_token, get_current_user

# Allow HTTP redirect URI for localhost (required for installed-app OAuth clients)
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

router = APIRouter()

_CREDENTIALS_FILE = pathlib.Path(__file__).parent.parent / "credentials.json"
def _redirect_uri() -> str:
    port = os.environ.get("BACKEND_PORT", "8000")
    return f"http://localhost:{port}/auth/callback"
_TTL = 300  # 5 minutes

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]

# state → (flow, created_at)
_active_flows: dict[str, tuple] = {}
# state → (jwt_token, created_at)
_pending_jwts: dict[str, tuple] = {}


def _cleanup() -> None:
    now = time.time()
    for state in list(_active_flows):
        if now - _active_flows[state][1] > _TTL:
            del _active_flows[state]
    for state in list(_pending_jwts):
        if now - _pending_jwts[state][1] > _TTL:
            del _pending_jwts[state]


@router.get("/auth/url")
def get_auth_url():
    """Returns the Google OAuth URL and state token. Frontend opens this in the system browser."""
    if not _CREDENTIALS_FILE.exists():
        raise HTTPException(
            status_code=500,
            detail="credentials.json not found in backend/. Download it from Google Cloud Console.",
        )
    _cleanup()

    flow = Flow.from_client_secrets_file(
        str(_CREDENTIALS_FILE),
        scopes=SCOPES,
        redirect_uri=_redirect_uri(),
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    _active_flows[state] = (flow, time.time())
    return {"url": auth_url, "state": state}


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str):
    """Google redirects here after user consents. Exchanges code for tokens and parks JWT."""
    if state not in _active_flows:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    flow, _ = _active_flows.pop(state)

    try:
        # Pass the full URL so the OAuth session verifies state and extracts code
        flow.fetch_token(authorization_response=str(request.url))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {e}")

    creds = flow.credentials

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {creds.token}"},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch user info from Google")

    user_info = resp.json()
    user_id = user_info["sub"]
    email = user_info.get("email", "")
    name = user_info.get("name", "")

    expiry_iso = None
    if creds.expiry:
        expiry = creds.expiry if creds.expiry.tzinfo else creds.expiry.replace(tzinfo=timezone.utc)
        expiry_iso = expiry.isoformat()

    get_db().table("users").upsert(
        {
            "id": user_id,
            "email": email,
            "name": name,
            "google_access_token": creds.token,
            "google_refresh_token": creds.refresh_token,
            "google_token_expiry": expiry_iso,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="id",
    ).execute()

    token = create_token(user_id, email)
    _pending_jwts[state] = (token, time.time())

    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head><title>Recall — Signed in</title>
<style>
  body { font-family: system-ui; background: #08080f; color: #d8d8f0;
         display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
  h2 { font-size: 1.4rem; font-weight: 500; }
  p  { color: rgba(200,200,230,0.5); margin-top: 0.5rem; font-size: 0.9rem; }
</style>
</head>
<body>
  <div style="text-align:center">
    <h2>&#10003; Signed in to Recall</h2>
    <p>You can close this window.</p>
  </div>
  <script>setTimeout(() => window.close(), 1500);</script>
</body>
</html>
""")


@router.get("/auth/poll")
def auth_poll(state: str):
    """Frontend polls this every 2 s after opening the browser."""
    _cleanup()
    if state not in _pending_jwts:
        return {"ready": False}
    token, _ = _pending_jwts.pop(state)
    return {"ready": True, "token": token}


@router.get("/auth/me")
def auth_me(user: dict = Depends(get_current_user)):
    """Validates JWT and returns user info including name from Supabase."""
    res = get_db().table("users").select("name, email").eq("id", user["sub"]).maybe_single().execute()
    name = ""
    if res and res.data:
        name = res.data.get("name") or ""
    return {"user_id": user["sub"], "email": user.get("email", ""), "name": name}
