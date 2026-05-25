from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from auth import get_current_user
from chat_title import suggest_title_from_first_message

router = APIRouter()


class SuggestTitleBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


@router.post("/capture/suggest-title")
async def capture_suggest_title(body: SuggestTitleBody, user: dict = Depends(get_current_user)):
    _ = user
    try:
        title = await suggest_title_from_first_message(body.text)
    except Exception as exc:
        return {"title": None, "error": str(exc)}
    return {"title": title}
