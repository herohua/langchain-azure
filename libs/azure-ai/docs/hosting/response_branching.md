# Response Branching

Response branching lets a client select an earlier response and send new input
from its saved graph state. The selected response is the **parent**; the new
response is its **child**. Use this to try a different follow-up or regenerate
an answer.

> **Design status:** This document defines the target behavior, including the
> examples below. It does not claim availability in a published release or full
> implementation in [PR #1206](https://github.com/langchain-ai/langchain-azure/pull/1206).
> See [Implementation gaps and verification](#implementation-gaps-and-verification).

## Overview

Suppose response A suggests a destination. Two requests can use A as their parent:

```text
A: Suggest a destination.
|-- B: Plan a busy itinerary.
`-- C: Plan a relaxed itinerary.
```

Both requests set `previous_response_id=A.id`. Each starts from A's exact
**checkpoint**: saved LangGraph execution state, including messages and other
graph state. C starts from A even if B has already finished. A's checkpoint
stays unchanged.

The branches share a LangGraph thread, with separate checkpoint histories. The
host restores the selected checkpoint and passes only the current request input
to the graph. This is the `langgraph_checkpoint` execution mode; history-based
continuation instead uses stored response history.

This design covers branching from parents whose graphs have finished. Branching
from paused parents is deferred, including isolation of tool replies, approval
decisions, and resume state between children. Ordinary tool and approval
continuation keeps its existing behavior.

## Request behavior

Branching reuses **ordinary continuation**: the host's existing rules for
accepting requests, resolving conversations, selecting parents, and persisting
response checkpoint references. It adds restoration of the selected parent's
exact checkpoint.

### Request routing

A **root response** has no parent. With `enable_response_branching=True`:

| Request linkage | Behavior |
| --- | --- |
| Neither `previous_response_id` nor `conversation` | Start a root response in a new thread. |
| `previous_response_id` only | Start a child from the selected parent's exact checkpoint. |
| `conversation` only | Use ordinary conversation continuation. |
| Both fields | Reject with HTTP 400 before graph execution. |

Omitted or `null` linkage fields do not select a parent or conversation. Resolve
references within the request's authorized storage scope. An ID from another
scope does not grant access. Reference errors follow the common continuation
rules and must prevent graph execution.

A response stored in a conversation can be selected using `previous_response_id`
when ordinary continuation allows it. The branch uses that response's checkpoint.
Conversation-head selection and updates belong to ordinary continuation; the
remaining cross-linkage question is listed under [TODO](#todo).

### Parent eligibility

A parent is eligible only when:

1. It is stored with `status="completed"` and accessible to the caller.
2. Its internal metadata identifies its exact final graph checkpoint.
3. That checkpoint and the history needed to restore it are available.
4. Its graph has finished, with no pending tasks or interrupts, and its output
   has no outstanding function calls or MCP approval requests.

**Response completion and graph completion are different.** A completed response
can contain tool calls or approval requests while its graph is paused. Such a
response is not eligible for branching, even if the request supplies matching
replies.

In-progress, failed, or cancelled parents receive a 4xx error under the common
error contract before graph execution. The host must not wait for completion,
substitute another response, or select intermediate progress.

Parents created before branching was enabled or through ordinary continuation
are eligible when they satisfy these rules. No branching-specific marker is
required. Later responses do not change the checkpoint selected by an earlier
response ID.

If a required checkpoint reference or checkpoint is missing, mismatched, or
unreadable, fail before graph execution. Never substitute a latest checkpoint,
message-history reconstruction, or a new root.

### OpenAI compatibility

Public HTTP requests and responses under `/responses` must follow the
[OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
and [conversation-state guide](https://developers.openai.com/api/docs/guides/conversation-state).
The [OpenAI quickstart](https://developers.openai.com/api/docs/quickstart?tool-type=web-search)
provides a client usage baseline. The API reference defines field semantics.

- `previous_response_id` selects a response. `conversation` identifies a separate
  persistent conversation resource resolved by the configured provider or platform.
- Tool outputs and approval decisions remain valid continuation inputs. Validate
  them through the appropriate continuation path; a graph pause alone is not a
  protocol error. Paused-parent branching remains outside this design's scope.
- A parent's top-level `instructions` are not carried over, including through its
  checkpoint. Clients must resend instructions they want on each turn. System or
  developer message items in conversation history remain distinct from these
  request-scoped instructions.
- Request `store` retains its existing meaning. See
  [Request store behavior](#request-store-behavior).

The branching flag selects an internal checkpoint execution strategy. It must
preserve these public semantics. This design does not add OpenAI-hosted models,
built-in tools, or a requirement to expose `/conversations` and its item-management
routes. Creating and managing conversation resources is outside this API path.

## Configuration

Enable branching with `enable_response_branching=True` on `ResponsesHostServer`.
It is disabled by default.

- Compile the graph with a checkpointer that preserves history and supports
  asynchronous reads and writes. Keep referenced checkpoints and the history
  needed to restore them.
- Keep `options.steerable_conversations=False` and `app=None`. Steering hands off
  an in-flight turn; branching selects a saved response boundary. The adapter
  must create its own host to configure branching.

A missing checkpointer, missing required async methods, or unsupported
configuration combination raises `ValueError` during host initialization.

The host selects a response storage provider automatically: `FileResponseStore`
locally or `FoundryStorageProvider` on Foundry. Server `store=provider` optionally
overrides that provider. This argument takes a provider, not a boolean.

## Quick start

### 1. Enable branching on the server

Starting from the [basic hosting setup](../../README.md#hosting-langgraph-agents-on-microsoft-foundry)
with a configured `model`:

```python
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver

from langchain_azure_ai.agents.hosting import ResponsesHostServer

graph = create_agent(model, tools=[], checkpointer=InMemorySaver())

if __name__ == "__main__":
    ResponsesHostServer(
        graph,
        enable_response_branching=True,
    ).run(port=8088)
```

`InMemorySaver` is for local demos. Use a durable, history-preserving saver to
retain graph state across process restarts.

### 2. Create a parent and two branches

Run this in a separate client process:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8088", api_key="local", max_retries=0)

a = client.responses.create(input="Suggest a destination.", store=True)
b = client.responses.create(
    input="Plan a busy itinerary.", previous_response_id=a.id
)
c = client.responses.create(
    input="Plan a relaxed itinerary.", previous_response_id=a.id
)

print(b.output_text)
print(c.output_text)
```

A must satisfy the [parent eligibility rules](#parent-eligibility). B and C each
start from A's checkpoint.

### 3. Continue, regenerate, or rewind

| Operation | Next request |
| --- | --- |
| Continue B | Send new input with `previous_response_id=b.id`. |
| Regenerate B | Resend B's input and per-request settings with its original parent, `previous_response_id=a.id`. |
| Rewind | Select an earlier response as the next request's parent. |

Each operation creates a new response. Selecting an earlier parent preserves
history; it does not replay stored SSE events or undo external effects.

## Storage and retention

### Request store behavior

Request `store` is a boolean and defaults to `True`. It controls retention of
the current response, independently of server `store=provider`. Enabling
branching must honor `store=False`.

A response created with `store=False` is not retained and cannot later be selected
as a parent. A child can use either retention setting without deleting or
modifying its stored parent. The graph saver writes checkpoints independently
of response retention.

Conversation-state isolation, temporary checkpoint cleanup, and full-input replay
belong to a separate execution-lifecycle design. They are not implementation or
verification requirements for this branching feature.

### Stored data and lifetime

| Data | Storage | Configuration |
| --- | --- | --- |
| Response, status, input/output, and checkpoint references | SDK response provider | Host default; optional server `store=provider`. |
| Graph state, pending writes, and checkpoint history | LangGraph checkpoint saver | Graph `checkpointer=...`. |

The application owns the graph saver's initialization, lifecycle, and retention
policy. A response provider does not replace the saver or force every response
to be retained.

Enabling branching keeps automatic provider selection available. If overriding
the provider, preserve its backend configuration and existing data. Selecting a
different provider does not migrate response records or graph checkpoints.

Retaining a response does not retain or renew its graph checkpoints. Align both
lifetimes and retain history needed by recoverable tasks. The host does not
coordinate retention or cascade response deletion to checkpoints. Cleanup belongs
to the backends. These policies do not promise OpenAI's hosted retention period.

## Logical design

### Checkpoint references

Roots and children use the root checkpoint namespace, `checkpoint_ns=""`.

An exact checkpoint reference identifies a thread, namespace, and checkpoint ID.
Its role depends on where it is used:

| Reference | Meaning |
| --- | --- |
| Origin | The parent's checkpoint: the starting point for a child. |
| Progress | The current response's latest confirmed graph checkpoint, referenced by SDK recovery metadata. |
| Final checkpoint | The last progress reference stored with a completed response. Reuse still requires parent eligibility checks. |

These are references to graph state, not copies of it. **Ownership** ties a
progress checkpoint to the response that wrote it and to that response's
requested parent, if any. Several responses can share a thread, so a thread ID
alone cannot establish ownership. A child's origin is not progress made by that
child.

### Persistence order

Persistence has two stages:

1. The graph saver writes the graph checkpoint. The host confirms that write
   before publishing its reference.
2. The host attaches the reference to SDK task metadata and requests an
   **SDK recovery snapshot** containing the metadata and current response output.

Requesting a snapshot does not confirm that the SDK has saved it. Recovery uses
the last snapshot the SDK actually persisted. Graph writes and SDK response
storage are not a transaction. A saved graph checkpoint alone is insufficient:
the parent response must also be stored, completed, and eligible.

### Fresh requests

1. **Select the parent.** Apply ordinary continuation's validation and
   parent-selection rules within the platform authorization context. Load the
   selected parent's exact checkpoint reference. Roots have no parent lookup.
2. **Validate the checkpoint.** For a selected parent, check its response and
   output against the eligibility rules. After application hooks and graph
   configuration have been applied, set the selected thread, root namespace,
   and checkpoint ID again.
   The returned checkpoint tuple and payload must match that identity, with no
   next tasks or interrupts. Validation failure prevents execution.
3. **Execute.** Shallow-copy the graph and attach a request-local saver wrapper,
   leaving the application's graph and saver unchanged. Pass current input and
   instructions, using the checkpoint for prior state. Run with
   `durability="sync"` and `updates`, `messages`, and `checkpoints` stream modes.
4. **Publish progress.** For each checkpoint event, wait for the underlying saver
   write to succeed. Update the checkpoint reference in `TaskStorageManager`,
   flush output events, and call `stream.checkpoint()` to request an SDK recovery
   snapshot. A graph event alone does not confirm the write.
5. **Finalize the response.** After shutdown/cancellation checks and pending
   interrupt emission, emit `response.completed` for SDK finalization. Request
   `store` controls terminal response retention. A retained response keeps the
   final confirmed checkpoint reference; parent eligibility still applies.

Configuration and input hooks, streaming, output conversion, progress storage,
and SDK finalization remain shared with ordinary checkpointed execution.

### Background recovery

The Agent Server SDK controls recovery. Automatic crash recovery requires SDK
opt-in and stored background work; enabling branching alone does not enable it.
The SDK applies the same rules for accepting root and child requests.

| Saved state | Recovery behavior |
| --- | --- |
| Root with no saved progress | Replay its original request using the existing root configuration. |
| Child with no saved progress | Re-read and validate its stored parent, then replay the child's input from that exact checkpoint. Fail if required data is unavailable. |
| Root or child with valid progress owned by that response | Resume with input `None`, preserving pending writes. No parent lookup is needed. |
| Valid saved progress whose graph is already finished | Finalize the response without running graph nodes again. |
| Malformed progress, unavailable or mismatched checkpoint, or wrong ownership | Fail without falling back to the parent or thread's latest checkpoint. |

These are backend recovery rules, not additional OpenAI request-validation rules.
Work beyond the last persisted SDK snapshot can repeat, including external
effects. Response finalization and storage may also run again after the graph
finishes. Keep the checkpoint history needed to restore that saved state.

## Failures and limitations

Check the response status or terminal SSE event as well as the HTTP status.
Execution errors can arrive as `status="failed"` or `response.failed` after HTTP
admission. Parameter and reference errors must prevent graph execution. Match
the corresponding OpenAI HTTP error and streaming contracts. Backend execution
failures use public code `server_error`; log internal codes separately. Exact
reference-error statuses and codes still need the verification listed below.

| Problem | Result and action |
| --- | --- |
| Request includes `response_id`, an invalid parent ID, or both linkage fields | HTTP 400 before execution. Omit `response_id`; use a nonempty string or `null` for the parent, and choose one linkage type. |
| Referenced response is missing or inaccessible | Return the corresponding OpenAI-compatible reference error before execution. Do not substitute a new root. |
| Parent response was not retained | HTTP 400 with `previous_response_not_found`. An internal checkpoint cannot replace the missing response. |
| Stored parent lacks a checkpoint reference, or its graph state is missing, mismatched, or unreadable | Backend failure and failed response. Restore the required data or choose another parent. There is no fallback. |
| Input contains approval or tool-result replies | Validate the linked calls and use the appropriate continuation path. These input types are not blanket errors. |
| Graph checkpoint write fails | Failed response. Do not publish progress for the missing checkpoint; investigate the saver backend. |
| SDK terminal response storage fails | The graph checkpoint alone cannot serve as a parent. Check SDK storage errors and stored response status. |

- **Deployment:** One host process, or one worker, supports concurrent branch
  requests. Multi-process and distributed workers are outside this design. A
  durable saver allows state to survive process recreation; actual process-crash
  windows still need validation.
- **Subgraphs:** The host selects the root checkpoint namespace only. It does
  not rewind or isolate private persistent subgraph histories.
- **External effects:** Branching does not roll back effects or guarantee
  exactly-once execution. Applications must make replayed work safe.

## Implementation notes

These details describe the proposed implementation for maintainers. Applications
do not need to manage these fields or call these internal helpers.

### Saver adapter

`ResponseCheckpointSaver` is a request-local asynchronous adapter in the existing
task-storage module. It is shared by branching and ordinary checkpointed
execution. The application's saver retains storage, serialization, channel-version
allocation, and lifecycle ownership.

Branch preparation uses `select_branch_checkpoint` to select an authorized
parent. `branch_selection_scope` caches that lookup for configuration preparation
within the request. `validate_branch_checkpoint` and the saver adapter validate
exact reads. `wait_for_checkpoint` confirms graph writes before SDK progress
publication. Completion retains the final confirmed progress reference.

Synchronous private-subgraph calls pass through without changing root progress.
Native subgraph replay delegates history queries to the application saver. Root
progress confirmation needs no cross-thread futures or locks.

### Internal metadata and ownership

Recovery must distinguish the current response's progress from a sibling's
checkpoints in the same thread. An ownership record in graph checkpoint metadata
stores the response and parent identities.

A **response key** is the response ID, prefixed with
`user-<SHA-256 hex digest of user_id_key>:` when the platform supplies a nonempty
`user_id_key`. A root thread uses this key, which is separate from backend
`user_isolation`.

| Field | Purpose |
| --- | --- |
| `langgraph_thread_id`, `langgraph_checkpoint_id` | Checkpoint reference in SDK progress metadata. |
| `langgraph_response_branching="checkpoint-v1"` | Execution mode selected when the request was accepted. Recovery uses this mode, even if the server flag later changes. |
| `langgraph_response_execution` | Graph ownership record: JSON-encoded `[response_key, parent_response_id]`. The parent is `null` for a root. Private subgraph checkpoints do not carry this record. |

Middleware strips client mode headers. Inconsistent saved recovery mode causes
recovery to fail.

### Native recovery and output

After configuration hooks and saver setup, validate ownership during the graph
checkpoint read, without a separate preflight read. Branching requires a matching
ownership record. Ordinary recovery rejects mismatched records but accepts
missing records for compatibility with snapshots created before this adapter.

`ResponseCheckpointSaver.resume_config` removes `checkpoint_id` from the config
passed to LangGraph for native resume. The adapter keeps the exact reference
locally for that request and uses it for root reads and writes without an explicit
ID. This reference advances only after a successful save with the expected thread,
namespace, and new checkpoint ID.

Pending writes retain task IDs and supported task paths. A newer sibling cannot
redirect recovery reads or receive the current response's writes. Valid progress
needs no parent lookup; a child without progress must re-read its parent, as
shown in the [recovery table](#background-recovery).

The stream starts with persisted output. The converter restores missing text
from completed nodes' cached updates and tracks emitted message IDs to avoid
appending persisted text again.

### Source entry points

Existing hosting modules:

- [Responses handler](../../langchain_azure_ai/agents/hosting/_responses_host.py):
  `build_runnable_config`, `handle_create`, and `_new_stream`.
- [Task storage](../../langchain_azure_ai/agents/hosting/_responses/task_storage_manager.py):
  checkpoint references and SDK task metadata.
- [Graph-stream conversion](../../langchain_azure_ai/agents/hosting/_converters/_stream.py):
  graph streaming and response output conversion.

[Implementation PR #1206](https://github.com/langchain-ai/langchain-azure/pull/1206)
also proposes these entry points:

- `agents/hosting/_responses/branching.py`: parent selection and eligibility
  checks after configuration hooks.
- `ResponseCheckpointSaver` in the task-storage module: exact reads, ownership
  checks, graph-write confirmation, and native recovery.
- `tests/unit_tests/agents/hosting/test_response_branching.py`: parent preservation,
  concurrent siblings, failures, and recovery coverage.

The branching helpers and saver wrapper are internal, not public extension points.

## Implementation gaps and verification

| Topic | Target contract | Status |
| --- | --- | --- |
| Response storage provider | Accept the host default or an explicit server `store=provider` override. | Automatic provider selection already exists and must remain available when branching is enabled. |
| Request-scoped `instructions` | Do not inherit the parent's top-level instructions through its checkpoint. | Check checkpoint restoration against this rule. |

Before claiming compatibility, verify these observable scenarios:

- The default provider and a history-preserving saver enable branching. Cover
  local `FileResponseStore`, Foundry `FoundryStorageProvider`, and an explicit
  provider override. Omitting server `store` must not fail initialization; boolean
  constructor values are not providers.
- B and C can select A independently, including when a newer sibling exists.
  A child with `store=False` does not delete or modify its stored parent.
- In-progress, failed, and cancelled parents receive a 4xx error before graph
  execution, without waiting or selecting another response or intermediate state.
- An eligible response stored in a conversation can be selected using only
  `previous_response_id` when ordinary continuation allows it, even after newer
  responses exist. Its branch uses that response's exact checkpoint. Conversation
  head effects remain outside this verification.
- Matched function outputs and approved or denied MCP decisions work through
  the same linkage used for ordinary continuation.
- HTTP errors, error objects, terminal SSE events, and absence of graph execution
  on caller-input failures match the public contract. Helper-level checks alone
  do not establish this.

Confirm remaining reference-error details against the service before claiming
compatibility: exact status, error code, and parameter attribution for inaccessible
resources and malformed response references not already specified above. Reuse
ordinary continuation's rules without adding stricter branching-only rules or
silently selecting another parent or conversation.

## TODO

Investigate this cross-linkage case separately:

1. A is stored in an explicit conversation.
2. B selects A with `previous_response_id=A.id`.
3. C later uses the original `conversation` ID.

Determine whether B advances that conversation's pointer and which parent and
context C selects. If ordinary continuation allows selecting B, branching uses
B's exact checkpoint. Otherwise, reject the request under those continuation
rules. Branching does not define conversation-head selection or pointer timing.
