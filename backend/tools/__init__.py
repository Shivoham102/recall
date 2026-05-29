from tools.memory import classify_intent, recall_search, recall_update_item, surface_tasks
from tools.filesystem import file_create
from tools.user_memory import remember_user_memory
from tools.goals import track_goal

_google_tools_available = False
try:
    from tools.google_services import (
        gmail_get_updates, surface_cards,
        gmail_find_contact, gmail_find_followup_thread, gmail_get_thread_context,
        gmail_fetch_style_samples, gmail_draft, gmail_reply_draft,
        calendar_list, calendar_create, surface_calendar,
    )
    _google_tools_available = True
except ImportError:
    pass

TOOL_DEFINITIONS = [
    {
        "name": "classify_intent",
        "description": (
            "Classify the intent of the user's message and decide whether to store it. "
            "Call this on every turn where you give a spoken response without doing agentic work."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "intent_type": {
                    "type": "string",
                    "enum": ["task", "blocker", "follow_up", "progress", "note", "query", "update"],
                },
                "should_store": {"type": "boolean"},
                "awaiting_clarification": {
                    "type": "boolean",
                    "description": "Set true when asking the user a clarifying question. Hard-blocks storage on this turn regardless of should_store.",
                },
                "due_hint": {"type": "string", "description": "Natural language due date, or omit if none"},
                "reminder_text": {
                    "type": "string",
                    "description": "What to say when the reminder fires, second-person. Omit if no reminder.",
                },
                "recurrence": {
                    "type": "object",
                    "description": (
                        "Set ONLY for repeating reminders ('every day', 'every weekday', 'every Monday at 9'). "
                        "Omit for one-off reminders. Still set due_hint to the first occurrence."
                    ),
                    "properties": {
                        "freq": {"type": "string", "enum": ["daily", "weekdays", "weekly"]},
                        "time": {"type": "string", "description": "24h wall-clock 'HH:MM', e.g. '18:00'"},
                        "days": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "weekly only: weekday numbers 0=Mon..6=Sun, e.g. [0,2,4]",
                        },
                        "tz": {"type": "string", "description": "IANA tz from the Date context, e.g. 'America/Los_Angeles'"},
                    },
                    "required": ["freq", "time", "tz"],
                },
                "content": {
                    "type": "string",
                    "description": "Clean, concise title to store — always set this when should_store is true. Strip conversational filler ('hey can you', 'please', 'remind me to') and write only the task or reminder itself, e.g. 'Cook dinner' or 'Go for a walk'. In multi-turn sequences, reflect corrections across turns (e.g. user said 'do X on the 14th', now says '10am' → 'do X on the 14th at 10am').",
                },
                "update_only": {
                    "type": "boolean",
                    "description": "Set true for an update/correction turn that should not create a new stored item.",
                },
            },
            "required": ["intent_type", "should_store"],
        },
    },
    {
        "name": "recall_search",
        "description": (
            "Semantically search the user's stored recall items. "
            "Use when the user asks about tasks, to-dos, pending items, blockers, or anything they've previously told you. "
            "After searching, always call surface_tasks with the indices of items you will mention."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "status": {"type": "string", "enum": ["open", "resolved", "all"], "default": "open"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "recall_update_item",
        "description": (
            "Update an existing recall item by id: status, content, reminder text, or due date. "
            "Use for corrections/reschedules to existing tasks/reminders, not for new captures. "
            "Only call this AFTER the user has confirmed the action in conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["resolved", "open", "snoozed"],
                    "description": "Optional. Only set when the user explicitly changes status.",
                },
                "content": {
                    "type": "string",
                    "description": "Optional updated item content. Replaces the existing content.",
                },
                "due_hint": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "Optional updated due date in natural language. Use null only when the user explicitly clears the reminder time.",
                },
                "reminder_text": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "Optional updated text to say when the reminder fires. Use null to clear it.",
                },
                "recurrence": {
                    "anyOf": [{"type": "object"}, {"type": "null"}],
                    "description": (
                        "Optional. Set an object {freq,time,tz,days?} to make this a repeating reminder "
                        "(or change its schedule); due_at is recomputed. Use null to stop repeating."
                    ),
                },
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "surface_tasks",
        "description": (
            "Display specific tasks as visual cards in the UI. "
            "Call this with the indices of the tasks you are about to mention in your spoken response — "
            "only those tasks will be shown as cards. "
            "Always call this after recall_search when you will discuss specific tasks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Indices from the recall_search result, e.g. [0, 1] to show the first two tasks",
                },
            },
            "required": ["indices"],
        },
    },
    {
        "name": "remember_user_memory",
        "description": (
            "Store a durable personal fact, preference, relationship, routine, or long-running project in Supermemory. "
            "Use for explicit 'remember that...' statements and very clear stable personal context. "
            "Do not use for tasks, reminders, one-off commands, ordinary questions, or temporary moods."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "memory": {
                    "type": "string",
                    "description": "Distilled memory to store, e.g. 'User prefers concise updates'.",
                },
                "category": {
                    "type": "string",
                    "enum": ["fact", "preference", "relationship", "routine", "project", "health", "finance", "legal", "location", "relationship_sensitive", "secret"],
                },
                "sensitivity": {
                    "type": "string",
                    "enum": ["normal", "sensitive", "secret"],
                    "description": "Mark sensitive personal context. Secrets/credentials must not be stored.",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Use high for explicit or very clear stable facts.",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "Set true only if the user explicitly confirmed storing sensitive context.",
                },
            },
            "required": ["memory", "category"],
        },
    },
    {
        "name": "track_goal",
        "description": (
            "Record a recurring personal aspiration the user wants to keep up with "
            "('I should call my mom more', 'I want to read more', 'keep up with the gym'). "
            "Recall nudges them if it goes neglected. Do NOT use for one-off tasks or timed reminders "
            "(use classify_intent for those). When you call this, also call classify_intent with should_store: false "
            "so the aspiration isn't double-stored as a note."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_text": {"type": "string", "description": "Concise goal, e.g. 'Call mom', 'Read more', 'Go to the gym'"},
                "cadence": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly"],
                    "description": "How often they want to do it. Default weekly.",
                },
            },
            "required": ["goal_text"],
        },
    },
    {
        "name": "file_create",
        "description": "Create a new text file on the local filesystem. Safe to call immediately — no confirmation needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Filename including extension, e.g. 'notes-2026-04-27.md'"},
                "content": {"type": "string", "description": "File content"},
                "directory": {"type": "string", "description": "Directory path, defaults to ~/Documents"},
            },
            "required": ["filename", "content"],
        },
    },
]

