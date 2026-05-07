import os
import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from auth import get_current_user

router = APIRouter()

_AUTH_CALLBACK_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Recall — Signed in</title>
  <style>
    body { background: #08080f; color: #d8d8f0; font-family: system-ui, sans-serif;
           display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
    .card { text-align: center; }
    .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
           background: #00e5ff; margin-right: 8px; }
    h2 { margin: 0 0 8px; font-size: 1.3rem; }
    p  { color: rgba(200,200,230,0.5); font-size: 0.9rem; margin: 0; }
  </style>
</head>
<body>
  <div class="card">
    <h2><span class="dot"></span>Signed in to Recall</h2>
    <p>You can close this window.</p>
  </div>
  <script>
    // Forward tokens from URL hash to the desktop app via deep link
    const hash = window.location.hash.slice(1);
    if (hash) {
      const a = document.createElement('a');
      a.href = 'recall://auth#' + hash;
      a.click();
    }
  </script>
</body>
</html>"""


@router.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback():
    return HTMLResponse(_AUTH_CALLBACK_HTML)


@router.get("/cartesia-token")
async def get_cartesia_token(user: dict = Depends(get_current_user)):
    """Issue a short-lived Cartesia access token for the authenticated user."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.cartesia.ai/access-token",
            headers={
                "Authorization": f"Bearer {os.environ['CARTESIA_API_KEY']}",
                "Cartesia-Version": "2026-03-01",
            },
            json={"expiresIn": 300},
        )
        resp.raise_for_status()
    return {
        "access_token": resp.json()["access_token"],
        "agent_id": os.environ["CARTESIA_AGENT_ID"],
        "user_id": user["sub"],
    }
