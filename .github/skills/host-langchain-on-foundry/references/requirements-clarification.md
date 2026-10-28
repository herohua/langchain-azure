# Requirements before hosting

Inspect code/config and existing decisions first. Use this checklist internally,
not as a questionnaire. Explain tradeoffs and ask one unresolved question at a
time; resolve behavior changes before editing.

| Decision | Establish or clarify |
| --- | --- |
| Scope | App only, adapt existing evaluation callers, or migrate evaluation workflows too? Local setup, deployment-ready files, or live deployment? |
| Graph | Source root, graph name, exact module/file export, compiled graph or factory; permission for a separate hosted entry point if needed. |
| Protocol and clients | Responses or Invocations; required payload/output, streaming, structured results, and permitted client changes. |
| Checkpointer | Keep the existing saver, use Foundry State Store, use an application-owned backend such as Cosmos DB, or remain stateless. Account for persistence previously supplied by Agent Server. |
| State lifetime and identity | Restart/cross-session retention, thread mapping, user/tenant isolation, and whether existing checkpoints must migrate. |
| Files and memory | Graph-backed files, sandbox files, external artifacts, or cross-thread memory; retention/access requirements. |
| Execution | Completed-turn continuation versus active-request recovery; idempotent side effects, HITL/resume, cancellation, concurrent turns. |
| Providers and access | Retain or change model/tools/authentication; credential initialization and runtime resource access. |

## Protocol is a client-contract decision

Do not infer hosting protocol from the model API or graph shape. Check adapter
support for the required schema/features; default message conversion expects
`messages`. Neither protocol preserves Agent Server runs/threads/state APIs.
Custom hooks or recovery may need host-server integration rather than the runner.
Resolve unsupported requirements instead of silently switching protocols.

## Evaluation is a separate scope decision

- **App only:** leave evaluation unchanged; flag affected callers as follow-up.
- **New invocation target:** retain datasets, evaluators, and pass criteria;
  adapt authentication, calls, conversation handling, completion, and output.
- **Workflow/platform migration:** clarify datasets, evaluators, results, and CI;
  use the corresponding Foundry workflow.

Preparing evaluation integration does not authorize dataset generation, judge
calls, or scored runs. Reuse an explicit "no/later" decision without asking again.

## Agree, then implement

Summarize target/export, protocol/client changes, saver/identity/retention,
execution requirements, evaluation scope, and allowed checks. Distinguish facts,
proposals, and blockers. Do not silently add/replace a saver or migrate old data.
Stop for planning-only requests; defer Azure project selection until needed.
