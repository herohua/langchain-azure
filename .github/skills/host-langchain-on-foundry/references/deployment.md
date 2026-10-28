# Deployment seams

Use Foundry workflows for commands and grants; connect these steps:

1. **Align launch paths:** graph config selects the graph; local startup and
   remote entry point select the process. Match protocol, graph name, config path,
   factory, and host options across both and the client. Keep dotenv loading local.
2. **Package shared imports:** choose a common upload root or install shared
   modules as dependencies. Local checkout imports can hide missing upload files.
   Keep runtime prompts/skills; exclude secrets and private diagnostics.
3. **Resolve deployment dependencies:** follow the installed builder's dependency
   contract and check a fresh installation on the target Python runtime.
   Local overrides do not prove remote compatibility.
4. **Initialize connected tools:** create/reuse the connection, wire its nonsecret
   name into runtime configuration, then resolve credentials before constructing
   the client. A connection name alone does not inject a key. Adapt
   [project_connection.py](../assets/project_connection.py) only for explicit
   `CustomKeys` retrieval; preserve existing secret integrations otherwise.
5. **Authorize the actual runtime identity:** retrieve the agent instance
   principal after deployment creates it, then grant missing access before
   invoking tools. Developer/project identities are not interchangeable with it.
   Check parent-resource scope for connection credentials and separate data-plane
   access for application storage.

Use a remote request that exercises the capability whose access changed.
A greeting cannot verify connected tools or persistence; inspect the protocol's
terminal outcome and actual tool results.
