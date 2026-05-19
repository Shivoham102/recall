from contextvars import ContextVar

# Set at the start of each authenticated request; read by rag.py, session_store.py, google_services.py
current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
current_user_tz: ContextVar[str] = ContextVar("current_user_tz", default="UTC")
current_style_ready: ContextVar[bool] = ContextVar("current_style_ready", default=False)
current_style_profile: ContextVar[dict] = ContextVar("current_style_profile", default={})
current_draft_preferences: ContextVar[dict] = ContextVar("current_draft_preferences", default={})
