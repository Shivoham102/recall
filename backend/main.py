import logging
import os
import sys
import socket
import pathlib
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Ensure backend/ is on sys.path so relative imports work on Vercel
sys.path.insert(0, str(pathlib.Path(__file__).parent))

# Must load .env BEFORE importing any routes — they access os.environ at module level
if getattr(sys, "frozen", False):
    # Running as PyInstaller bundle — load .env from the bundle's extracted dir
    _bundle_dir = pathlib.Path(sys._MEIPASS)
    load_dotenv(_bundle_dir / ".env", override=True)
else:
    load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes.items import router as items_router
from routes.reminders import router as reminders_router
from routes.voice import router as voice_router
from routes.agent_stream import router as agent_stream_router
from routes.chat_title import router as chat_title_router
from routes.cron import router as cron_router
from routes.proactive import router as proactive_router
from routes.google_tokens import router as google_tokens_router
from routes.memory import router as memory_router
from routes.updates import router as updates_router
from routes.suggestions import router as suggestions_router

app = FastAPI(title="Recall Backend")


def _cors_origins() -> list[str]:
    raw = os.environ.get("BACKEND_CORS_ORIGINS", "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    env = (os.environ.get("ENVIRONMENT") or os.environ.get("VERCEL_ENV") or "").lower()
    if env in {"production", "prod"}:
        raise RuntimeError("BACKEND_CORS_ORIGINS must be set in production")
    return ["*"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_stream_router)
app.include_router(chat_title_router)
app.include_router(items_router)
app.include_router(reminders_router)
app.include_router(voice_router)
app.include_router(cron_router)
app.include_router(proactive_router)
app.include_router(google_tokens_router)
app.include_router(memory_router)
app.include_router(updates_router)
app.include_router(suggestions_router)

_sm_enabled = bool(os.environ.get("SUPERMEMORY_API_KEY", "").strip())
logger.info("Supermemory: %s", "enabled" if _sm_enabled else "disabled (no API key)")


@app.get("/health")
def health():
    return {"status": "ok"}


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    import uvicorn

    _requested = int(os.environ.get("BACKEND_PORT", 0))

    for _attempt in range(5):
        port = _requested or _find_free_port()
        try:
            os.environ["BACKEND_PORT"] = str(port)
            print(f"PORT:{port}", flush=True)
            if getattr(sys, "frozen", False):
                uvicorn.run(app, host="127.0.0.1", port=port, reload=False)
            else:
                uvicorn.run("main:app", host="127.0.0.1", port=port, reload=True)
            break
        except OSError as e:
            if "address already in use" in str(e).lower() and not _requested:
                continue
            raise
    else:
        raise RuntimeError("Could not bind to a free port after 5 attempts")
