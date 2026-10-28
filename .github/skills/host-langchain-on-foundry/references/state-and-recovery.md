# Persistence

Preserve the agreed saver. If Agent Server supplied persistence, choose its
replacement explicitly: changing hosting protocol does not replace it.
For an agreed Foundry saver, adapt [foundry_graph.py](../assets/foundry_graph.py);
configure it during graph construction, not in YAML, and use async execution.
Changing savers does not migrate historical checkpoints; check format compatibility.

| Need | Preserve |
| --- | --- |
| Messages, TODOs, state-backed files | Graph checkpoints, not merely Responses history |
| Cross-thread memory | Application store, not merely a checkpointer |
| Sandbox files | Session filesystem lifecycle |
| Artifacts beyond session deletion | External durable storage |

For thread identity, verify Responses conversation/user mapping or Invocations
session/user mapping in the installed adapter. Do not assume cross-compute-session
continuity works identically. Keep hosted `user_isolation=True` for user-specific
Foundry state; local runs cannot verify caller isolation. Use separate local
configuration if needed, not weaker hosted isolation.

When continuation checks are requested, create, update, then read a recognizable
value using the agreed identity mapping. For cross-session checks, change compute
without reconstructing the value. Inspect actual reads, not assistant memory;
check cross-user denial separately. Retain the local state root across restarts.
Completed-turn continuation is distinct from [active-request recovery](recovery.md).
