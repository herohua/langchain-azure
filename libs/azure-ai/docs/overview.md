# Overview

`langchain-azure-ai` adapts Microsoft Foundry and Azure services to LangChain
and LangGraph interfaces. This overview helps contributors understand component
boundaries, dependencies, data flows, state ownership, and testing layers. Detailed
behavior and acceptance cases belong in the corresponding feature designs.

## System boundaries

The layers group components by responsibility. The process boundary separates
local libraries from remote services. Applications select the components they
need. Deep Agents builds on LangChain and LangGraph. Arrows point from each
adapter to its main library dependencies.

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 20, "rankSpacing": 25}}}%%
flowchart TB
    subgraph process["<b>Application process</b>"]
        direction TB
        app["<b>Application code</b><br/>Define the workflow and configuration."]
        subgraph frameworks["<b>Frameworks</b>"]
            direction LR
            deep["<b>Deep Agents</b><br/>Add planning, subagents, files, and skills."]
            langchain["<b>LangChain</b><br/>Define components and agent loops."]
            langgraph["<b>LangGraph</b><br/>Execute graphs and manage graph state."]
            deep ~~~ langchain ~~~ langgraph
        end
        subgraph package["<b>langchain-azure-ai</b> · Adapt Azure services and hosting protocols."]
            models["<b>Models</b><br/>Chat / embeddings"]
            agents["<b>Agent Service</b><br/>Remote agent nodes"]
            integrations["<b>Azure integrations</b><br/>Tools / data / observability"]
            hosting["<b>Hosting</b><br/>Graph / protocol mapping"]
        end
        subgraph support["<b>Dependencies</b>"]
            providers["<b>Model provider libraries</b><br/>LangChain providers / OpenAI / Anthropic"]
            agentClients["<b>Agent Service clients</b><br/>Azure AI Projects / OpenAI"]
            resourceClients["<b>Resource clients</b><br/>Azure service SDKs / MCP"]
            server["<b>Agent Server SDK</b><br/>Serve protocol APIs and manage the host."]
        end
        app ~~~ frameworks
        frameworks ~~~ models & agents & integrations & hosting
        models -->|depends on| providers
        agents -->|depends on| agentClients
        integrations -->|depends on| resourceClients
        hosting -->|depends on| server
    end
    subgraph remote["<b>Azure services</b> · Execute remote operations and retain service-managed data."]
        direction LR
        foundryModels["<b>Foundry models</b>"]
        foundryAgents["<b>Foundry Agent Service</b>"]
        foundryServices["<b>Foundry services</b><br/>State Store / Toolbox / Memory"]
        azure["<b>Azure resources</b><br/>Search / storage / databases / other services"]
        foundryModels ~~~ foundryAgents ~~~ foundryServices ~~~ azure
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

## Scenarios and components

The diagrams show how requests and results move through the components.
Solid arrows carry requests or writes; dotted arrows carry results.
Component links point to source under `langchain_azure_ai`. Public package
exports are the application entry points.

### Call Microsoft Foundry models

```mermaid
flowchart LR
    subgraph process["<b>Application process</b>"]
        app["<b>Application</b>"]
        model["<b>langchain-azure-ai</b><br/>Chat models / embeddings"]
        clients["<b>Provider libraries</b><br/>and service clients"]
        app -->|Messages / text| model
        model -->|Provider input| clients
        model -.->|Messages / deltas / vectors| app
        clients -.->|Provider output| model
    end
    models["<b>Foundry models</b>"]
    clients -->|API request| models
    models -.->|Model output| clients
```

The application selects a model, endpoint, and credentials. The model adapter
resolves Foundry configuration and uses the corresponding LangChain provider
implementation and service client. Calls return LangChain messages or
embedding vectors. Applications can invoke these components directly or use
them inside an agent.

| Component | Main entry points | Responsibility |
| --- | --- | --- |
| [Chat models](../langchain_azure_ai/chat_models/) | `AzureAIOpenAIApiChatModel`, `AzureAIAnthropicChatModel` | Extend `ChatOpenAI` and `ChatAnthropic` with Foundry endpoint and authentication handling. |
| [Embeddings](../langchain_azure_ai/embeddings/) | `AzureAIOpenAIApiEmbeddingsModel` | Extend `OpenAIEmbeddings` for Foundry configuration and embedding requests. |

### Host applications as Foundry hosted agents

