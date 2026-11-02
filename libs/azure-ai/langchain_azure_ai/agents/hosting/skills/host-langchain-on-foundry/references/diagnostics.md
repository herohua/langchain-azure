# Diagnostic pitfalls

Use the Foundry `trace` and `troubleshoot` workflows to query traces and logs.
These are the LangChain-specific traps when reading the results.

- **Duplicate spans.** The host may already install Microsoft OpenTelemetry
  instrumentation. Check before adding `AzureAIOpenTelemetryTracer` or another
  provider, or every step is reported twice. The tracers are configured
  differently, and a LangSmith key does not send telemetry to Foundry.
- **False success.** Outer spans and function-call items can succeed while the
  request fails, and tools can return error text as a normal result. Check the
  terminal status, exceptions, and actual tool output. Do not confuse the hosted
  response or invocation ID with the model's own response ID; only the former
  can be retrieved from the host.
- **Missing files or state.** Deep Agents' default virtual files live in graph
  state, not in sandbox files, so look for them through the graph, the saver, or
  a tool. A trace tree is not a state viewer. Checkpoints may store deltas; read
  them with the matching serializer and only from trusted data. Do not disable
  user isolation to make inspection easier.
- **Slow continuation.** Separate model and tool latency from checkpoint and
  history I/O and retries before changing anything. Do not trade durability or
  isolation for speed. Rule out application, middleware, and dependency changes
  before blaming hosting.
