"""Sample 08 - Human-in-the-loop over the Responses API.

This sample demonstrates an **approval-style HITL** flow using LangGraph's
``langgraph.types.interrupt``: before any tool runs, the graph pauses and
asks the client to approve the proposed tool call. This ordinary interrupt is
serialized as a ``function_call`` and resumed with
``function_call_output``. MCP approvals use their separate OpenAI
``mcp_approval_request`` / ``mcp_approval_response`` protocol.

State is persisted by a checkpointer keyed by the ``conversation`` id, so the
second request continues the paused run. Local runs use ``InMemorySaver``;
Foundry-hosted runs use ``FoundryCheckpointSaver``.

Required environment variables (set in ``.env`` or your shell):

    FOUNDRY_PROJECT_ENDPOINT        e.g. https://<acct>.services.ai.azure.com/api/projects/<proj>
    AZURE_AI_MODEL_DEPLOYMENT_NAME  e.g. gpt-4o   (defaults to "gpt-4o")
    PORT                            optional, defaults to 8088

Run::

    az login
    cp .env.example .env  # then edit the values
    python main.py

Then in another terminal — ask the agent a question that requires a
tool::

    curl -X POST http://127.0.0.1:8088/responses -H 'Content-Type: application/json' -d '{"input":"What is the weather in Seattle?","conversation":{"id":"demo-hitl-1"}}'

The response ``output`` contains a reserved ``function_call`` whose
``arguments`` describe the proposed tool call::

    {"interrupt_id": "<id>", "value": {"tool": "get_weather", "arguments": {"location": "Seattle"}}}

Resume it through ``function_call_output``, targeting its ``call_id``::

    curl -X POST http://127.0.0.1:8088/responses -H 'Content-Type: application/json' -d '{"conversation":{"id":"demo-hitl-1"},"input":[{"type":"function_call_output","call_id":"<id>","output":"{\\"resume\\": {\\"tool\\":\\"get_weather\\",\\"arguments\\":{\\"location\\":\\"Vancouver\\"}}}"}]}'
"""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Any

from azure.ai.agentserver.core import AgentConfig
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from langchain_azure_ai.agents.hosting import (
    FoundryCheckpointSaver,
    ResponsesHostServer,
)
from langchain_azure_ai.callbacks.tracers import enable_auto_tracing

load_dotenv()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def get_weather(
    location: Annotated[str, "City and country, e.g. 'Seattle, US'."],
) -> str:
    """Return a fake weather snapshot for the given location."""
    return f"It's sunny and 22C in {location}."


_TOOLS_BY_NAME = {"get_weather": get_weather}


# ---------------------------------------------------------------------------
# Chat model
# ---------------------------------------------------------------------------


_AZURE_AI_SCOPE = "https://ai.azure.com/.default"


def _build_chat_model() -> ChatOpenAI:
    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"].rstrip("/")
    deployment = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")
    credential = DefaultAzureCredential()
    project = AIProjectClient(endpoint=project_endpoint, credential=credential)
    openai_client = project.get_openai_client()
    token_provider = get_bearer_token_provider(credential, _AZURE_AI_SCOPE)

    return ChatOpenAI(
        model=deployment,
        base_url=str(openai_client.base_url),
        api_key=token_provider,
    )


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def _build_graph(checkpointer: BaseCheckpointSaver[Any]) -> "object":
    llm = _build_chat_model()
    tools = list(_TOOLS_BY_NAME.values())
    model = llm.bind_tools(tools)

    def call_model(state: MessagesState) -> dict:
        return {"messages": [model.invoke(state["messages"])]}

    def approve_and_call_tool(state: MessagesState) -> dict:
        """Pause for human approval, then execute the proposed tool call."""
        last = state["messages"][-1]
        tool_call = last.tool_calls[0]  # type: ignore[attr-defined]
        proposed: dict[str, Any] = {
            "tool": tool_call["name"],
            "arguments": tool_call["args"],
        }

        # Use the client-supplied resume payload when it overrides the proposal.
        approved: Any = interrupt(proposed)
        if not isinstance(approved, dict) or "tool" not in approved:
            approved = proposed

        tool_fn = _TOOLS_BY_NAME[approved["tool"]]
        result = tool_fn.invoke(approved.get("arguments") or {})
        return {
            "messages": [ToolMessage(content=str(result), tool_call_id=tool_call["id"])]
        }

    def should_continue(state: MessagesState) -> str:
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return END
        return "approve_and_call_tool"

    workflow = StateGraph(MessagesState)
    workflow.add_node("agent", call_model)
    workflow.add_node("approve_and_call_tool", approve_and_call_tool)
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        path_map=["approve_and_call_tool", END],
    )
    workflow.add_edge("approve_and_call_tool", "agent")
    return workflow.compile(checkpointer=checkpointer)


async def main() -> None:
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        enable_auto_tracing()
    else:
        enable_auto_tracing(auto_configure_azure_monitor=True)

    if AgentConfig.from_env().is_hosted:
        checkpointer = FoundryCheckpointSaver(user_isolation=False)
    else:
        checkpointer = InMemorySaver()
    async with checkpointer:
        graph = _build_graph(checkpointer)
        await ResponsesHostServer(graph).run_async(
            port=int(os.environ.get("PORT", "8088"))
        )


if __name__ == "__main__":
    asyncio.run(main())
