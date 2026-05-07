import os
import sys
import pathlib
from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).parent.parent / ".env")

from line import CallRequest
from line.llm_agent import LlmAgent, LlmConfig, end_call
from line.voice_agent_app import VoiceAgentApp
from tools_line import make_tools
from prompts import SYSTEM_PROMPT_IDENTITY, SYSTEM_PROMPT_TOOL_RULES

SYSTEM = SYSTEM_PROMPT_IDENTITY + "\n\n" + SYSTEM_PROMPT_TOOL_RULES


async def get_agent(env, call_request: CallRequest):
    user_id = call_request.metadata.get("user_id", "") if call_request.metadata else ""
    return LlmAgent(
        model="anthropic/claude-sonnet-4-6",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        tools=[*make_tools(user_id), end_call],
        config=LlmConfig(system_prompt=SYSTEM),
    )


app = VoiceAgentApp(get_agent=get_agent)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    print(f"Starting Line VoiceAgentApp on port {port}", file=sys.stderr)
    app.run(port=port)
