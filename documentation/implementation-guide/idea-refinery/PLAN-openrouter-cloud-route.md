# PLAN — OpenRouter (cloud) route: grounding + open questions

**Status:** 📐 **PARKED — pending the operator's privacy decision (2026-07-26, corrected).** Purpose:
bigger-model support via OpenRouter — a **cloud** service (a privacy *compromise*, not local). The
operator is **still deciding** whether to be comfortable with OpenRouter accessing Open Brain data (§4);
until that call, nothing is designed. This records only **what actually exists** + the **hard
constraint** (no export of private idea data by default) — no assumptions.

> **Correction to the discarded first draft.** It proposed "cloud-tag an idea → route its research/
> brainstorm through OpenRouter." That is **wrong and unsafe**: (1) it misread the cloud-tag system,
> and (2) it would **export** the idea + its dossier to a cloud model. Retracted.

---

## 1. How cloud tags ACTUALLY work (ground truth, `openbrain-gateway/app.py`)

`openbrain-gateway` is an **inbound** privacy reverse-proxy in front of `openbrain-mcp`, used **only by
cloud clients** (ChatGPT / Claude Desktop / Claude Code). It is *access control on what a cloud client
may READ*, not a way to send data out:

- **Cloud READS are force-filtered to `metadata.share == "cloud"`** (`_force_read_filter`, non-
  overridable). A cloud client can retrieve **only** rows tagged `share=cloud`; all local/personal
  data is **physically not returned** (default-deny). Enforced server-side (`metadata @> $::jsonb`).
- **The tag is set at data-entry:** cloud-originated writes are stamped `origin=cloud, share=cloud`
  (`_force_write_extra`); local writes carry **no** cloud share by default. So "cloud-visible" ≈
  "created by / imported for cloud," decided when the data enters.
- **Default-deny tool allow-list:** only `search/fetch/search_thoughts/list_thoughts` (filtered reads)
  + `capture_thought/ingest_url(s)` (stamped writes). Aggregates (`thought_stats`) and everything else
  are **blocked** for cloud.

**Therefore:** re-tagging existing LOCAL data as `share=cloud` = making it retrievable by cloud clients
= **exposing/exporting** it. That is a deliberate, one-way exposure — the exact risk to avoid. The tag
gates INBOUND reads; it is not an outbound-export mechanism, and the Idea Refinery must not treat it as
one.

## 2. The hard constraint for the Idea Refinery

The Idea Refinery is **100% local** by design (qwen36-27b + bge-m3 + private SearXNG/Tor). Sending an
idea's text or its dossier to any cloud model (via OpenRouter or otherwise) is an **export of private
Open Brain data** — there is **no sanctioned outbound-export path** for it today, and the cloud tag is
not one. So there is **no "escalate an idea to cloud" design** under the current privacy policy.

## 3. What actually exists for OpenRouter (don't reinvent, don't overstate)

- **`agent-org` cloud lane** (`agent-org/config/litellm-cloud.config.yaml` +
  `agent-org/docker/docker-compose.yml:446-519`, profile `cloud`): a separate LiteLLM
  (`llm-gateway-cloud`) whose **only** egress is `ao-egress` (allowlist-pinned to `openrouter.ai`).
  Master-key + per-role virtual keys + budgets; ZDR/no-log (`data_collection: "deny"`).
  - **NOT deployed** — the `cloud` profile has never been brought up; it is **conditional** on a
    capability-floor test (only if local 27B judgment proves too weak).
  - **The model ids are PLACEHOLDERS** ("operator-chosen at Pc.1") — there is no real OpenRouter model
    configured yet. Do not cite one as available.
  - Its documented privacy boundary: **governance-level SUMMARIES only, never raw data/secrets.**
- There is **no Idea-Refinery-specific** OpenRouter route, key, or model. Nothing to build on yet.

## 4. Purpose (operator): bigger-model support via OpenRouter — a privacy compromise still under consideration

The route is to give idea refinement access to a **bigger model hosted as a cloud service** (far more
resources than the local plane), reached via OpenRouter.

**What OpenRouter is (operator's framing):** a **cloud service** — **a step closer to private** than raw
cloud, but **not local**. Sending an idea to it means that idea's Open Brain data is accessible to
OpenRouter.

**Decision status — OPEN, and the operator's alone.** The operator is **still deciding how comfortable
to be** with OpenRouter having access to Open Brain data. This is a privacy/comfort judgment — **not** a
technical fork for me to resolve, push, or assume. Nothing is designed or built until the operator
decides; the local default is untouched meanwhile.

If/when the operator chooses to proceed, the design would then be grounded in the **real** lane
(`agent-org` `llm-gateway-cloud` → `ao-egress`, once its `cloud` profile + an operator-chosen model are
provisioned) with a per-idea, explicit, audited forward-send — never the read-filter `share` tag, never
bulk re-tagging existing data. Until then this is a parked note, not a design.

---

## Appendix — sources
- `openbrain-gateway/app.py` — the cloud read-filter / write-stamp / tool allow-list (§1).
- `agent-org/config/litellm-cloud.config.yaml`, `agent-org/docker/docker-compose.yml:446-519` — the
  (undeployed, placeholder-model, conditional) OpenRouter lane (§3).
