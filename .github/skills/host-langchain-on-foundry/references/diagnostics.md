# Diagnostic pitfalls

Use Foundry `trace`/`troubleshoot` for queries and log access.

- **Duplicate spans:** check host-provided Microsoft OTel instrumentation before
  adding `AzureAIOpenTelemetryTracer` or another provider. Their settings are not
  interchangeable; a LangSmith key does not route telemetry to Foundry.
- **False success:** outer spans/function-call items can succeed while the request
  fails, or tools return error text. Inspect terminal outcome, exceptions, and
  tool results. Distinguish hosted response/invocation IDs from model response IDs.
- **Missing files/state:** Deep Agents' default virtual files live in graph state,
  not sandbox Files. Read through the graph/saver or tool; a trace tree is not a
  state viewer. Checkpoints may be deltas; use matching serializers and trusted
  data. Do not disable user isolation for inspection.
- **Slow continuation:** separate model/tool latency from checkpoint/history I/O
  and retries. Do not trade durability/isolation for speed. Separate application,
  middleware, and dependency changes from hosting regressions.
