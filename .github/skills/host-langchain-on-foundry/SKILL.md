---
name: host-langchain-on-foundry
description: >-
  Supplement microsoft-foundry when hosting an existing Python LangChain,
  LangGraph, or Deep Agents app on Foundry Hosted Agents. Use for requirements
  clarification, graph/protocol/checkpointer decisions, integration order,
  and hosting pitfalls. Delegate standard Foundry operations to
  microsoft-foundry. Exclude prompt agents and standalone quality evaluation.
---

# Host LangChain apps on Foundry

Preserve application logic and providers unless changes are agreed.

## 1. Clarify before editing

Read [requirements](references/requirements-clarification.md). Inspect code and
reuse prior decisions; ask one question at a time for unresolved choices.
Summarize the agreed design in Chat before scaffolding or changing dependencies,
graphs, or clients. Planning-only requests stop there; clarification needs no
platform setup.

## 2. Reuse Foundry workflows

For agreed platform operations, load `microsoft-foundry` and follow its applicable
prerequisites: `create`/re-host, `local-run`, `deploy`, `invoke`, or
`rbac`/`trace`/`troubleshoot`. Use current official docs if unavailable.
Do not duplicate those workflows here or expand setup into deployment/evaluation.

## 3. Apply only relevant hosting notes

| Stage or requirement | Read |
| --- | --- |
| Graph loading and startup | [Local hosting](references/local-hosting.md) |
| Stateful continuation | [Persistence](references/state-and-recovery.md) |
| Interrupted-request recovery | [Recovery](references/recovery.md) |
| Local-to-hosted configuration and access | [Deployment seams](references/deployment.md) |
| Failed or slow requests, tracing/state inspection | [Diagnostics](references/diagnostics.md) |

Configure required persistence before local checks; keep the tested entry point
when deploying, then exercise the changed runtime capability remotely.
Skip completed work. Read references and their `assets/` examples only as needed.
