# Persistence

Keep the saver the user agreed on. Apps migrating from LangGraph Platform or
Agent Server often had persistence supplied by the platform without any code;
Foundry hosting does not supply a LangGraph checkpointer, so choose the
replacement explicitly. Switching between Responses and Invocations does not
change this.

Without a checkpointer, the Responses host replays prior Responses history into
the graph on each turn, which restores messages but not other graph state.
With a checkpointer, the host sends only the new input and the graph resumes
from its checkpoint.

## What needs which store

| Need | Persist with |
| --- | --- |
| Messages, TODOs, Deep Agents state-backed files | Graph checkpoints; Responses history alone loses non-message state |
| Memory shared across threads | A LangGraph store; a checkpointer is per thread |
| Sandbox files | The session filesystem and its lifecycle |
| Artifacts that must outlive the session | External durable storage such as Blob Storage |

## Attach the saver

Compile the graph with the saver in code. The hosting YAML has no checkpointer
setting, and the runner reads only `graphs` from `langgraph.json`, so any
checkpointer configured there for Agent Server is ignored.

Both hosts run graphs asynchronously (`ainvoke`/`astream`). Every saver must
therefore implement the async methods: `FoundryCheckpointSaver` is async-only,
and sync-only savers such as `CosmosDBSaverSync` are not compatible. Use the
async `CosmosDBSaver` for a database-backed option. The same constraint applies
to any code that calls the graph directly, so replace `graph.invoke` with
`await graph.ainvoke`.

When the runner loads the graph, add a separate zero-argument factory that
reuses the app's existing builder, and point the hosted `langgraph.json` entry
at it:

```python
# foundry_graph.py
from langchain_azure_ai.agents.hosting import FoundryCheckpointSaver

# Placeholder: import the app's existing builder, whatever its real module and
# name are. It must accept a checkpointer and return a compiled graph.
from agent import build_agent


def create_graph():
    return build_agent(
        checkpointer=FoundryCheckpointSaver(user_isolation=True),
    )
```

```json
{
  "graphs": {
    "agent": "./foundry_graph.py:create_graph"
  }
}
```

When you write a host-server entry point instead, keep the saver's async
context open for the lifetime of the host so its credential is closed on
shutdown:

```python
async with FoundryCheckpointSaver(user_isolation=True) as checkpointer:
    graph = build_agent(checkpointer=checkpointer)
    await ResponsesHostServer(graph).run_async()
```

`FoundryCheckpointSaver` targets Foundry-hosted containers using container
protocol `2.0.0`, which supply the per-request context the state-store API
needs. Locally, the hosting extra backs the store with files under
`AGENTSERVER_STATE_ROOT`. The repository samples also show switching to an
in-memory or SQLite saver when not hosted. Either is fine; agree which one so
local and hosted behavior are not confused.

Changing savers does not migrate existing checkpoints. If old threads must
survive, check serializer and format compatibility first and agree on a
migration; otherwise existing conversations start empty.

## Identity and isolation

Check how the installed adapter maps requests to LangGraph `thread_id` and
user partition: a Responses conversation ID, or an Invocations
`agent_session_id`, plus the platform-provided user key. Do not assume
continuity across compute sessions behaves the same way for both protocols.

Keep `user_isolation=True` in hosted deployments that store user-specific
state; without it, stored items are not partitioned by end user. Local runs have no real caller identity, so they cannot test
isolation. If local runs need different settings, use separate local
configuration rather than weakening hosted isolation. Isolation is fixed when
a thread's store is first created, so changing the flag later does not affect
existing threads.

## Checking continuation

When the user asks to verify continuation:

1. Create a recognizable value in one turn, update it in a second turn, then
   read it in a third, all with the agreed identity mapping.
2. For cross-session checks, change compute between turns without restating
   the value.
3. Confirm the value from actual state reads or tool results, not from what the
   assistant says it remembers.
4. Check that a different user cannot read it, as a separate test.

Locally, keep the same `AGENTSERVER_STATE_ROOT` or saver database across
restarts, otherwise a restart looks like data loss.

Continuing a completed turn is different from recovering a request that was
interrupted mid-run; see [Recovery](recovery.md).
