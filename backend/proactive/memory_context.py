from supermemory_client import PROACTIVE_TIMEOUT_SECONDS, format_memory_context, get_user_profile


async def get_proactive_memory_context(user_id: str, query: str) -> str:
    profile = await get_user_profile(
        user_id,
        query=query,
        timeout=PROACTIVE_TIMEOUT_SECONDS,
        allow_stale=True,
    )
    return format_memory_context(profile)
