# Interrupted-request recovery

For **Responses**, connect all four layers:

1. Durable graph [checkpoints](state-and-recovery.md).
2. The [host recovery option](../assets/commands.md#responses-recovery-option)
   in both local and deployed entry points.
3. Request `background=true`, `store=true`, and stable conversation identity:
   [request example](../assets/recovery-request.json).
4. Client retention of the response ID and bounded retrieval to terminal status.
   After disconnect, retrieve that response rather than resubmitting input.

A polling deadline does not cancel server work. Make external effects idempotent
or reconcile replay. Completed-turn continuation does not test crash recovery;
use an authorized interrupted-request check, never production disruption.

For **Invocations**, verify the installed host/platform recovery contract and
runner support separately. Do not copy Responses flags or retrieval semantics.
Use host-server integration if required; do not silently change protocols.
