
---

# Autonomous Coding Agent Guide (Refactored)

## 1. Core Contracts (Explicit & Versioned)

All communication follows a **single keystone contract**, versioned for compatibility:

**Flow:** Request Envelope → Router → Module → Router Return → Formatter

### Request Envelope (Router Input)

* `version`, `request_id` (UUID), `timestamp`
* `user`: id, roles, permissions
* `session`: conversation id
* `locale`, `timezone`
* `input`: text or structured params
* `attachments`: files/URLs
* `context`: prior turns, system hints
* `capabilities_allowed`: allowlist of modules/tools

### Module Call (Router → Module)

* `request_id`, `module_id`, `action` (`execute | help | health`)
* `payload`: JSON validated against schema
* `timeout_ms`, `cancellation_token`, `trace_context`

### Module Result (Module → Router)

* `request_id`, `module_id`, `status` (`ok | error | partial | streaming_end`)
* `content`: markdown blocks + optional structured data
* `artifacts`, `events`
* `usage`, `diagnostics`
* `error`: code, message, retriable?

### Error Envelope (Any Stage)

* `request_id`, `where`, `error_code`, `message`, `details`, `retriable`

### Formatter Output (Router → OpenWebUI)

* Clean markdown with optional tables/lists
* Stable front-matter: `title`, `tags`
* `request_id`

---

## 2. Module Packaging & Manifests

Modules live under `/modules` with a strict structure:

```
/modules/
  <module-slug>/
    module.manifest.json   # required
    env/                   # required (venv or container)
    service/               # required (implementation)
    artifacts/             # optional (outputs/models)
    docs/                  # optional (HELP.md, examples)
```

**Manifest is authoritative** — Router trusts manifests, not source code.

### Required Manifest Fields

* Identity: `name`, `slug`, `version` (semver)
* Entry: `entry.kind` (`cli | http | grpc`), `entry.path`
* Schemas: `schema.input`, `schema.output` (URIs or embedded JSON Schema)
* Capabilities: e.g. `"search"`, `"vision"`
* Limits: timeouts, input sizes, budgets
* Auth: required secrets (scoped, brokered by Router)
* Health: probe definitions
* Environment: `env.kind` (`venv | container`)
* Compatibility: `routerCompatibility`
* Help: `help.short`, `help.long`
* Permissions: optional role allowlists
* Install: optional warmup hooks

---

## 3. Module Isolation & Execution

### Isolation Modes

* **Venv (default)**: fast iteration in local Python environments
* **Container**: for native/CUDA or security-sensitive workloads

### Execution Adapters

* `cli`: subprocess, JSON over stdin/stdout, strict CPU/wall timeouts
* `http/grpc`: local or remote call with `request_id` and budget headers

### Stable Interface

* `describe()` → metadata (schemas, limits, help)
* `health()` → readiness check
* `execute(envelope)` → result envelope (streaming or final)
* `validate(input)` → dry-run validation

---

## 4. Router Responsibilities

Router consists of modular components:

* **Ingress Adapter**: OpenWebUI ⇄ Request envelope
* **Dispatcher**: routing, policy, concurrency, retries, backpressure
* **Egress/Return**: normalization and formatting
* **Registry**: manifest-driven discovery and metadata

### Discovery

* Registry scans `/modules/*/module.manifest.json` on boot/rescan
* Validates schema, probes health, maintains states (`Ready | Degraded | Unavailable`)
* Hot-reload support

### Conflict Resolution

* Multiple versions: keep all, use highest compatible
* Duplicate slug+version: quarantine if mismatched

---

## 5. Reliability & Scalability

* **Queueing**: async per-module queues with DLQ and backpressure
* **Retries**: idempotent ops only, with backoff + jitter
* **Rate Limits**: per-user, per-module, global (`retry_after`)
* **Caching**: memoize results by `<slug>@<version> + normalized input`
* **Cold-start Prep**: warmup hooks for model loads, etc.
* **Load Balancing**: multiple instances, health-aware routing
* **Graceful Degradation**: fallback paths when modules fail

---

## 6. Formatter & UX Consistency

* **Streaming**: token/chunk streams → incremental markdown
* **Uniform UX**: titles, summaries, diagnostics, error cards
* **Markdown Safety**: sanitize, truncate large payloads, escape fences
* **Templates**: reusable cards for errors/confirmations
* **Accessibility**: screen-reader friendly markup
* **Internationalization**: localized output with English fallback

---

## 7. Discoverability & Help

* `help` → list Ready modules (slug, version, caps, `help.short`)
* `help <slug>` → full manifest help (schemas, limits, examples)
* No source introspection permitted

---

## 8. Observability & Operations

* **Logs**: structured with `request_id`, `module_id`, latency, error_code
* **Metrics**: latency (p50/p95/p99), error rates, queue depth, cache hits
* **Tracing**: W3C trace context across components
* **Audit**: persist envelopes (without sensitive payloads)
* **Health Monitoring**: continuous probes with alerts
* **Profiling**: CPU, memory, I/O

