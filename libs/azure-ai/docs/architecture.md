# langchain-azure-ai architecture

`langchain-azure-ai` adapts Microsoft Foundry and Azure services to LangChain
and LangGraph interfaces. This document describes the package's component
boundaries, data flows, state ownership, and testing layers. Detailed
behavior and acceptance cases belong in the corresponding feature designs.

## System boundaries

The layers group components by responsibility. The process boundary separates
local libraries from remote services. Applications select the components they
need. Deep Agents builds on LangChain and LangGraph.

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 20, "rankSpacing": 25}}}%%
flowchart TB
    subgraph process["Application process"]
        direction TB
        app["Application code<br/>Define the workflow and configuration."]
        subgraph frameworks["Frameworks"]
            direction LR
            deep["Deep Agents (optional)<br/>Add planning, subagents, files, and skills."]
            langchain["LangChain<br/>Define components and agent loops."]
            langgraph["LangGraph<br/>Execute graphs and manage graph state."]
            deep ~~~ langchain ~~~ langgraph
        end
        subgraph package["langchain-azure-ai · Adapt Azure services and hosting protocols."]
            direction LR
            models["Models<br/>Chat / embeddings"]
            agents["Agent Service<br/>Remote agent nodes"]
            integrations["Azure integrations<br/>Tools / data / observability"]
            hosting["Hosting<br/>Graph / protocol mapping"]
            models ~~~ agents ~~~ integrations ~~~ hosting
        end
        subgraph support["Supporting libraries"]
            direction LR
            server["Agent Server SDK (hosting extra)<br/>Serve protocol APIs and manage the host."]
            clients["Azure / OpenAI / Anthropic / MCP clients<br/>Handle authentication and service calls."]
            server ~~~ clients
        end
        app ~~~ frameworks ~~~ package ~~~ support
    end
    subgraph remote["Azure services · Execute remote operations and retain service-managed data."]
        direction LR
        foundryModels["Foundry models"]
        foundryAgents["Foundry Agent Service / State Store"]
        azure["Azure resources<br/>Search / storage / databases / other services"]
        foundryModels ~~~ foundryAgents ~~~ azure
    end
    process ~~~ remote
    style process fill:#f8fafc,stroke:#64748b
    style frameworks fill:#eff6ff,stroke:#3b82f6
    style package fill:#ecfdf5,stroke:#059669
    style support fill:#eff6ff,stroke:#3b82f6
    style remote fill:#fffbeb,stroke:#d97706
    classDef default fill:#ffffff,stroke:#94a3b8
