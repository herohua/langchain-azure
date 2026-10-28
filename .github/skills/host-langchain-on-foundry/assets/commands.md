# Runner-specific recipes

Use Foundry skills for standard azd operations.

## Direct runner

For direct SDK hosting, first add `langchain-azure-ai[hosting]` with the app's
package manager. From the app root, set the agreed protocol:

```powershell
$protocol = "<confirmed-protocol>"
if ($protocol -notin @("responses", "invocations")) {
    throw "Choose the agreed Responses or Invocations hosting protocol first."
}
uv run --no-sync --env-file .env python -m langchain_azure_ai.agents.hosting.run --protocol $protocol --config langgraph.json
```

For multiple graphs, add the graph name as a positional runner argument.

## Responses recovery option

Append to the Responses runner entry point, not as a standalone command:

```text
--option resilient_background=true
```

Do not pass this runner option to Invocations.
