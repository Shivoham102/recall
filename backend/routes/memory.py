from fastapi import APIRouter, Depends, HTTPException
from auth import get_current_user
from supermemory_client import clear_user_memory, get_memory_tab_data

router = APIRouter()


@router.get("/memory/profile")
async def memory_profile(user: dict = Depends(get_current_user)):
    return await get_memory_tab_data(user["sub"])


@router.delete("/memory/clear")
async def memory_clear(user: dict = Depends(get_current_user)):
    result = await clear_user_memory(user["sub"])
    if result.get("ok") or result.get("status") in {"disabled", "unconfigured"}:
        return result
    raise HTTPException(status_code=502, detail=result.get("error") or "Memory clear failed")
