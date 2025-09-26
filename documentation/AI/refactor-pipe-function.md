
## 1) Core contracts (explicit, versioned)

**Single keystone contract** governs all layers:  
Envelope → Router → Module → Router Return → Formatter.

### Request (Router input)

- `version`, `request_id` (UUID), `timestamp`
    
- `user` (id, roles, permissions), `session` (conversation id), `locale/timezone`
    
- `input` (text, structured params), `attachments` (files/urls), `context` (prior turns/system hints)
    
- `capabilities_allowed` (module/tool allowlist)
    

### Module call (Router→Module)

- `request_id`, `module_id`, `action` (execute | help | health)
    
- `payload` (JSON, validated against declared schema)
    
- `timeout_ms`, `cancellation_token`, `trace_context`
    

### Module result (Module→Router)

- `request_id`, `module_id`, `status` (ok | error | partial | streaming_end)
    
- `content` (markdown blocks + optional structured data), `artifacts`, `events`
    
- `usage`, `diagnostics`, `error` (code, message, retriable?)
    

### Error envelope (any stage)

- `request_id`, `where`, `error_code`, `message`, `details`, `retriable`
    

### Formatter output (to OpenWebUI)

- Clean markdown, optional tables/lists, stable front-matter (title, tags), `request_id`
    

---

## 2) Module packaging & manifests (single source of truth)

Modules follow a strict folder pattern under `/modules`:

```
/modules/
  <module-slug>/
    module.manifest.json   ← required
    env/                   ← required (venv or container shim)
    service/               ← required (module implementation)
    artifacts/             ← optional (temp outputs, models)
    docs/                  ← optional (HELP.md, examples)
```

**Manifest is king** — Router never inspects code, only the manifest.

### Required manifest fields

- `name`, `slug`, `version` (semver)
    
- `entry.kind`: `cli` | `http` | `grpc`
    
- `entry.path`: invocation path/URL
    
- `schema.input` / `schema.output`: JSON Schemas (URIs or embedded)
    
- `capabilities`: tags (e.g., `"search"`, `"vision"`)
    
- `limits`: timeouts, max input size, budgets
    
- `auth`: required secrets (scoped, Router-brokered)
    
- `health`: probe definitions
    
- `env.kind`: `venv` | `container`
    
- `routerCompatibility`: accepted Router API versions
    
- `help.short` / `help.long`
    
- `permissions`: optional role allowlists
    
- `install`: optional warmup hooks
    

---

## 3) Module isolation & execution

### Isolation modes

- **Venv (default, fast dev loop)**: local Python venv per module.
    
- **Container (for native/CUDA/security-sensitive)**: run via sidecar/container.
    

### Execution adapters

- `cli`: spawn subprocess, JSON over stdin/stdout, enforce CPU/Wall timeouts, propagate cancel.
    
- `http/grpc`: call local/remote endpoint with `request_id` + budget headers.
    

### Interface (stable surface)

- `describe()` → metadata (schemas, limits, help)
    
- `health()` → readiness & dependency check
    
- `execute(envelope)` → produces result envelope (streamed or final)
    
- `validate(input)` → dry-run validation
    

---

## 4) Router responsibilities (SRP & scalability)

Split Router into:

- **Ingress Adapter** (Open WebUI ⇄ Request envelope)
    
- **Dispatcher** (routing, policy, concurrency, retries, backpressure)
    
- **Egress/Return** (normalization, formatter)
    
- **Registry** (manifest-driven discovery/metadata)
    

### Discovery

- Registry scans `/modules/*/module.manifest.json` on boot, file events, or admin rescan.
    
- Validates against manifest schema.
    
- Health probes from manifest.
    
- Hot reloadable with Ready/Degraded/Unavailable states.
    

### Conflict resolution

- Multiple versions → keep all, pick highest compatible.
    
- Duplicate slug+version → quarantine if mismatch.
    

---

## 5) Reliability & scale techniques

- **Queueing**: per-module async queues with DLQs, backpressure signals.
    
- **Retries**: idempotent ops only, with backoff + jitter.
    
- **Rate limits**: per user/module/global, with friendly `retry_after`.
    
- **Caching**: deterministic results memoized by `<slug>@<version> + normalized input`.
    
- **Cold-start prep**: optional warmup hooks (model downloads, etc.).
    
