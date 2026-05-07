from fastapi import APIRouter
from fastapi.responses import HTMLResponse

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