```

The hosting adapters implement the graph handlers and their input, output,
and execution-state mapping on the Agent Server SDK. See
[Responses hosting](hosting/responses.md) and
[Invocations hosting](hosting/invocations.md) for supported adapter behavior.

## Components

Component links point to their source under `langchain_azure_ai`. Public
package exports are the application entry points; internal modules implement
the adapters.

| Component | Main entry points | Responsibility |
| --- | --- | --- |
| [Chat models](../langchain_azure_ai/chat_models/) | `AzureAIOpenAIApiChatModel`, `AzureAIAnthropicChatModel` | Extend `ChatOpenAI` and `ChatAnthropic` with Foundry endpoint and authentication handling. |
| [Embeddings](../langchain_azure_ai/embeddings/) | `AzureAIOpenAIApiEmbeddingsModel` | Extend `OpenAIEmbeddings` for Foundry configuration and embedding requests. |
| [Agent Service](../langchain_azure_ai/agents/__init__.py) | `AgentServiceFactory`, `ResponsesAgentNode`, `AgentServiceAgentState` | Create or reference remote Foundry agents and compose them as graph nodes, including local tool and approval handling. |
| [Hosting](../langchain_azure_ai/agents/hosting/) | `ResponsesHostServer`, `InvocationsHostServer`, [configuration runner](../langchain_azure_ai/agents/hosting/run.py) | Serve a graph through Responses or HTTP Invocations; translate protocol input, graph output, interrupts, and recovery state. |
| [Checkpoint persistence](../langchain_azure_ai/agents/hosting/_checkpoint/) | `FoundryCheckpointSaver` | Implement the async LangGraph checkpoint contract on Foundry State Store. |
| [Middleware](../langchain_azure_ai/agents/middleware/) | Content Safety middleware, `AzureAIMemoryMiddleware` | Apply service-backed controls at supported agent, model, and tool hooks; synchronize selected messages to Foundry Memory. |
| [Tools](../langchain_azure_ai/tools/) | `AzureAIServicesToolkit`, service tools, `AzureAIProjectToolbox`, `tools.builtin` | Expose local service-calling tools, managed MCP tools and resources, and server-side tool definitions. |
| [Retrieval](../langchain_azure_ai/retrievers/) and [vector stores](../langchain_azure_ai/vectorstores/) | `AzureAISearchRetriever`, `AzureSearch`, `AzureAIMemoryRetriever` | Map searches and memory retrieval to LangChain documents; manage vector ingestion and search through the vector-store interface. |
| [Cache](../langchain_azure_ai/vectorstores/cache.py) | `AzureCosmosDBMongoVCoreSemanticCache` | Reuse model generations through semantic lookup, with entries scoped to the model configuration. |
| [Chat history](../langchain_azure_ai/chat_history/) | `AzureAIMemoryChatMessageHistory` | Wrap transcript storage and forward messages for long-term memory extraction. |
| [Document loading](../langchain_azure_ai/document_loaders/) | `AzureAIContentUnderstandingLoader` | Analyze documents and media, then map content, fields, and source metadata to LangChain documents. |
| [Tracing](../langchain_azure_ai/callbacks/tracers/) | `AzureAIOpenTelemetryTracer`, `enable_auto_tracing` | Convert LangChain callbacks into OpenTelemetry spans and metrics; support explicit callbacks and automatic callback injection. |
| [Evaluation](../langchain_azure_ai/evaluation/) | `FoundryEvaluator`, `FoundryEvaluatorSuite`, evaluation subgraph helpers | Convert evaluation inputs, run Foundry evaluators, interpret results, and compose bounded evaluate/refine loops. |

Shared configuration lives in [`_resources.py`](../langchain_azure_ai/_resources.py)
and [`utils/`](../langchain_azure_ai/utils/). [`_user_agent.py`](../langchain_azure_ai/_user_agent.py)
provides package attribution, and [`_api/`](../langchain_azure_ai/_api/)
provides experimental and deprecation warnings. Several public namespaces
load feature implementations lazily.

The Cosmos DB vector-store, chat-history, NoSQL semantic-cache, and
[query-translator](../langchain_azure_ai/query_constructors/cosmosdb_no_sql.py)
compatibility modules forward to the separate `langchain-azure-cosmosdb` package. That
package also provides graph checkpoint and Store implementations. The
remaining semantic-cache implementation in this package uses its vector-store
client. See the [Cosmos DB package](../../azure-cosmosdb/README.md) for those
components and their installation requirements.

## Data flows

The scenarios show how requests and results move through the components.
Solid arrows carry requests or writes; dotted arrows carry results.

### Call Microsoft Foundry models

```mermaid
flowchart LR
    subgraph process["Application process"]
        app["Application"]
        model["langchain-azure-ai<br/>Chat models / embeddings"]
        clients["Provider libraries<br/>and service clients"]
        app -->|Messages / text| model
        model -->|Provider input| clients
        model -.->|Messages / deltas / vectors| app
        clients -.->|Provider output| model
    end
    models["Foundry models"]
    clients -->|API request| models
    models -.->|Model output| clients
```

The application selects a model, endpoint, and credentials. The model adapter
resolves Foundry configuration and uses the corresponding LangChain provider
implementation and service client. Calls return LangChain messages or
embedding vectors. Applications can invoke these components directly or use
them inside an agent.

### Host applications as Foundry hosted agents

```mermaid
flowchart TB
    caller["HTTP client"]
    subgraph hosting["Foundry hosted agent container"]
        server["Agent Server SDK<br/>Responses / Invocations"]
        adapter["langchain-azure-ai<br/>Graph hosting adapter"]
        workflow["Application graph<br/>LangChain / LangGraph<br/>Deep Agents (optional)"]
        server -->|Protocol input| adapter
        adapter -->|Graph input| workflow
        workflow -.->|Graph output / events| adapter
        adapter -.->|Protocol output / events| server
    end
    caller -->|HTTP request| server
    server -.->|HTTP response / stream| caller
