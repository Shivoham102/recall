from db import get_db


def _serialize(history: list[dict]) -> list:
    result = []
    for turn in history:
        content = turn["content"]
        if isinstance(content, list):
            content = [
                b.model_dump() if hasattr(b, "model_dump") else b
                for b in content
            ]
        result.append({"role": turn["role"], "content": content})
    return result


def save_session(session_id: str, history: list[dict]) -> None:
    try:
        get_db().table("sessions").upsert(
            {"session_id": session_id, "history": _serialize(history)},
            on_conflict="session_id",
        ).execute()
    except Exception:
        pass  # table not yet created — persistence degrades gracefully


def load_session(session_id: str) -> list[dict]:
    try:
        res = (
            get_db().table("sessions")
            .select("history")
            .eq("session_id", session_id)
            .maybe_single()
            .execute()
        )
        if res is None or res.data is None:
            return []
        return res.data.get("history", [])
    except Exception:
        return []  # table not yet created — start fresh
