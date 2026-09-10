"""HumanInTheLoopMiddleware over the Responses API."""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Any

from azure.ai.agentserver.core import AgentConfig
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_azure_ai.agents.hosting import (
    FoundryCheckpointSaver,
    ResponsesHostServer,
)
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

load_dotenv()


@tool
def get_weather(
    location: Annotated[str, "City and country, e.g. 'Seattle, US'."],
) -> str:
    """Return a fake weather snapshot for a location."""
    return f"It's sunny and 22C in {location}."


@tool
def get_local_time(
    location: Annotated[str, "City and country, e.g. 'Seattle, US'."],
) -> str:
    """Return a fake local time for a location."""
    return f"The local time in {location} is 09:30."


_AZURE_AI_SCOPE = "https://ai.azure.com/.default"


def build_model() -> ChatOpenAI:
    """Create a Foundry OpenAI-compatible chat model."""
    credential = DefaultAzureCredential()
    project = AIProjectClient(
        endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"].rstrip("/"),
        credential=credential,
    )
    return ChatOpenAI(
        model=os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o"),
        base_url=str(project.get_openai_client().base_url),
        api_key=get_bearer_token_provider(credential, _AZURE_AI_SCOPE),
    )


def build_agent(checkpointer: BaseCheckpointSaver[Any]) -> object:
    """Create an agent whose tool calls require explicit decisions."""
    tools = [get_weather, get_local_time]
    return create_agent(
        model=build_model(),
        tools=tools,
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on={
                    tool.name: {
                        "allowed_decisions": ["approve", "reject", "edit", "respond"]
                    }
                    for tool in tools
                }
            )
        ],
        checkpointer=checkpointer,
    )


async def main() -> None:
    """Start the hosted agent."""
    checkpointer: BaseCheckpointSaver[Any]
    if AgentConfig.from_env().is_hosted:
        checkpointer = FoundryCheckpointSaver(user_isolation=False)
    else:
        checkpointer = InMemorySaver()

    async with checkpointer:
        await ResponsesHostServer(build_agent(checkpointer)).run_async(
            port=int(os.environ.get("PORT", "8088"))
        )


if __name__ == "__main__":
    asyncio.run(main())
