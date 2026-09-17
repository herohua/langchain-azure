"""Sample 03 - Invocations API with multi-turn session continuity.

Hosts a ``create_agent`` graph (compiled with ``MemorySaver``) as
the Azure AI Invocations API. Multi-turn conversations work
automatically: the resolved ``agent_session_id`` is forwarded to the
graph as ``RunnableConfig.configurable.thread_id``, so the checkpointer
keeps each session's history in memory.

Required environment variables (set in `.env` or your shell):

    FOUNDRY_PROJECT_ENDPOINT        e.g. https://<acct>.services.ai.azure.com/api/projects/<proj>
    AZURE_AI_MODEL_DEPLOYMENT_NAME  e.g. gpt-4o   (defaults to "gpt-4o")
    PORT                            optional, defaults to 8088

Run::

    az login
    cp .env.example .env  # then edit the values
    python main.py

Then in another terminal:

    # Turn 1 - capture the x-agent-session-id response header
    curl -i -X POST http://127.0.0.1:8088/invocations -H 'Content-Type: application/json' -d '{"message":"My name is Alice.","metadata":{"tenant":"contoso"}}'

    # Turn 2 - reuse the same session id
    curl -X POST 'http://127.0.0.1:8088/invocations?agent_session_id=<id>' -H 'Content-Type: application/json' -d '{"message":"What is my name?"}'

    # Streaming variant
    curl -N -X POST http://127.0.0.1:8088/invocations -H 'Content-Type: application/json' -d '{"message":"Count to 5.","stream":true}'
"""
from __future__ import annotations

import os
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import before_model
from langchain_azure_ai.agents.hosting import InvocationsHostServer
from langchain_azure_ai.callbacks.tracers import enable_auto_tracing
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_config
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from starlette.requests import Request

load_dotenv()


_AZURE_AI_SCOPE = "https://ai.azure.com/.default"


@before_model
def print_invocation_metadata(state: Any, runtime: Any) -> None:
    """Print metadata passed to this graph invocation."""
    del state, runtime
    metadata = get_config().get("configurable", {}).get(
        "invocation_metadata", {}
    )
    print(f"Invocation metadata: {metadata}")


class MetadataInvocationsHostServer(InvocationsHostServer):
    """Pass optional request metadata to the hosted graph."""

    async def parse_request(
        self, request: Request
    ) -> tuple[str | list[dict[str, Any]], bool]:
        """Validate and retain optional invocation metadata."""
        message, stream = await super().parse_request(request)
        body = await request.json()
        metadata = body.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError(  # noqa: TRY004
                "Request body field 'metadata' must be an object."
            )
        request.state.invocation_metadata = metadata
        return message, stream

    def build_runnable_config(self, request: Request) -> RunnableConfig:
        """Add invocation metadata to the graph's run configuration."""
        config = super().build_runnable_config(request)
        config.setdefault("configurable", {})["invocation_metadata"] = (
            request.state.invocation_metadata
        )
        return config


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


def main() -> None:
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        enable_auto_tracing()
    else:
        enable_auto_tracing(auto_configure_azure_monitor=True)

    # MemorySaver keys conversations by thread_id, which the host wires
    # from agent_session_id. Replace with a durable checkpointer
    # (Redis, Cosmos, etc.) for production.
    graph = create_agent(
        _build_chat_model(),
        tools=[],
        middleware=[print_invocation_metadata],
        checkpointer=MemorySaver(),
    )
    port = int(os.environ.get("PORT", "8088"))
    MetadataInvocationsHostServer(graph).run(port=port)


if __name__ == "__main__":
    main()
