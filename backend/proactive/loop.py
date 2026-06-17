"""
Headless agentic loop for proactive jobs.

Mirrors agent.py's run_agentic_loop but with no SSE streaming.
Collects surfaced cards from surface_* tool results and returns them
alongside the final text response.
"""
import asyncio
import json
import os
from dataclasses import dataclass, field

from anthropic import AsyncAnthropic
from anthropic.types import TextBlock, ToolUseBlock

from google_auth import GoogleReauthRequired

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


@dataclass
class LoopResult:
    text: str = ""
    email_cards: list[dict] = field(default_factory=list)
    calendar_cards: list[dict] = field(default_factory=list)
    task_cards: list[dict] = field(default_factory=list)
    reauth_required: bool = False


async def run_headless_loop(
    system_prompt: str,
    user_message: str,
    tool_definitions: list[dict],
    tool_registry: dict,
    model: str = "claude-haiku-4-5-20251001",
    max_iterations: int = 8,
    timeout_per_tool: float = 20.0,
    overall_timeout: float = 240.0,
) -> LoopResult:
    """
    Run a tool-use loop to completion and return text + surfaced cards.
    Cards are populated by intercepting surface_cards / surface_calendar / surface_tasks results.
    """
    return await asyncio.wait_for(
        _run_loop(system_prompt, user_message, tool_definitions, tool_registry, model, max_iterations, timeout_per_tool),
        timeout=overall_timeout,
    )


async def _run_loop(
    system_prompt: str,
    user_message: str,
    tool_definitions: list[dict],
    tool_registry: dict,
    model: str,
    max_iterations: int,
    timeout_per_tool: float,
) -> LoopResult:
    client = _get_client()
    history: list[dict] = [{"role": "user", "content": user_message}]
    result = LoopResult()

    for _ in range(max_iterations):
        response = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_prompt,
            tools=tool_definitions,
            tool_choice={"type": "auto"},
            messages=history,
        )

        # Collect text from this response
        for block in response.content:
            if isinstance(block, TextBlock) and block.text.strip():
                result.text = block.text.strip()

        if response.stop_reason == "end_turn":
            history.append({"role": "assistant", "content": response.content})
            break

        if response.stop_reason == "tool_use":
            tool_use_blocks = [b for b in response.content if isinstance(b, ToolUseBlock)]
            history.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in tool_use_blocks:
                try:
                    tool_fn = tool_registry.get(block.name)
                    if tool_fn is None:
                        res = {"summary": f"Unknown tool: {block.name}", "error": True}
                    else:
                        res = await asyncio.wait_for(tool_fn(block.input), timeout=timeout_per_tool)
                except asyncio.TimeoutError:
                    res = {"summary": f"{block.name} timed out", "error": True}
                except GoogleReauthRequired:
                    res = {"summary": "Google reconnect required", "error": True, "reauth_required": True}
                except Exception as exc:
                    print(f"[proactive_loop] {block.name} failed: {exc}")
                    res = {"summary": f"{block.name} unavailable", "error": True}

                if res.get("reauth_required"):
                    result.reauth_required = True

                # Intercept surface tool outputs to populate cards
                if block.name == "surface_cards" and not res.get("error"):
                    result.email_cards = res.get("items_data", [])
                elif block.name == "surface_calendar" and not res.get("error"):
                    result.calendar_cards = res.get("items_data", [])
                elif block.name == "surface_tasks" and not res.get("error"):
                    result.task_cards = res.get("items_data", [])

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(res),
                })

            history.append({"role": "user", "content": tool_results})

    return result
