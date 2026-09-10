# Human-in-the-loop tool approval

This sample pauses a LangGraph node with `interrupt(proposed)` before a tool
runs. Hosting exposes this ordinary interrupt as a reserved `function_call`
named `__hosted_agent_adapter_interrupt__`. Resume it with a matching
`function_call_output`.

The `arguments` field contains the LangGraph interrupt ID and proposed value:

```json
{"interrupt_id": "<id>", "value": {"tool": "get_weather", "arguments": {"location": "Seattle"}}}
```

The client may return a raw resume value or a JSON LangGraph `Command`
envelope containing `resume`, `update`, or `goto`. See [main.py](main.py)
for runnable curl examples.

MCP server approvals use OpenAI's separate `mcp_approval_request` and
`mcp_approval_response` items. An MCP response resumes its explicitly typed
MCP interrupt with `{"approve": bool}` plus `reason` when supplied.

Local runs use `InMemorySaver`; Foundry-hosted runs use
`FoundryCheckpointSaver`. Follow the parent [hosting README](../../README.md)
for local execution and deployment instructions.