- **Load balancing**: multiple instances per module; health-aware routing.
    
- **Graceful degradation**: fallback when modules unavailable.
    

---

## 6) Formatter & UX consistency

- **Streaming**: token streams or chunked events → incremental markdown.
    
- **Uniform UX**: titles, summaries, diagnostics, error cards.
    
- **Markdown safety**: sanitize, cap large payloads, escape fences.
    
- **Templates**: reusable response cards, errors, confirmations.
    
- **Accessibility**: screen-reader friendly markup.
    
- **Internationalization**: multiple locales, fallback to English.
    

---

## 7) Discoverability & help

- `help` → list Ready modules (slug, version, caps, `help.short`)
    
- `help <slug>` → full manifest help, schemas, limits, examples
    
- No introspection of source files.
    

---

## 8) Observability & ops

- **Logs**: structured by `request_id`, `module_id`, latency, error_code.
    
- **Metrics**: latency (p50/p95/p99), error rates, queue depth, cache hit rate.
    
- **Tracing**: W3C trace context across Router, Modules, Orchestrator.
    
- **Audit**: persist envelopes (sans sensitive payloads) for compliance/replay.
    
- **Health monitoring**: continuous probes with alerting.
    
- **Profiling**: CPU, memory, I/O.
    

---

## 9) Security & governance

- **Validation**: Router enforces JSON Schema, size/type limits.
    
- **Least privilege**: manifest-scoped permissions and secrets.
    
- **Budgets**: per-request limits (time, memory, tokens).
    
- **Secrets**: short-lived, Router-distributed, scoped by manifest.
    
- **Safety classification**: module ratings (SAFE → DESTRUCTIVE).
    
- **Sandboxing**: containers, rollback, isolated resources.
    
- **Encryption**: end-to-end, including stored artifacts.
    

---

## 10) Agent orchestration (agent-ready)

- **Plan–Act–Observe** loop: tool selection, working memory, long-term store.
    
- **Tool selection**: Registry metadata + capabilities + health + cost.
    
- **Safety rails**: budgets, confirmation prompts, PII redaction.
    
- **Error recovery**: retries, alternate modules, graceful degradation.
    
- **Tracing**: every agent step logged for audit.
    
- **Learning system**: track success/fail patterns, improve policy.
    

---

## 11) Data modeling & schemas

- Versioned JSON Schemas for:
    
    - Request envelope
        
    - Module manifests & `describe()` output
        
    - Module input/output payloads
        
    - Result & error envelopes
        
    - Agent state and memory schemas
        
    - Governance/policy configs
        
- Semantic versioning + deprecation cycles.
    
- Auto-doc generation + validation tools.
    
- Registry integration with schema-aware matching.
    

---

## 12) Developer ergonomics

- **Scaffold generator**: creates folder pattern + manifest stub + health probe.
    
- **Local runner**: build venv, run health, launch test harness.
    
- **Validation CLI**: `validate-manifest`, `validate-schemas`.
    
- **Hot reload**: manifests watched by Registry.
    

---

## 13) Autonomous agent implementation guidance

### Code generation requirements

- **Manifest templates**: Provide JSON schema templates for common module types
- **Boilerplate generation**: Auto-generate module scaffolding from manifest
- **Validation scripts**: Create automated validation for manifest compliance
- **Test harness**: Generate test cases based on input/output schemas
- **Documentation**: Auto-generate module documentation from manifests

### Migration automation

- **Discovery**: Scan existing `ai_pipes/` directory for current modules
- **Analysis**: Extract functionality, dependencies, and interfaces
- **Conversion**: Transform to new module format with manifest generation
- **Validation**: Test converted modules against original behavior
- **Integration**: Wire converted modules into new Router system

### Safety automation

- **Risk assessment**: Automatically classify operations by safety level
- **Confirmation flows**: Generate appropriate confirmation prompts
- **Rollback preparation**: Create automated backup and restore procedures
- **Monitoring**: Implement health checks and failure detection
- **Recovery**: Automate recovery procedures for common failure modes

## 14) Implementation roadmap (autonomous agent execution plan)

### Phase 0 - Analysis & Planning (Foundation)
- **Current system audit**: Inventory existing modules and dependencies
- **Migration planning**: Create conversion matrix for each current module
- **Risk assessment**: Identify dangerous operations requiring special handling
- **Compatibility verification**: Ensure OpenWebUI integration remains intact

