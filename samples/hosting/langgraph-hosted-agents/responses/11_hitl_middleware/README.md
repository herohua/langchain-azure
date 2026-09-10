# Human-in-the-loop middleware

This sample hosts a LangChain `create_agent` graph with two tools guarded by
`HumanInTheLoopMiddleware`. It is separate from the
[ordinary LangGraph `interrupt()` sample](../08_hitl/) because the two pause
mechanisms accept different resume values.

## Channel behavior

The host exposes each pending interrupt as paired `mcp_approval_request` and
reserved `function_call` output items.

The shortcut approval channel retains the same behavior for every LangGraph
interrupt; Hosting does not inspect `action_requests` or `review_configs` to
guess its source:

- `mcp_approval_response{approve:true}` resumes with the original
  `interrupt.value`.
- `mcp_approval_response{approve:false}` returns
  `interrupt_rejected` without consuming the pending interrupt.

`HumanInTheLoopMiddleware` expects a `decisions` response, so submit its
approve, reject, edit, respond, and mixed decisions through
`function_call_output`.

## Run

Set `FOUNDRY_PROJECT_ENDPOINT`, then start the host:

```bash
az login
python main.py
```

Run the included client to request both tools and approve only the first:

```bash
python client.py
```

The first response contains one middleware interrupt with two
`action_requests`. The client reads their order, builds one matching decision
per action, copies the paired reserved `function_call.call_id`, and sends:

```json
{
  "type": "function_call_output",
  "call_id": "<interrupt-id>",
  "output": "{\"resume\":{\"decisions\":[{\"type\":\"approve\"},{\"type\":\"reject\",\"message\":\"Local time access denied\"}]}}"
}
```

The decision count must equal the `action_requests` count, and decision order
must match action order. Here `get_weather` executes, `get_local_time` does
not, its rejection reason becomes an error `ToolMessage`, and the graph
continues to a normal final response.

Approve every action by sending one approve decision per action:

```json
{
  "type": "function_call_output",
  "call_id": "<interrupt-id>",
  "output": "{\"resume\":{\"decisions\":[{\"type\":\"approve\"},{\"type\":\"approve\"}]}}"
}
```

Reject every action similarly:

```json
{
  "type": "function_call_output",
  "call_id": "<interrupt-id>",
  "output": "{\"resume\":{\"decisions\":[{\"type\":\"reject\",\"message\":\"Denied\"},{\"type\":\"reject\",\"message\":\"Denied\"}]}}"
}
```

Local runs use `InMemorySaver`; Foundry-hosted runs use
`FoundryCheckpointSaver`.
