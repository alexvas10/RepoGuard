import os
import logging
import uuid
from core.config import settings

# --- Configure Vertex AI ---
# We do this BEFORE other imports to ensure the SDK picks up the environment
if settings.GOOGLE_GENAI_USE_VERTEXAI:
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
    os.environ["GOOGLE_CLOUD_PROJECT"] = settings.GCP_PROJECT_ID
    os.environ["GOOGLE_CLOUD_LOCATION"] = settings.GCP_LOCATION

from google.adk.agents import Agent
from google.adk.planners import BuiltInPlanner
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .prompt import root_agent_instruction
from .sub_agents.gatekeeper.agent import gatekeeper_agent
from .sub_agents.guardian.agent import guardian_agent
from .sub_agents.architect.agent import architect_agent

# --- Configure Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

root_agent = Agent(
    model=settings.GEMINI_MODEL,
    name="repoguard_orchestrator",
    description="Orchestrates repository protection and remediation workflows.",
    instruction=root_agent_instruction,
    planner=BuiltInPlanner(
        thinking_config=types.ThinkingConfig(include_thoughts=True, thinking_budget=512)
    ),
    sub_agents=[gatekeeper_agent, guardian_agent, architect_agent],
)

async def invoke_root_agent(prompt_text: str) -> str:
    """Helper to run a prompt through the root orchestrator agent."""
    # Ensure agents are using the latest model from settings
    root_agent.model = settings.GEMINI_MODEL
    gatekeeper_agent.model = settings.GEMINI_MODEL
    guardian_agent.model = settings.GEMINI_MODEL
    architect_agent.model = settings.GEMINI_MODEL

    session_service = InMemorySessionService()
    runner = Runner(agent=root_agent, app_name="repoguard", session_service=session_service)
    
    try:
        session_id = str(uuid.uuid4())
        await session_service.create_session(
            app_name="repoguard",
            user_id="system",
            session_id=session_id,
        )
        message = types.Content(role="user", parts=[types.Part(text=prompt_text)])
        result = "(agent returned empty response)"
        async for event in runner.run_async(
            user_id="system",
            session_id=session_id,
            new_message=message,
        ):
            if event.is_final_response():
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            result = part.text
                            break
        return result
    except Exception as exc:
        logger.error("Error invoking root agent: %s", exc)
        return f"Error: {exc}"

# Export for easier access
__all__ = ["root_agent", "invoke_root_agent"]
