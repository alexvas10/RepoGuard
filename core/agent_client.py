import asyncio
import json
import logging
import os
import uuid
import httpx
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types, errors as genai_errors
from .config import settings

# Limit concurrent Vertex AI calls to 2 — prevents model errors under load when
# multiple Gatekeeper + Guardian agents fire simultaneously.
_SEMAPHORE = asyncio.Semaphore(2)
_TRANSIENT_ERRORS = (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException)

logger = logging.getLogger(__name__)

# Point ADK at Vertex AI using the same project/location as the rest of the app.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.GCP_PROJECT_ID)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.GCP_LOCATION)

# Sentinel values — truthy flag passed to invoke_agent to enable MCP tools.
GATEKEEPER_TOOLS = True
GUARDIAN_TOOLS = True

# System prompt injected when tools are active. Placed in `instruction` (the ADK
# system prompt slot) so Gemini treats it as a persistent behavioral rule rather
# than a conversational hint it can choose to ignore.
_TOOL_INSTRUCTION = (
    "You are a RepoGuard agent. You MUST call the create_merge_request_note tool "
    "to post your analysis as a comment on the GitLab merge request. "
    "Calling this tool is mandatory — do not skip it or respond with text only."
)


# ---------------------------------------------------------------------------
# MCP client — handles JSON-RPC over Streamable HTTP to the GitLab MCP sidecar
# ---------------------------------------------------------------------------

class MCPClient:
    """Thin JSON-RPC client for the @yoda.digital/gitlab-mcp-server sidecar."""

    def __init__(self):
        self.base_url = settings.MCP_SERVER_URL
        self._session_id: str | None = None

    async def _post(self, payload: dict, session_id: str | None = None) -> tuple[dict, "httpx.Response"]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{self.base_url}/mcp", json=payload, headers=headers)
        resp.raise_for_status()
        if not resp.content:
            return {}, resp
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            for line in resp.text.splitlines():
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str and data_str != "[DONE]":
                        return json.loads(data_str), resp
            return {}, resp
        return resp.json(), resp

    async def initialize(self) -> str | None:
        data, resp = await self._post({
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "repoguard", "version": "1.0"},
            },
        })
        session_id = resp.headers.get("mcp-session-id")
        await self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            session_id=session_id,
        )
        return session_id

    async def call_tool(self, name: str, arguments: dict) -> dict:
        if self._session_id is None:
            self._session_id = await self.initialize()
        data, _ = await self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            session_id=self._session_id,
        )
        if "error" in data:
            raise RuntimeError(f"MCP error calling {name}: {data['error']}")
        return data.get("result", {})


# ---------------------------------------------------------------------------
# Agent runner — Google Cloud Agent Builder (ADK) for all paths
# ---------------------------------------------------------------------------

async def invoke_agent(prompt: str, tools=None) -> str:
    """
    Run a prompt through Gemini via Google Cloud Agent Builder (ADK).

    tools=None  → text-only agent (forensic analysis)
    tools=True  → agent with create_merge_request_note tool (Gatekeeper + Guardian)

    The `instruction` field (ADK system prompt) enforces mandatory tool use when
    tools are active, making Gemini treat it as a persistent rule rather than a hint.
    """
    mcp = MCPClient() if tools else None
    # _tool_called[0] is reset each attempt and flipped True when Gemini calls the tool.
    _tool_called = [False]

    if mcp:
        async def create_merge_request_note(
            project_id: str,
            merge_request_iid: int,
            body: str,
        ) -> str:
            """Post a comment on a GitLab merge request."""
            _tool_called[0] = True
            if "Powered by RepoGuard" not in body:
                body = body + "\n\n---\n*Powered by RepoGuard · Gemini 2.5 Flash*"
            result = await mcp.call_tool("create_merge_request_note", {
                "project_id": project_id,
                "merge_request_iid": merge_request_iid,
                "body": body,
            })
            logger.info("[MCP] create_merge_request_note → %s", result)
            return str(result)

    _max_attempts = 5
    result = "(agent returned empty response)"
    _adk_tools = [create_merge_request_note] if mcp else []
    for attempt in range(_max_attempts):
        _tool_called[0] = False
        adk_tools = _adk_tools

        agent = LlmAgent(
            model=settings.GEMINI_MODEL,
            name="repoguard",
            instruction=_TOOL_INSTRUCTION if adk_tools else "",
            tools=adk_tools,
            generate_content_config=types.GenerateContentConfig(
                temperature=0,
                max_output_tokens=2048,
            ),
        )
        session_service = InMemorySessionService()
        runner = Runner(agent=agent, app_name="repoguard", session_service=session_service)

        try:
            async with _SEMAPHORE:
                session_id = str(uuid.uuid4())
                await session_service.create_session(
                    app_name="repoguard",
                    user_id="system",
                    session_id=session_id,
                )
                message = types.Content(role="user", parts=[types.Part(text=prompt)])
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

            # When tools are required, verify Gemini actually called the tool.
            # If it skipped the tool call (returns text only under load), retry.
            if mcp and not _tool_called[0]:
                if attempt < _max_attempts - 1:
                    logger.warning(
                        "[invoke_agent] Gemini skipped required tool call (attempt %d/%d), retrying in 3s",
                        attempt + 1, _max_attempts,
                    )
                    await asyncio.sleep(3)
                    continue
                else:
                    logger.error(
                        "[invoke_agent] Gemini never called required tool after %d attempts — returning text result",
                        _max_attempts,
                    )
            return result

        except Exception as exc:
            is_quota = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
            is_transient = isinstance(exc, _TRANSIENT_ERRORS)
            if not is_quota and not is_transient:
                raise
            if attempt == _max_attempts - 1:
                logger.error(
                    "[invoke_agent] failed after %d attempts, returning partial result: %s",
                    _max_attempts, exc,
                )
                return result
            # 429 quota resets within ~15s; transient network errors use exponential backoff.
            wait = 15 if is_quota else 2 ** attempt
            logger.warning(
                "[invoke_agent] %s (attempt %d/%d), retrying in %ds",
                "quota exhausted" if is_quota else "transient error",
                attempt + 1, _max_attempts, wait,
            )
            await asyncio.sleep(wait)

    return "(agent returned empty response)"
