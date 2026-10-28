# Requirements before hosting

The table below is a checklist for you, not a questionnaire for the user.
Asking about eight areas one by one costs many turns, and most answers are
already in the code. Work in three steps:

1. **Infer.** Read `langgraph.json`, the graph module, dependencies, existing
   clients, and earlier decisions in the conversation. Fill every row you can,
   choosing the conservative default when the code does not decide it: keep the
   current saver, models, and tools; leave evaluation unchanged.
2. **Propose.** Show the user one short design summary that separates facts
   (read from code), proposals (your defaults), and open questions. Explain the
   tradeoff behind each proposal in one sentence.
3. **Ask only what remains.** Ask about rows the code cannot answer and that
   change behavior, such as the protocol when no client exists yet. Group
   related questions. Do not ask again about anything the user has already
   decided, including an explicit "no" or "later".

| Decision | Establish |
| --- | --- |
| Scope | App only, adapting existing evaluation callers, or migrating evaluation workflows too. Local setup, deployment-ready files, or a live deployment. |
| Graph | Source root, graph name, the exact module and export, compiled graph or factory, and whether a separate hosted entry point is allowed. |
| Protocol and clients | Responses or Invocations; required payload and output shape, streaming, structured results, and which client changes are allowed. |
| Checkpointer | Keep the existing saver, use Foundry State Store (`FoundryCheckpointSaver`), use an application-owned backend such as Cosmos DB, or stay stateless. Account for persistence that Agent Server used to provide. |
| State lifetime and identity | Retention across restarts and sessions, thread mapping, user or tenant isolation, and whether existing checkpoints must migrate. |
| Files and memory | Graph-backed files, sandbox files, external artifacts, or cross-thread memory, with their retention and access needs. |
| Execution | Completed-turn continuation or active-request recovery; idempotent side effects, human-in-the-loop resume, cancellation, and concurrent turns. |
| Providers and access | Keep or change models, tools, and authentication; how credentials are initialized and which resources the runtime identity must reach. |

## Protocol is a client-contract decision

The protocol defines what callers send and receive, so infer it from the
clients, not from the model API or the graph shape.

- **Responses** serves an OpenAI Responses-style `/responses` endpoint. Choose
  it when callers already speak Responses or need conversations, background
  responses, and human-in-the-loop items without a custom schema.
- **Invocations** serves a generic `/invocations` endpoint where the
  application defines its own JSON. Choose it when callers need a custom
  payload or output shape.

The default message conversion of both hosts expects a `messages` state key;
check that the graph has one or plan an adapter. Neither protocol reproduces
the Agent Server runs, threads, or state APIs, so callers that use those APIs
need changes whichever protocol is chosen. Custom hooks or recovery may need a
host-server entry point instead of the runner. If a requirement is unsupported,
raise it with the user instead of quietly switching protocols, because that
silently breaks their clients.

## Evaluation is a separate scope decision

Moving the agent changes how evaluation code reaches it, but redesigning
evaluation is a different project.

- **App only:** leave evaluation untouched and list affected callers as
  follow-up.
- **New invocation target:** keep datasets, evaluators, and pass criteria;
  adapt authentication, calls, conversation handling, completion detection, and
  output parsing.
- **Workflow or platform migration:** clarify datasets, evaluators, result
  storage, and CI, then use the matching Foundry workflow.

Preparing evaluation integration does not authorize generating datasets,
calling judge models, or running scored evaluations; those cost money and
need explicit approval.

## Agree, then implement

Before editing, confirm the target graph and export, protocol and client
changes, saver with identity and retention, execution requirements, evaluation
scope, and which checks you may run. Never add or replace a saver, or migrate
existing checkpoints, without agreement: either can lose conversation state.
Defer Azure project selection until a step actually needs it.
