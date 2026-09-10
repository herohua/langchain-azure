# What this sample demonstrates

A [LangGraph](https://langchain-ai.github.io/langgraph/) **human-in-the-loop**
agent hosted using the **Responses protocol**, modelled as a
**tool-call approval flow**: before any tool runs, the graph pauses
and surfaces the proposed call to the client as the **standard OpenAI
`mcp_approval_request` output item**. Any Responses-API client that
already supports MCP server approvals (e.g. the OpenAI Python SDK)
can drive this agent without code changes.

The host does not emit a substitute `function_call` for this approval.
Ordinary model tool calls keep their normal `function_call` and
`function_call_output` flow.

## How It Works

### Model Integration

The agent uses `langchain_openai.ChatOpenAI` with an Azure bearer token
provider from `DefaultAzureCredential` and an OpenAI-compatible endpoint
from `azure.ai.projects.AIProjectClient`.

See [main.py](main.py) for the full implementation.

### Approval-style HITL

The graph has three nodes:

- `agent` — invokes the chat model.
- `approve_and_call_tool` — when the model emits a tool call, this
  node pauses with `langgraph.types.interrupt(proposed)` where
  `proposed` is the proposed tool name and arguments. On resume, it
  invokes the tool and emits a `ToolMessage`.

When the graph pauses, the host serializes the pending interrupt as one
approval item:

An **`mcp_approval_request`** item with an encoded interrupt id,
   `server_label == "langgraph"`, `name ==
   "__hosted_agent_adapter_interrupt__"`, and `arguments` JSON of the
   form:

   ```json
   {"interrupt_id": "<id>", "value": {"tool": "get_weather", "arguments": {"location": "Seattle"}}}
   ```

State is persisted by a checkpointer keyed by the `conversation.id`, so the
second request continues the paused run from exactly where it left off. Local
runs use `InMemorySaver`; Foundry-hosted runs use `FoundryCheckpointSaver` so
an approval pause survives container replacement.

### Agent Hosting

The agent is hosted using
[`langchain_azure_ai.agents.hosting.ResponsesHostServer`](../../../../libs/azure-ai/langchain_azure_ai/agents/hosting),
which adapts the compiled LangGraph runnable into a REST endpoint
compatible with the OpenAI Responses protocol.

## Running the Agent Host

Follow the instructions in the [Running the Agent Host
Locally](../../README.md#running-the-agent-host-locally) section of the README in the
parent directory to run the agent host.

## Interacting with the agent

> Depending on how you run the agent host, you can invoke the agent
> using `curl` (`Invoke-WebRequest` in PowerShell) or `azd`. Please
> refer to the [parent README](../../README.md) for more details. Use
> this README for sample queries you can send to the agent.

### Step 1 — ask the agent a question that requires a tool

```bash
curl -X POST http://127.0.0.1:8088/responses \
  -H "Content-Type: application/json" \
  -d '{"input": "What is the weather in Seattle?", "conversation": {"id": "demo-hitl-1"}}'
```

The response `output` array will contain:

- a `function_call` item with `name == "get_weather"` — the LLM's own
  tool call. **Do not** reply to this one; the graph closes it itself
  on resume.
- an `mcp_approval_request` item — the OpenAI-standard approval
  prompt. **Copy its `id`** to use in Step 2.

The approval item's `arguments` JSON describes the proposed action:

```json
{"interrupt_id": "<langgraph interrupt id>", "value": {"tool": "get_weather", "arguments": {"location": "Seattle"}}}
```

### Step 2 — approve (or reject)

#### Approve — `mcp_approval_response` with `approve: true`

Post an `mcp_approval_response` whose `approval_request_id` matches
the `id` of the `mcp_approval_request` item:

```bash
curl -X POST http://127.0.0.1:8088/responses \
  -H "Content-Type: application/json" \
  -d '{
    "conversation": {"id": "demo-hitl-1"},
    "input": [
      {
        "type": "mcp_approval_response",
        "approval_request_id": "<id of the mcp_approval_request item>",
        "approve": true
      }
    ]
  }'
```

The host resumes the graph with `{"approve": true}`. The graph decides
whether to invoke `get_weather` and returns a final assistant message.

This is the same wire flow OpenAI's Responses API uses for MCP server
tool approvals — any standard Responses client will already know how
to render it.

#### Reject — `mcp_approval_response` with `approve: false`

```bash
curl -X POST http://127.0.0.1:8088/responses \
  -H "Content-Type: application/json" \
  -d '{
    "conversation": {"id": "demo-hitl-1"},
    "input": [
      {
        "type": "mcp_approval_response",
        "approval_request_id": "<id of the mcp_approval_request item>",
        "approve": false,
        "reason": "user canceled"
      }
    ]
  }'
```

The host resumes the graph with:

```json
{
  "approve": false,
  "reason": "user canceled"
}
```

The graph consumes the pending interrupt on both approval and rejection.
Unknown, expired, or duplicate approval IDs fail before graph execution.

> Compatibility change: `interrupt()` now returns the approval object,
> not the original interrupt value. Inspect `decision["approve"]` and
> optional `decision.get("reason")` in the graph node.

## Deploying the Agent to Foundry

To host the agent on Foundry, follow the instructions in the [Deploying
the Agent to
Foundry](../../README.md#deploying-the-agent-to-foundry) section of
the README in the parent directory.

The deployment descriptors declare Responses protocol `2.0.0`, which supplies
the hosted request context required by `FoundryCheckpointSaver`.
