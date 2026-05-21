import context
from supermemory_client import SENSITIVE_CATEGORIES, add_user_memory


async def remember_user_memory(inp: dict) -> dict:
    user_id = context.current_user_id.get("")
    if not user_id:
        return {"summary": "No user scope for memory", "remembered": False, "error": True}

    memory = str(inp.get("memory") or "").strip()
    category = str(inp.get("category") or "fact").strip().lower()
    sensitivity = str(inp.get("sensitivity") or "normal").strip().lower()

    if not memory:
        return {"summary": "No memory provided", "remembered": False}

    if category == "secret" or sensitivity == "secret":
        return {"summary": "I cannot store secrets or credentials.", "remembered": False, "error": True}

    if category in SENSITIVE_CATEGORIES and not inp.get("confirmed"):
        return {
            "summary": "Sensitive memory requires confirmation first",
            "remembered": False,
            "requires_confirmation": True,
        }

    try:
        result = await add_user_memory(
            user_id=user_id,
            memory=memory,
            category=category,
            metadata={
                "sensitivity": sensitivity,
                "confidence": str(inp.get("confidence", "high")),
            },
        )
    except Exception as exc:
        return {"summary": "Memory unavailable; continuing without saving.", "remembered": False, "error": True, "status": str(exc)}
    if result.get("ok"):
        return {
            "summary": "Remembered.",
            "remembered": True,
            "status": result.get("status"),
            "custom_id": result.get("custom_id"),
        }
    return {
        "summary": "Memory unavailable; continuing without saving.",
        "remembered": False,
        "status": result.get("status"),
    }
