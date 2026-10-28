# Graph loading and startup

- **Environment first:** the runner does not load `langgraph.json`'s `env`.
  Configure environment before SDK/graph imports; tools may initialize on import.
  Remotely, use injected values rather than requiring `.env` or overwriting them.
- **Reuse the target:** the runner accepts compiled graphs or zero-argument
  factories. Check factory signatures; select multi-graph configurations
  explicitly and carry the selection into deployment.
- **Separate persistence only when agreed:** adapt
  [foundry_graph.py](../assets/foundry_graph.py) around an existing shared builder.
  Keep Studio's entry point without a custom saver; `langgraph dev` manages it.
- **Do not force runner compatibility:** use host-server integration when agreed
  hooks/options cannot be expressed through configuration.

For direct SDK hosting without an azd scaffold, use the
[runner recipe](../assets/commands.md#direct-runner). Otherwise use Foundry
`local-run`; do not combine competing install/startup procedures.
Keep framework/provider changes separate from hosting changes.
