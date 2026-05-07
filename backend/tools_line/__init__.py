from tools_line.memory import (
    make_recall_search,
    make_recall_update_item,
    make_surface_tasks,
    make_classify_intent,
)

_google_available = False
try:
    from tools_line.google_services import (
        make_gmail_get_updates,
        make_surface_cards,
        make_gmail_find_contact,
        make_gmail_fetch_style_samples,
        make_gmail_draft,
        make_calendar_list,
        make_calendar_create,
    )
    _google_available = True
except ImportError:
    pass


def make_tools(user_id: str) -> list:
    """Return Line-compatible tools bound to user_id for this call."""
    # recall_search and surface_tasks share the same fetch list so surface_tasks
    # can look up items by index from the most recent search result.
    recall_search_tool = make_recall_search(user_id)
    tools = [
        make_classify_intent(user_id),
        recall_search_tool,
        make_recall_update_item(user_id),
        make_surface_tasks(user_id, recall_search_tool._last_fetch),
    ]
    if _google_available:
        gmail_updates_tool = make_gmail_get_updates(user_id)
        tools += [
            gmail_updates_tool,
            make_surface_cards(user_id, gmail_updates_tool._last_fetch),
            make_gmail_find_contact(user_id),
            make_gmail_fetch_style_samples(user_id),
            make_gmail_draft(user_id),
            make_calendar_list(user_id),
            make_calendar_create(user_id),
        ]
    return tools
