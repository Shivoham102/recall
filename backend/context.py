from contextvars import ContextVar

# Set at the start of each authenticated request; read by rag.py, session_store.py, google_services.py
current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