### Phase 1 - Core Infrastructure (Essential foundation)
- **Contract definitions**: Implement JSON schemas for all envelope types
- **Router architecture**: Build Ingress, Dispatcher, Egress, Registry components
- **Module interface**: Create base module class and execution adapters
- **Safety framework**: Implement operation classification and confirmation flows
- **Basic observability**: Structured logging and essential metrics

### Phase 2 - Module System (Functional core)
- **Manifest system**: Implement module discovery and validation
- **Execution isolation**: Venv and container execution adapters
- **Health monitoring**: Module health checks and status tracking
- **Error handling**: Standardized error processing and recovery
- **Basic formatter**: Markdown generation for common response types

### Phase 3 - Migration & Integration (Production readiness)
- **Module conversion**: Convert existing modules to new format
- **OpenWebUI adapter**: Complete integration layer with streaming support
- **Testing framework**: Comprehensive validation and regression testing
- **Performance optimization**: Caching, connection pooling, resource management
- **Documentation**: Complete API docs and usage guides

### Phase 4 - Advanced Features (Intelligence layer)
- **Agent orchestrator**: Plan-act-observe loop implementation
- **Memory systems**: Working, short-term, and long-term storage
- **Learning capabilities**: Pattern recognition and success tracking
- **Advanced policies**: Complex safety rules and governance
- **Full observability**: Complete metrics, tracing, and audit systems

### Validation checkpoints
- **Phase 0**: All current functionality inventoried and categorized
- **Phase 1**: New architecture passes unit and integration tests
- **Phase 2**: Single module successfully converted and functioning
- **Phase 3**: All modules converted, system performance equal/better
- **Phase 4**: Agent capabilities demonstrated, safety validated

### Adapter implementation requirements

- **Pipe class structure**: Maintain OpenWebUI's `Pipe` class with `Valves` for configuration
- **Method mapping**: `pipe(body, __user__)` → Request envelope transformation
- **Response format**: Return markdown string compatible with OpenWebUI rendering
- **Error handling**: Convert internal errors to user-friendly markdown messages
- **File handling**: Support OpenWebUI's file attachment system via `body.get("files", [])`
- **Streaming**: Implement OpenWebUI's streaming interface if supported

### Current system migration path

- **Phase 0 - Compatibility layer**: Wrap existing functions in new module format
- **Gradual transition**: Convert one module at a time while maintaining functionality
- **Testing strategy**: Validate each converted module against existing behavior
- **Rollback plan**: Maintain old system until new system is fully validated

### Configuration preservation

- **Docker volume mounts**: Maintain `/host_scripts` mounting pattern
- **Environment variables**: Preserve existing `.env` file structure
- **Security model**: Keep existing permission and access patterns
- **GPU access**: Ensure CUDA/GPU passthrough continues working
    

---

## 15) Autonomous agent validation summary

### Architecture completeness ✅
- **Clear contracts**: All interfaces explicitly defined with schemas
- **Manifest-driven**: Self-documenting modules with discoverable capabilities
- **Safety framework**: Built-in protection against dangerous operations
- **Migration path**: Concrete steps from current to target architecture
- **Execution guidance**: Phase-by-phase implementation roadmap

### Agent implementation readiness ✅
- **Code generation**: Templates and scaffolding for rapid development
- **Validation automation**: Automated testing and compliance checking
- **Safety automation**: Risk assessment and confirmation flow generation
- **Monitoring integration**: Health checks and failure detection
- **Recovery procedures**: Automated rollback and restoration capabilities

### Production deployment readiness ✅
- **Scalability**: Stateless design, async dispatch, horizontal scaling
- **Reliability**: Error handling, retries, graceful degradation
- **Observability**: Comprehensive logging, metrics, and tracing
- **Security**: Least privilege, sandboxing, audit trails
- **Maintainability**: Schema-driven, versioned, self-documenting

### OpenWebUI compatibility ✅
- **Seamless integration**: Maintains existing pipe interface
- **Response formatting**: Consistent markdown output
- **Streaming support**: Real-time response capabilities
- **File handling**: Upload/download compatibility
- **Error presentation**: User-friendly error messages

This architecture provides a complete, implementable blueprint for autonomous agent-driven refactoring of the AI Stack pipe system, with clear success criteria and validation checkpoints.