---

## 9. Security & Governance

* **Validation**: JSON Schema, size/type limits enforced by Router
* **Least Privilege**: scoped permissions/secrets per manifest
* **Budgets**: per-request limits on time, memory, tokens
* **Secrets**: short-lived, distributed via Router
* **Safety Ratings**: classify modules (`SAFE → DESTRUCTIVE`)
* **Sandboxing**: containers, rollback, resource isolation
* **Encryption**: full end-to-end including stored artifacts

---

## 10. Agent Orchestration

* **Plan–Act–Observe Loop**: tool selection, working memory, long-term store
* **Tool Selection**: Registry metadata + health + cost evaluation
* **Safety Rails**: budgets, confirmations, PII redaction
* **Error Recovery**: retries, alternate modules, graceful degradation
* **Tracing**: log every agent step for audit
* **Learning System**: monitor success/fail patterns to refine policies

---

## 11. Data Modeling & Schemas

* Versioned JSON Schemas for:

  * Request & Result envelopes
  * Module manifests & outputs (`describe()`)
  * Module I/O payloads
  * Agent state & memory
  * Governance/policy configs
* Semantic versioning & deprecation cycles
* Auto-doc generation & validation tools
* Registry integration with schema matching

---

## 12. Developer Ergonomics

* **Scaffold Generator**: auto-create module folder + manifest stub
* **Local Runner**: venv build, health check, test harness
* **Validation CLI**: `validate-manifest`, `validate-schemas`
* **Hot Reload**: manifest watching in Registry

---

## 13. Autonomous Agent Implementation Guidance

### Code Generation

* Provide manifest templates (JSON schema)
* Auto-generate scaffolding from manifests
* Generate validation/test harnesses from schemas
* Auto-generate documentation from manifests

### Migration Automation

* Scan `ai_pipes/` for existing modules
* Extract dependencies and interfaces
* Convert to manifest-driven format
* Validate against original behavior
* Integrate into Router

### Safety Automation

* Classify operations by risk level
* Generate confirmation flows
* Prepare rollback procedures
* Add monitoring and health checks
* Automate recovery for common failures

---

## 14. Implementation Roadmap

### Phase 0 — Analysis & Planning

* Inventory current modules/dependencies
* Create migration matrix
* Identify risky operations
* Verify OpenWebUI compatibility

### Phase 1 — Core Infrastructure

* Define JSON schemas for envelopes
* Implement Router (Ingress, Dispatcher, Egress, Registry)
* Build base module class + adapters
* Add safety framework
* Provide structured logs & basic metrics

### Phase 2 — Module System

* Implement manifest discovery/validation
* Add venv & container execution adapters
* Add health monitoring and error handling
* Provide basic formatter

### Phase 3 — Migration & Integration

* Convert modules to new format
* Build OpenWebUI adapter with streaming
* Add regression testing framework
* Optimize performance (caching, pooling)
* Finalize API docs and guides

### Phase 4 — Advanced Features

* Implement agent orchestrator (plan–act–observe)
* Add memory systems (working, short/long-term)
* Enable adaptive learning and advanced safety policies
* Provide full observability suite

### Validation Checkpoints

* Phase 0: Inventory complete
* Phase 1: Unit/integration tests pass
* Phase 2: First module successfully converted
* Phase 3: All modules converted, perf ≥ baseline
* Phase 4: Agent orchestration validated

### Adapter Requirements

* Maintain OpenWebUI `Pipe` class + `Valves`
* `pipe(body, __user__)` → request envelope transform
* Return markdown-compatible output
* Map errors → user-friendly markdown
* Support file attachments via `body.get("files", [])`
* Implement streaming if supported

### Migration Path

* Wrap existing functions in new format
* Convert incrementally (one module at a time)
* Validate each conversion against current behavior
* Maintain rollback path until fully validated

### Configuration Preservation

* Maintain `/host_scripts` mounts
* Keep existing `.env` patterns
* Preserve permissions/security model
* Ensure GPU passthrough (CUDA)

---

## 15. Validation Summary

**Architecture Completeness ✅**

* Explicit contracts with schemas
* Manifest-driven modules
* Safety framework baked-in
* Concrete migration plan
* Phased roadmap

**Agent Implementation Readiness ✅**

* Templates & scaffolding tools
* Automated validation & testing
* Safety automation & confirmation flows
* Monitoring and recovery procedures

**Production Readiness ✅**

* Scalable, stateless design
* Reliable error handling & fallback
* Comprehensive observability
* Secure with least privilege and audit trails
* Schema-driven maintainability

**OpenWebUI Compatibility ✅**

* Pipe interface preserved
* Markdown output guaranteed
* Streaming supported
* File handling compatible
* Errors user-friendly

---