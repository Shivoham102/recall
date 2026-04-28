import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from routes.capture import router as capture_router
from routes.query import router as query_router
from routes.items import router as items_router
from routes.reminders import router as reminders_router
from routes.agent_stream import router as agent_stream_router
from stt import get_model

load_dotenv()

app = FastAPI(title="Recall Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "tauri://localhost"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(capture_router)
app.include_router(query_router)
app.include_router(items_router)
app.include_router(reminders_router)
app.include_router(agent_stream_router)


@app.on_event("startup")
async def startup():
    get_model()  # warm faster-whisper so first request isn't slow


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("BACKEND_PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
