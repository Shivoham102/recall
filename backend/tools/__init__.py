from tools.memory import classify_intent, recall_search, recall_update_item
from tools.filesystem import file_create

_google_tools_available = False
try:
    from tools.google_services import (
        gmail_get_updates, surface_cards,
        gmail_find_contact, gmail_fetch_style_samples,
        gmail_draft, calendar_list, calendar_create,
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
        "description": "Semantically search the user's stored recall items. Use this to find context before taking actions.",
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
    "file_create": file_create,
}

if _google_tools_available:
    TOOL_DEFINITIONS += [
        {
            "name": "gmail_get_updates",
            "description": (
                "Fetch recent emails from the inbox, filtered to real people (no newsletters or automated mail). "
                "Use when the user asks for an update, a briefing, or 'what's new'. "
                "Set since_last_checkin=true when the user says 'since we last checked in' or similar. "
                "Emails are returned with numeric indices [0], [1], etc. "
                "After reading the results, call surface_cards with the indices of the emails you will discuss."
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
                "only those emails will be shown as cards. Never surface emails you don't discuss."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Indices from the gmail_get_updates result, e.g. [0, 2] to show emails 0 and 2",
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
            "name": "gmail_fetch_style_samples",
            "description": (
                "Fetch a sample of the user's recent sent emails to understand their writing style. "
                "Call this before drafting any email so you can match their tone, length, and vocabulary exactly."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Number of emails to fetch, default 8, max 15"},
                },
            },
        },
        {
            "name": "gmail_draft",
            "description": (
                "Save a Gmail draft without sending. Use this to show the user the draft content. "
                "Safe to call immediately — does not send the email."
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
            "name": "calendar_list",
            "description": "List upcoming Google Calendar events.",
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
        "gmail_fetch_style_samples": gmail_fetch_style_samples,
        "gmail_draft": gmail_draft,
        "calendar_list": calendar_list,
        "calendar_create": calendar_create,
    })