```

The application supplies a compiled graph with its models, tools, middleware,
and persistence. Agent Server accepts HTTP requests. The hosting adapter
translates between the protocol and the application graph. See
[Responses hosting](hosting/responses.md) and
[Invocations hosting](hosting/invocations.md) for protocol behavior, interrupts,
and recovery.

### Connect applications to agents and Azure resources

```mermaid
flowchart LR
    workflow["Application graph<br/>LangChain / LangGraph<br/>Deep Agents (optional)"]
    node["langchain-azure-ai<br/>ResponsesAgentNode"]
    agent["Foundry Agent Service<br/>Remote agent"]
    resources["langchain-azure-ai<br/>Azure tools / retrieval / loaders"]
    azure["Azure services<br/>Tools / search / analysis / memory"]
    cosmos["langchain-azure-cosmosdb<br/>Checkpointer / Store"]
    database["Azure Cosmos DB"]
    storage["langchain-azure-storage<br/>Blob loader / AzureBlobBackend"]
    blobs["Azure Blob Storage"]

    workflow -->|Agent input| node
    node -->|Responses request| agent
    agent -.->|Response items / events| node
    node -.->|Graph state update| workflow
    workflow -->|Tool input / query / source| resources
    resources -->|Service request| azure
    azure -.->|Service result| resources
    resources -.->|Tool result / documents| workflow
    workflow -->|Checkpoints / Store operations| cosmos
    cosmos -->|Database operations| database
    database -.->|Stored records| cosmos
    cosmos -.->|Checkpoints / Store values| workflow
    workflow -->|Document / file operations| storage
    storage -->|Blob operations| blobs
    blobs -.->|Blob contents / metadata| storage
    storage -.->|Documents / file contents| workflow
