---
name: host-langchain-on-foundry
description: >-
  Host an existing Python LangChain, LangGraph, or Deep Agents app on Microsoft
  Foundry Hosted Agents with langchain-azure-ai: clarify requirements, pick the
  Responses or Invocations protocol, wire a checkpointer (FoundryCheckpointSaver,
  Cosmos DB, or none), connect tools, enable interrupted-request recovery, and
  diagnose hosted failures. Use this skill whenever the user wants to deploy,
  host, re-host, or migrate a LangGraph/LangChain/Deep Agents agent to Foundry
  or Azure AI Foundry, mentions langgraph.json with Foundry, uses
  langchain_azure_ai.agents.hosting, is moving off LangGraph Platform or Agent
  Server, or asks how state, persistence, or recovery work for a Foundry-hosted
  graph, even if they never say "hosted agent". Complements the
  microsoft-foundry skill, which owns generic azd and Foundry operations. Not
  for Foundry prompt agents or standalone evaluation work.
---

# Host LangChain apps on Foundry

The user already has a working agent. The goal is to put it behind a Foundry
hosting protocol without rewriting it, so keep application logic, models,
tools, and providers as they are unless the user agrees to change them. Every
unrequested change makes it harder to tell a hosting regression from an
application regression.

## 1. Agree on the design before editing

Hosting choices (protocol, checkpointer, identity mapping) change the
client contract and where state lives, and they are expensive to undo after
deployment. Read [requirements](references/requirements-clarification.md),
infer as much as possible from the code and earlier conversation, then present
one proposed design for the user to confirm. Ask only about what the code
cannot answer.

If the user only wants a plan, stop after the agreed design. Clarification
needs no Azure resources, so do not create or select a Foundry project yet.

## 2. Reuse Foundry workflows instead of duplicating them

For platform operations, load the `microsoft-foundry` skill and follow the
matching workflow: `create` or re-host, `local-run`, `deploy`, `invoke`, and
`rbac`, `trace`, or `troubleshoot`. This skill only covers what is specific to
LangChain and LangGraph apps.

If `microsoft-foundry` is not installed, use these sources instead of
improvising commands:

- [LangGraph hosting guide](https://learn.microsoft.com/azure/foundry/how-to/develop/langchain-hosted-agents)
  for prerequisites, local requests, and deployment.
- [langchain-azure hosted-agent samples](https://github.com/langchain-ai/langchain-azure/blob/main/samples/hosting/langgraph-hosted-agents/README.md)
  for runnable Responses and Invocations examples.
- [Foundry LangGraph samples](https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents/langgraph)
  for platform configuration and deployment files.

Do not grow setup into deployment or evaluation work the user did not ask for.

## 3. Read only the notes for the current stage

| Stage or requirement | Read |
| --- | --- |
| Graph loading and local startup | [Local hosting](references/local-hosting.md) |
| State across turns, restarts, or sessions | [Persistence](references/persistence.md) |
| Surviving a crash or disconnect mid-request | [Recovery](references/recovery.md) |
| Moving from local to hosted, connected tools, access | [Deployment](references/deployment.md) |
| Failed, slow, or misleading requests; tracing; state inspection | [Diagnostics](references/diagnostics.md) |

Order matters. Configure the agreed persistence before local checks, because a
graph that works without a checkpointer can fail once the async-only Foundry
saver is attached. Deploy the same entry point you tested locally, then
exercise the capability you changed against the hosted agent; a greeting
proves only that the container started. Skip stages the user has already
completed.
