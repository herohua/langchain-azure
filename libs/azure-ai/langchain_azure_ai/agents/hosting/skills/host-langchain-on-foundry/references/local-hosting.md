# Graph loading and local startup

## Pitfalls

- **Load the environment first.** The runner reads `graphs` from
  `langgraph.json` but ignores its `env` key. Set environment variables before
  the graph module is imported, because tools and clients often read them at
  import time. In the hosted container, rely on the variables the platform
  injects; do not require a `.env` file there or overwrite injected values.
- **Reuse the existing graph.** The runner accepts a compiled graph or a
  factory that can be called with no arguments. A factory with a required
  parameter, such as a `config` argument written for LangGraph Studio, will not
  load; wrap it in a new zero-argument function instead of changing the
  original. An async factory runs on a temporary event loop that closes before
  the server starts, so do not open loop-bound resources such as `async with`
  clients inside it. When `langgraph.json` lists several graphs, pass the graph
  name explicitly and use the same name in the deployed entry point.
- **Add a hosted entry point only when needed.** If the agreed persistence
  requires a different saver, put it in a separate factory around the existing
  builder (see [Persistence](persistence.md#attach-the-saver)). Keep the
  original export for `langgraph dev` and Studio, which manage persistence
  themselves and ignore a custom checkpointer.
- **Do not bend the app to fit the runner.** The runner exposes only
  `ResponsesServerOptions` for the Responses protocol. When agreed hooks or
  options cannot be expressed that way, such as Invocations background
  execution, write a host-server entry point instead (see
  [Recovery](recovery.md#invocations)).

## Direct runner

Use this when hosting directly with the SDK, without an azd scaffold. If the
`microsoft-foundry` `local-run` workflow already scaffolded the project, follow
it instead; mixing two install and startup procedures leaves the user with two
entry points that drift apart.

Add `langchain-azure-ai[hosting]` with the app's own package manager (pip,
uv, Poetry, and so on), load the environment, then start the runner from the
directory that contains `langgraph.json`:

```bash
python -m langchain_azure_ai.agents.hosting.run --protocol responses --config langgraph.json
```

- `--protocol` is required and must be the agreed `responses` or
  `invocations`; there is no default, because the protocol is part of the
  client contract.
- Add the graph name as a positional argument when `langgraph.json` defines
  more than one graph, for example `... run agent --protocol responses`.
- Repeat `--option NAME=VALUE` to set `ResponsesServerOptions` fields. Options
  are rejected for `invocations`.
- `--host` defaults to `0.0.0.0` and `--port` to `$PORT` or `8088`.

Run it through the app's environment, for example
`uv run --env-file .env python -m ...` or after activating the virtual
environment. Keep framework and provider upgrades out of this step so a failure
points at hosting rather than at a dependency change.