```mermaid
flowchart LR
    caller["<b>HTTP client</b>"]
    subgraph hosting["<b>Foundry hosted agent container</b>"]
        server["<b>Agent Server SDK</b><br/>Responses / Invocations"]
        adapter["<b>langchain-azure-ai</b><br/>Graph hosting adapter"]
        workflow["<b>Application graph</b><br/>LangChain / LangGraph<br/>Deep Agents"]
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

| Component | Main entry points | Responsibility |
| --- | --- | --- |
| [Hosting](../langchain_azure_ai/agents/hosting/) | `ResponsesHostServer`, `InvocationsHostServer`, [configuration runner](../langchain_azure_ai/agents/hosting/run.py) | Serve a graph through Responses or HTTP Invocations; translate protocol input, graph output, interrupts, and recovery state. |
| [Checkpoint persistence](../langchain_azure_ai/agents/hosting/_checkpoint/) | `FoundryCheckpointSaver` | Implement the async LangGraph checkpoint contract on Foundry State Store. |

**Installation:** `langchain-azure-ai[hosting]` supplies the hosting dependencies
for the adapters and `FoundryCheckpointSaver`.

### Connect applications to agents and Azure resources

Connect a graph to remote agents, tools, data, and persistence through
LangChain, LangGraph, and Deep Agents interfaces.

| Integration type | Main entry points | Input and result |
| --- | --- | --- |
| [Graph nodes](../langchain_azure_ai/agents/__init__.py) | `AgentServiceFactory`, `ResponsesAgentNode`, `AgentServiceAgentState` | Graph messages go to a remote Foundry agent; response items become graph state updates. The local graph handles local tools and approvals. |
| [Tools, skills and MCPs](../langchain_azure_ai/tools/) | `AzureAIServicesToolkit`, service tools, `AzureAIProjectToolbox`, `tools.builtin` | Tool arguments produce service results. Toolbox supplies MCP tools, resources, and skills; built-in definitions configure server-side tools. |
| [Checkpointers](../../azure-cosmosdb/README.md#langgraph-checkpointer) | `CosmosDBSaver`, `CosmosDBSaverSync` | Persist and load graph checkpoints and pending writes in Cosmos DB. |
| [Stores](../../azure-cosmosdb/README.md#langgraph-store-long-term-memory) | `CosmosDBStore`, `AsyncCosmosDBStore` | Keys, values, and queries write or retrieve data shared across graph threads. |
| [Memory](../langchain_azure_ai/chat_history/) | `AzureAIMemoryChatMessageHistory`, `AzureAIMemoryRetriever`, `AzureAIMemoryMiddleware` | Forward selected messages to Foundry Memory for extraction; retrieve memories by store and scope. The base chat-history implementation owns the transcript. |
| [Vector stores](../langchain_azure_ai/vectorstores/) and [retrievers](../langchain_azure_ai/retrievers/) | `AzureSearch`, `AzureAISearchRetriever` | Documents and vectors populate a search index; queries return LangChain documents. |
| [Semantic cache](../langchain_azure_ai/vectorstores/cache.py) | `AzureCosmosDBMongoVCoreSemanticCache` | Model-scoped semantic lookup returns cached generations or records new ones. |
| [Document loaders](../langchain_azure_ai/document_loaders/) | `AzureAIContentUnderstandingLoader`, `AzureBlobStorageLoader` | Source documents or media become LangChain documents with content and metadata. Content Understanding analyzes inputs; the Blob loader downloads and parses them. |
| [Backends](../../azure-storage/README.md#deep-agents-azure-blob-storage-backend-usage) | `AzureBlobBackend` | Deep Agents file operations read or write workspace files in Blob Storage. |

**Additional dependencies:** `langchain-azure-ai[tools]` supplies SDKs for the
service tools. Toolbox MCP tools and resources require `langchain-mcp-adapters`
and `httpx`; Toolbox skill file mappings also require `deepagents`.

**Separate packages:** Cosmos DB checkpointers and Stores come from
`langchain-azure-cosmosdb`. The Blob document loader and filesystem backend
come from `langchain-azure-storage`.

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

The Cosmos DB vector-store, chat-history, NoSQL semantic-cache, and
[query-translator](../langchain_azure_ai/query_constructors/cosmosdb_no_sql.py)
compatibility modules forward to the separate `langchain-azure-cosmosdb` package. That
package also provides graph checkpoint and Store implementations. The
remaining semantic-cache implementation in this package uses its vector-store
client. See the [Cosmos DB package](../../azure-cosmosdb/README.md) for those
components and their installation requirements.

## Shared components

Middleware, observability, and evaluation support applications across the
scenarios above.

| Component | Main entry points | Responsibility |
| --- | --- | --- |
| [Middleware](../langchain_azure_ai/agents/middleware/) | Content Safety middleware, `AzureAIMemoryMiddleware` | Apply service-backed controls at supported agent, model, and tool hooks; synchronize selected messages to Foundry Memory. |
| [Observability](../langchain_azure_ai/callbacks/tracers/) | `AzureAIOpenTelemetryTracer`, `enable_auto_tracing` | Convert LangChain callbacks into OpenTelemetry spans and metrics; support explicit callbacks and automatic callback injection. |
| [Evaluation](../langchain_azure_ai/evaluation/) | `FoundryEvaluator`, `FoundryEvaluatorSuite`, evaluation subgraph helpers | Convert evaluation inputs, run Foundry evaluators, interpret results, and compose bounded evaluate/refine loops. |

**Installation:** `langchain-azure-ai[opentelemetry]` supplies the tracing and
Azure Monitor dependencies.

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

Shared configuration lives in [`_resources.py`](../langchain_azure_ai/_resources.py)
and [`utils/`](../langchain_azure_ai/utils/). [`_user_agent.py`](../langchain_azure_ai/_user_agent.py)
provides package attribution, and [`_api/`](../langchain_azure_ai/_api/)
provides experimental and deprecation warnings. Several public namespaces
load feature implementations lazily.

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