```

`ResponsesAgentNode` calls a remote Foundry agent from the application's graph.
`AgentServiceFactory` can create or reference that agent. See the
[Agent Service examples](azure_ai_agents_demo_v2.ipynb).

The graph can also access resources through tools, retrievers, loaders, and
persistence interfaces. Applications can combine this package's components
with a [Cosmos DB checkpointer or Store](../../azure-cosmosdb/README.md), a
[Blob document loader](../../azure-storage/README.md#azure-blob-storage-document-loader-usage),
or other LangChain integrations. Deep Agents can also load Toolbox skills into
its files or backend and use
[`AzureBlobBackend`](../../azure-storage/README.md#deep-agents-azure-blob-storage-backend-usage)
for its file workspace. Checkpoints, long-term memory, source documents, and
workspace files have separate roles. Each package owns its adapters and service
configuration.

## State and resource ownership

| State or resource | Owner and role |
| --- | --- |
| Graph definition and compiled graph | The application defines topology, state schema, prompts, models, and tools. LangGraph executes it. |
| Graph checkpoints and pending writes | The configured LangGraph saver persists execution state. The application supplies the saver; `FoundryCheckpointSaver` is one implementation. |
| Hosted response or invocation records | Protocol storage retains input, output, status, and execution metadata. Task and replay storage support the configured execution mode. |
| Responses conversation head | `conversation_chain_store` retains the checkpoint reference used for ordinary checkpointed continuation. A branch selects its parent's exact checkpoint. |
| Remote Agent Service conversation | Agent Service retains remote conversation data; the local graph carries the IDs needed for later requests. |
| Chat transcript and long-term memory | A base chat-history implementation owns the transcript. Memory adapters forward selected messages for service-managed extraction and retrieve memories by store and scope. |
| Search indexes and semantic cache entries | Search or database services retain indexed documents, vectors, and cached generations. Adapters define their schema and request mapping. |
| Service clients and credentials | Components hold their configured clients. Their APIs define client reuse and cleanup; Responses hosting also binds storage-client reuse to a request. |

Response storage, graph persistence, transcripts, and long-term memory have
separate purposes and lifetimes. Retaining one does not establish recovery or
retention for the others. See the
[durable state model](hosting/response_resiliency.md#durable-state-model).

State Store-backed adapters use Agent Server's local file storage during local
development and Foundry State Store when hosted. Applications can also supply
other LangGraph savers. See the [storage choices](hosting/responses.md#storage-choices).

Some adapters also hold process-local state. For example, memory middleware
buffers messages and tracks update progress on the middleware instance.
Applications must account for instance reuse when choosing memory scopes and
conversation boundaries.

Hosted graph thread selection uses platform user context when available.
Storage providers and state-store isolation settings participate in the same
boundary. Request-local checkpoint and client reuse must preserve user scope,
checkpoint selection, and durability. See
[request preparation](../langchain_azure_ai/agents/hosting/_responses/request_preparation.py)
and the [storage client scope](../langchain_azure_ai/agents/hosting/_storage/state_store_factory.py).

## Shared configuration and compatibility

Model adapters support Foundry project endpoints and direct service endpoints.
They resolve constructor arguments, environment variables, and credentials
before calling the provider client. OpenAI-based chat and embedding models
share configuration logic; Anthropic models use the Anthropic Foundry client.
Service tools, middleware, and storage adapters have their own endpoint and
credential requirements. Each component's contract defines its precedence and
supported sync and async paths.

The [package manifest](../pyproject.toml) defines dependency ranges and extras.
`hosting`, `tools`, and `opentelemetry` enable the
corresponding integrations. The `v1` extra supports deprecated Agent Service
and inference implementations. Compatibility exports preserve older import
paths while directing applications to current implementations.

Tracing uses the OpenTelemetry provider and exporter configured for the
process. Applications can attach the tracer explicitly or enable automatic
callback injection. Content recording and state recording are separate
controls. Hosting also adds outbound SDK attribution through User-Agent
tokens. Its feature mask combines process registrations and request context;
it should be interpreted according to that scope.

## Testing

Tests verify adapters at their integration boundaries. The test directory name
alone does not establish whether a test uses a live service.

| Layer | What it verifies | External boundary |
| --- | --- | --- |
| [Unit tests](../tests/unit_tests/) | Configuration, conversions, errors, component contracts, and graph/host behavior with deterministic inputs. | Mock service clients or storage as needed; use real LangChain and LangGraph execution where the behavior depends on them. |
| [Integration tests](../tests/integration_tests/) | Adapters composed with SDK and framework interfaces, including tracing, loading, agent, and storage flows. | Individual tests use local implementations, mocks, or recorded HTTP exchanges. New recordings require the relevant service setup. |
| [Process recovery E2E](../tests/e2e_tests/agents/hosting/responses_resilience/README.md) | Response and graph recovery across controlled host-process failures. | A deterministic graph and model isolate recovery behavior; separate runners exercise local processes or a deployed Foundry host. |
| [Docs and samples checks](../../../.github/tests/) | Selected example contracts and imports. | Offline checks with mocked service boundaries; they validate the cases explicitly included in that suite. |
| Live-service validation | Service behavior, authentication, deployment, and platform recovery in a configured environment. | Real Azure resources and service calls; record the environment and tested versions. |

The SDK targets Python 3.11 through 3.14. The current
[ordinary CI matrix](../../../.github/workflows/check_diffs.yml) runs 3.11 and
3.14, with separate unit, integration, and selected docs/sample jobs.
[Quarantined integration tests](../tests/quarantined_integration_tests/)
are outside the ordinary integration-test target. Commands and environment
setup live in the [contribution guide](../../../CONTRIBUTING.md) and
[SDK Makefile](../Makefile).

Each feature design should keep its acceptance cases beside its behavior:
initial state, input or action, expected result, failure or isolation checks,
and links to the implementing tests. Shared test infrastructure can be linked
from this section. The [tracer test matrix](../tests/integration_tests/tracers/README.md)
and recovery E2E guide provide existing examples.

Validation reports should identify the tested revision, Python and dependency
versions, checks run, results, and material coverage limits. Distinguish source
inspection, local or recorded execution, installed-package checks, and live
Azure validation. A passing mock test establishes the behavior at that mocked
boundary.
