from contextvars import ContextVar

# Set at the start of each authenticated request; read by rag.py, session_store.py, google_services.py
current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
current_user_tz: ContextVar[str] = ContextVar("current_user_tz", default="UTC")
current_style_ready: ContextVar[bool] = ContextVar("current_style_ready", default=False)
current_style_profile: ContextVar[dict] = ContextVar("current_style_profile", default={})
current_draft_preferences: ContextVar[dict] = ContextVar("current_draft_preferences", default={})

# Per-request scratch for "fetch then reference by index" tools (recall_search → surface_tasks,
# gmail_get_updates / gmail_find_followup_thread → surface_cards, calendar_list → surface_calendar).
# These were module-level globals — shared across users on warm serverless instances, so one user's
# fetch could be indexed by another user's surface_* call. ContextVars are copied per asyncio task,
# so each request is isolated. ALWAYS .set(...) a fresh list; never mutate the value in place.
current_task_fetch: ContextVar[list] = ContextVar("current_task_fetch", default=[])
current_email_fetch: ContextVar[list] = ContextVar("current_email_fetch", default=[])
current_thread_candidate_fetch: ContextVar[list] = ContextVar("current_thread_candidate_fetch", default=[])
current_calendar_fetch: ContextVar[list] = ContextVar("current_calendar_fetch", default=[])
