from fastapi import APIRouter, Depends, HTTPException
from auth import get_current_user
from supermemory_client import (
    clear_user_memory,
    delete_user_memory,
    get_memory_tab_data,
    list_user_memories,
)

router = APIRouter()


@router.get("/memory/profile")
async def memory_profile(user: dict = Depends(get_current_user)):
    return await get_memory_tab_data(user["sub"])


@router.get("/memory/items")
async def memory_items(user: dict = Depends(get_current_user)):
    return await list_user_memories(user["sub"])


@router.delete("/memory/items/{doc_id}")
async def memory_item_delete(doc_id: str, user: dict = Depends(get_current_user)):
    result = await delete_user_memory(user["sub"], doc_id)
    if result.get("ok"):
        return result
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Memory not found")
    if result.get("status") in {"disabled", "unconfigured"}:
        return result
    raise HTTPException(status_code=502, detail=result.get("error") or "Memory delete failed")


@router.delete("/memory/clear")
async def memory_clear(user: dict = Depends(get_current_user)):
    result = await clear_user_memory(user["sub"])
    if result.get("ok") or result.get("status") in {"disabled", "unconfigured"}:
        return result
    raise HTTPException(status_code=502, detail=result.get("error") or "Memory clear failed")
