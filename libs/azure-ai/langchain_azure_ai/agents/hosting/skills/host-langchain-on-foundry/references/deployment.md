# From local to hosted

Use the Foundry workflows for deployment commands and role assignments. This
file covers the seams between them where LangChain apps usually break.

1. **Align the launch paths.** `langgraph.json` selects the graph; the local
   command and the hosted entry point select the process. Protocol, graph name,
   config path, factory, and host options must match across local, hosted, and
   client code, otherwise the deployed agent is not the one you tested. Load
   `.env` only locally.
2. **Package shared imports.** Pick an upload root that contains every module
   the graph imports, or install shared modules as dependencies. A local
   checkout can import files that were never uploaded, so a passing local run
   does not prove the image is complete. Include runtime prompts and skills;
   exclude secrets and private diagnostics.
3. **Resolve dependencies for the target.** Follow the dependency contract of
   the installed builder and test a fresh install on the target Python version.
   Local overrides, editable installs, and cached wheels do not prove the
   remote build will resolve.
4. **Initialize connected tools.** Create or reuse the project connection, put
   its non-secret name in runtime configuration, and resolve the credential
   before constructing the tool client. A connection name alone does not
   inject a key into the container. If the app already has a working secret
   integration, keep it. For a `CustomKeys` connection, read the key at
   startup like this:

   ```python
   import os

   from azure.ai.projects import AIProjectClient
   from azure.ai.projects.models import CustomCredential
   from azure.identity import DefaultAzureCredential


   def load_tool_api_key() -> str:
       with (
           DefaultAzureCredential() as credential,
           AIProjectClient(
               endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
               credential=credential,
           ) as project,
       ):
           connection = project.connections.get(
               os.environ["TOOL_CONNECTION_NAME"], include_credentials=True
           )
           credentials = connection.credentials
           if not isinstance(credentials, CustomCredential):
               raise ValueError("The tool connection must use CustomKeys credentials.")
           api_key = credentials.credential_keys.get("api_key")
           if not api_key:
               raise ValueError("The tool connection needs an api_key secret field.")
           return api_key
   ```

   `TOOL_CONNECTION_NAME` and the `api_key` field name are examples; use the
   names the user configured.
5. **Authorize the runtime identity.** The hosted agent runs as its own
   instance identity, which exists only after deployment creates it. Retrieve
   that principal, then grant what is missing before invoking tools; access
   held by the developer or the project identity does not carry over. Reading
   connection credentials needs access at the parent resource scope, and
   application storage needs its own data-plane role.

Verify with a remote request that exercises the capability whose access
changed. A greeting cannot prove connected tools or persistence work; check the
terminal status of the response or invocation and the actual tool results.
