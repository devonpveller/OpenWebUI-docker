# Portable Research Service + Quartz 4 — implementation guides

Two **portable** implementation plans, extracted from the live `ai-stack`
workspace, written so that *any* Open WebUI + local-LLM stack can reproduce the
same behavior in its own setup. They are intentionally workspace-agnostic: where
this workspace uses a specific container name, port, or model, the plan states
the **contract** and gives this workspace's value as the reference default.

These are **two separate efforts**. The research service stands alone. The
Quartz 4 integration is a downstream consumer of it (a read/edit surface over
the same data) and is planned as its own deliverable.

| Plan | File | What it gives you |
|------|------|-------------------|
| **Research service** | [PORTABLE-RESEARCH-SERVICE.md](PORTABLE-RESEARCH-SERVICE.md) | A shared, grounded research harness behind an async job API, an Open Brain ingestion path with a `source → claim` relationship, and an Open WebUI pipeline that triggers it. |
| **Quartz 4 integration** | [PORTABLE-QUARTZ4-INTEGRATION.md](PORTABLE-QUARTZ4-INTEGRATION.md) | A Quartz v4 wiki that renders the Open Brain knowledge base (threads / sources / claims / research syntheses) as a browsable, editable site, with the same build/publish-gate discipline that keeps it from serving half-built output. |

## Reading order

1. Read **PORTABLE-RESEARCH-SERVICE.md** first. It establishes the data model
   (`sources`, `claims`, `claim_sources`, `threads`) that Quartz reads.
2. Read **PORTABLE-QUARTZ4-INTEGRATION.md** only if you want the read/edit
   surface. It assumes the research service's schema is already present.

## Source of truth

These plans are derived from this workspace's live implementation:

- Research harness/curator: `OB1/integrations/research-service/`,
  `OB1/integrations/research-curator/`
- Schema: `OB1/docker/init-claims.sql`, `init-threads.sql`, `init-sources.sql`,
  `init-research-jobs.sql`, `init-source-chunks.sql`
- OWUI inlet: `owui/tools/deep_research.py`, `smolcrawl/deep_research_thin_client.py`
- Quartz viewer/compiler/workbench: `OB1/docker/wiki-viewer/`,
  `OB1/recipes/entity-wiki/`, `OB1/docker/workbench/`
- Internal design docs: `documentation/implementation-guide/research-engine-for-OB/`,
  `documentation/implementation-guide/expand-quartz-4/`

If you are implementing inside *this* workspace, prefer those internal docs —
they carry workspace-specific deployment runbooks. These portable plans strip
that out so they travel.