TOOL_REGISTRY: dict = {
    "classify_intent": classify_intent,
    "recall_search": recall_search,
    "recall_update_item": recall_update_item,
    "surface_tasks": surface_tasks,
    "remember_user_memory": remember_user_memory,
    "track_goal": track_goal,
    "file_create": file_create,
}

if _google_tools_available:
    TOOL_DEFINITIONS += [
        {
            "name": "gmail_get_updates",
            "description": (
                "Fetch recent emails from the inbox, filtered to real people (no newsletters or automated mail). "
                "Use whenever the user asks about their email or wants a briefing — including 'any updates from my email', "
                "'check my email', 'any new emails', 'what's in my inbox', 'brief me', 'what's new', 'catch me up', "
                "'morning briefing', or similar. You CAN read email; the only restriction is you cannot send. "
                "Set since_last_checkin=true when the user says 'since last time' or 'since we checked in'. "
                "Emails are returned with numeric indices [0], [1], etc. "
                "After reading the results, call surface_cards with the indices of the emails you will discuss. "
                "Give one spoken sentence per email — lead with the sender's first name and the gist, never read subjects verbatim."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "since_last_checkin": {
                        "type": "boolean",
                        "description": "If true, fetch emails since the last time this tool was called",
                    },
                    "since_hours": {
                        "type": "integer",
                        "description": "Hours to look back. Default 24. Ignored when since_last_checkin is true.",
                    },
                },
            },
        },
        {
            "name": "surface_cards",
            "description": (
                "Display specific emails as visual cards in the UI. "
                "Call this with the indices of the emails you are about to mention in your spoken response — "
                "only those emails will be shown as cards. Never surface emails you don't discuss. "
                "Use source='thread_search' when surfacing follow-up thread candidates."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Indices from the gmail_get_updates result, e.g. [0, 2] to show emails 0 and 2",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["updates", "thread_search"],
                        "description": "updates for inbox updates, thread_search for follow-up thread candidates",
                    },
                },
                "required": ["indices"],
            },
        },
        {
            "name": "gmail_find_contact",
            "description": (
                "Search the user's sent email history to resolve a person's name (and optionally company) "
                "to their email address. Call this whenever you have a name but no email address."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Person's name to search for"},
                    "company": {"type": "string", "description": "Company or domain to narrow the search, optional"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "gmail_find_followup_thread",
            "description": (
                "Find the best existing email thread for a follow-up request. "
                "Search order: sent first, then inbox fallback. "
                "Use this when the user asks to follow up/reply/continue a prior email thread. "
                "If confidence is low or no match, ask one clarification question instead of drafting."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query_text": {"type": "string", "description": "User's follow-up request text"},
                    "recipient_hint": {"type": "string", "description": "Optional recipient/company hint"},
                    "lookback_days": {"type": "integer", "description": "Lookback window in days, default 365"},
                },
                "required": ["query_text"],
            },
        },
        {
            "name": "gmail_get_thread_context",
            "description": (
                "Fetch and summarize recent messages from a specific Gmail thread for context-grounded follow-ups. "
                "Call this after selecting a follow-up thread and before drafting the reply."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string", "description": "Gmail thread id"},
                    "max_messages": {"type": "integer", "description": "How many recent messages to include (default 6)"},
                },
                "required": ["thread_id"],
            },
        },
        {
            "name": "gmail_fetch_style_samples",
            "description": (
                "Load the user's email style profile (weekly precomputed from top sent emails, with fallback refresh). "
                "Always call this before gmail_draft — never skip it. "
                "Use the returned style_guidance and samples to match tone, sentence length, and vocabulary."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Target sample size for fallback refresh, default 10, max 15"},
                },
            },
        },
        {
            "name": "gmail_draft",
            "description": (
                "Save a Gmail draft without sending. "
                "Required sequence before calling: (1) call gmail_find_contact if you don't have the recipient's email address; "
                "(2) call gmail_fetch_style_samples so a style profile is loaded; "
                "(3) write the body matching that style and any explicit user preference (short, concise, detailed, formal, casual) — never draft generically. "
                "Does not send the email."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
        {
            "name": "gmail_reply_draft",
            "description": (
                "Save a Gmail reply draft in an existing thread without sending. "
                "Required sequence before calling: (1) gmail_find_followup_thread, (2) gmail_get_thread_context, "
                "(3) gmail_fetch_style_samples, then draft a context-grounded reply that also honors explicit style requests."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string", "description": "Target Gmail thread id"},
                    "to": {"type": "string", "description": "Reply recipient email"},
                    "subject": {"type": "string", "description": "Optional subject override; usually omitted for thread replies"},
                    "body": {"type": "string"},
                    "in_reply_to": {"type": "string", "description": "Message-ID from latest thread message"},
                    "references": {"type": "string", "description": "References header chain"},
                },
                "required": ["thread_id", "to", "body"],
            },
        },
        {
            "name": "calendar_list",
            "description": (
                "List upcoming Google Calendar events. "
                "Use when the user asks what's on their calendar, their schedule, upcoming meetings, "
                "or as part of a morning briefing alongside gmail_get_updates and recall_search. "
                "After listing, call surface_calendar with the indices of events worth highlighting."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "default": 7},
                },
            },
        },
        {
            "name": "surface_calendar",
            "description": (
                "Render calendar events as visual cards in the UI. "
                "Call after calendar_list with the indices of ALL events you mention verbally. "
                "Every event you name in your spoken response MUST appear here — no exceptions. "
                "Use for morning briefings or any time you discuss specific upcoming events."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Indices into the events returned by the most recent calendar_list call",
                    },
                },
                "required": ["indices"],
            },
        },
        {
            "name": "calendar_create",
            "description": (
                "Create a Google Calendar event. Only call this AFTER the user has confirmed the details."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start_time": {"type": "string", "description": "ISO 8601 datetime, e.g. 2026-04-28T15:00:00"},
                    "end_time": {"type": "string", "description": "ISO 8601 datetime"},
                    "description": {"type": "string"},
                    "attendees": {"type": "array", "items": {"type": "string"}, "description": "List of attendee emails"},
                },
                "required": ["title", "start_time", "end_time"],
            },
        },
    ]
    TOOL_REGISTRY.update({
        "gmail_get_updates": gmail_get_updates,
        "surface_cards": surface_cards,
        "gmail_find_contact": gmail_find_contact,
        "gmail_find_followup_thread": gmail_find_followup_thread,
        "gmail_get_thread_context": gmail_get_thread_context,
        "gmail_fetch_style_samples": gmail_fetch_style_samples,
        "gmail_draft": gmail_draft,
        "gmail_reply_draft": gmail_reply_draft,
        "calendar_list": calendar_list,
        "calendar_create": calendar_create,
        "surface_calendar": surface_calendar,
    })

# Proactive (headless) tool subset — excludes write/draft/file tools.
# Surface tools included so the LLM can judge and selectively render cards.
_PROACTIVE_NAMES = {
    "recall_search", "recall_update_item",
    "gmail_get_updates", "gmail_find_contact", "gmail_find_followup_thread",
    "gmail_get_thread_context", "calendar_list",
    "surface_cards", "surface_calendar", "surface_tasks",
}
PROACTIVE_TOOL_DEFINITIONS = [t for t in TOOL_DEFINITIONS if t["name"] in _PROACTIVE_NAMES]
PROACTIVE_TOOL_REGISTRY: dict = {k: v for k, v in TOOL_REGISTRY.items() if k in _PROACTIVE_NAMES}
