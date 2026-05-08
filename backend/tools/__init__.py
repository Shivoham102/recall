from tools.memory import classify_intent, recall_search, recall_update_item, surface_tasks
from tools.filesystem import file_create

_google_tools_available = False
try:
    from tools.google_services import (
        gmail_get_updates, surface_cards,
        gmail_find_contact, gmail_find_followup_thread, gmail_get_thread_context,
        gmail_fetch_style_samples, gmail_draft, gmail_reply_draft,
        calendar_list, calendar_create,
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
                "due_hint": {"type": "string", "description": "Natural language due date, or omit if none"},
                "reminder_text": {
                    "type": "string",
                    "description": "What to say when the reminder fires, second-person. Omit if no reminder.",
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
            "Mark a recall item as resolved, snoozed, or update its due date. "
            "Only call this AFTER the user has confirmed the action in conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string"},
                "status": {"type": "string", "enum": ["resolved", "open", "snoozed"]},
                "due_hint": {"type": "string", "description": "Updated due date in natural language, optional"},
            },
            "required": ["item_id", "status"],
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
                "or as part of a morning briefing alongside gmail_get_updates and recall_search."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "default": 7},
                },
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
    })
