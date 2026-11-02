# Interrupted-request recovery

Recovery means a request that was running when the container crashed or the
client disconnected finishes without being resubmitted. It is a different
guarantee from continuing a completed conversation (see
[Persistence](persistence.md)) and needs every layer below; any missing layer
silently turns recovery into "start over" or "lose the turn".

Both protocols share two rules:

- **Durable checkpoints are mandatory.** The host resumes from the last
  checkpoint, and both hosts raise `ValueError` at startup when
  `resilient_background=True` is set without a checkpointer. Use a durable
  saver in hosted deployments; an in-memory saver dies with the container.
- **Resumed work can repeat.** Nodes that ran after the last checkpoint run
  again, so make external side effects idempotent or reconcile them. A client
  polling deadline does not cancel server work.

## Responses

1. **Checkpoints:** attach a durable saver (see
   [Persistence](persistence.md#attach-the-saver)).
2. **Host option:** enable `resilient_background` in both the local and the
   deployed entry point, so local tests exercise the same code path. With the
   runner, append it to the existing command:

   ```bash
   python -m langchain_azure_ai.agents.hosting.run --protocol responses --config langgraph.json --option resilient_background=true
   ```

   In a host-server entry point, pass
   `ResponsesServerOptions(resilient_background=True)` to `ResponsesHostServer`.
3. **Request:** send background, stored, non-streaming requests with a stable
   conversation ID, because recovery resumes a stored background response in
   its conversation:

   ```json
   {
     "conversation": "<existing-conversation-id>",
     "input": "<application request>",
     "background": true,
     "store": true,
     "stream": false
   }
   ```

4. **Client:** keep the returned response ID and poll `GET /responses/{id}`
   with a bounded deadline until a terminal status. After a disconnect,
   retrieve that response instead of resubmitting the input, which would run
   the work twice.

## Invocations

The runner cannot enable Invocations recovery: it constructs
`InvocationsHostServer` without options and rejects `--option` for this
protocol. Write a host-server entry point when the user agrees to recovery:

```python
import asyncio

from langchain_azure_ai.agents.hosting import (
    FoundryCheckpointSaver,
    InvocationsHostServer,
    ResponsesServerOptions,
)

# Placeholder: the app's existing builder, which accepts a checkpointer.
from agent import build_agent


async def main() -> None:
    async with FoundryCheckpointSaver(user_isolation=True) as checkpointer:
        graph = build_agent(checkpointer=checkpointer)
        server = InvocationsHostServer(
            graph,
            options=ResponsesServerOptions(resilient_background=True),
        )
        await server.run_async()


if __name__ == "__main__":
    asyncio.run(main())
```

The Invocations contract differs from Responses; do not copy Responses fields
or endpoints:

- Send `"background": true` with the application's payload, for example
  `{"message": "...", "background": true}`. The host returns `202` with an
  invocation envelope. Background streaming is not supported.
- Poll `GET /invocations/{invocation_id}` for the final `response`; cancel with
  `POST /invocations/{invocation_id}/cancel`. Terminal results stay
  retrievable for 30 days.
- Continue a session by sending the `x-agent-session-id` response header back
  as the `agent_session_id` query parameter. Send `previous_invocation_id` to
  require that the new turn extends the expected chain.
- `steerable_conversations=True` lets a new turn supersede an active one in the
  same session. Enable it only if the user wants that behavior.

## Testing recovery

A completed-turn continuation test does not prove crash recovery. When the user
asks for a recovery check, interrupt a running request in an environment they
have authorized, for example by restarting a local process mid-request, then
retrieve the original response or invocation ID. Never disrupt production
deployments to test this.
