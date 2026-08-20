# Anthropic resources for the multi-agent coding org (research, 2026-07-03)

**Why this doc.** The operator asked whether Anthropic ships resources that would let us *stop
hand-building every permutation* of the agent-org. This maps each relevant Anthropic/Claude resource
to what we've already built (`agent-org/agent-bridge`) and the delivery pipeline we've designed
(`DELIVERY-PIPELINE.md`). Advisory — nothing here changes code; it de-risks DP.1–DP.6 + D0.f.

**One-line answer.** Keep hand-building the *governance* (it's genuinely ours); adopt off-the-shelf
Anthropic primitives for the *plumbing* — git/PR/fork via the **GitHub MCP server**, and optionally
the **Agent SDK**'s hook + permission + session machinery to thin our worker harness.

---

## The resources, mapped to our build

### 1. Claude Agent SDK — `claude-agent-sdk` (Python + TypeScript)
The Claude Code agent loop as a library: tool execution, context management, session/resume,
subagents, permissions, an MCP client, and **PreToolUse/PostToolUse hooks**.
- Docs: https://code.claude.com/docs/en/agent-sdk/overview
- Repos: `anthropics/claude-agent-sdk-python`, `anthropics/claude-agent-sdk-typescript`

| We hand-built | SDK gives |
|---|---|
| worker harness (wake/poll) | the agent loop + tool execution |
| floor as a bridge classifier + open-terminal filter | **PreToolUse hook** returning allow/deny/ask before every tool call |
| wake/resume by session | native `resume=session_id` |
| worker scope grants | `allowed_tools` + `permission_mode` |

**Does NOT** provide: our governance FSM (floor/steering/differently-goaled reviewer), or
inter-agent channels — SDK subagents are **intra-session** only (for cross-instance messaging see
Managed Agents).

### 2. GitHub MCP server — `github/github-mcp-server`
First-class MCP server: PRs, branches, reviews, merges, commits, issues, Actions, **fork +
sync-fork**, cross-fork PRs — all at the API level.
- Repo: https://github.com/github/github-mcp-server

Directly replaces shell-git in **two** places we've designed around the little-coder git-proxy:
- **D1/D4 (PR → merge):** `create_pull_request` / `pull_request_review_write` / `merge_pull_request`
  instead of wrapping `gh`/REST by hand.
- **D0.f (fork/upstream):** `create_fork` + "sync fork" + a cross-fork PR (head `fork:branch`, base
  `upstream:main`) — **no in-workspace `remote add`** at all, which is the whole difficulty the
  git-proxy imposes.

**Known gap:** no per-call token injection. We already solve this with the per-owner token broker
(`LC_<OWNER>_TOKEN`, `projects.owner_token_env`) — wire the resolved token as the MCP server's
`GITHUB_TOKEN` per project/agent.

### 3. "Building Effective Agents" (Anthropic research guide)
The design north-star. Validates our architecture as *named* patterns, not ad-hoc:
- **Orchestrator-Workers** = our PO/PM → little-coder workers.
- **Evaluator-Optimizer** = our differently-goaled reviewer loop.
- Principle we already follow: *simple composable patterns, add complexity only when it pays.*
- https://www.anthropic.com/research/building-effective-agents

### 4. Managed Agents (platform beta)
Hosted multi-agent + **event-driven messaging between separate agent instances** + persistent
sessions + MCP. An alternative to building our own inter-agent bus **if** we want cross-instance
comms (worker→PM status, PM→reviewer) without owning the transport. Trade-off: Anthropic stores
session state. For our already-built FastAPI bridge, the Agent SDK is lighter; Managed Agents matters
only if we want event-driven comms we'd otherwise hand-roll.
- https://platform.claude.com/docs/en/managed-agents/overview

### 5. Anthropic Cookbook + Headless
- **Cookbook** — runnable recipes; the **"Chief of Staff" agent** ≈ our PO/PM (triage → delegate →
  collect). https://platform.claude.com/cookbook · https://github.com/anthropics/claude-cookbooks
- **Headless / channels** — CI-style unattended dispatch + remote state query (worker progress) — the
  built-in analog of our Fix 1 visibility. https://code.claude.com/docs/en/headless

### 6. MCP ecosystem (reference servers)
`modelcontextprotocol/servers` — Git, Filesystem, Memory, Fetch. The standard tool transport; already
integrated by the Agent SDK's `mcp_servers`. Relevant if we expose Mattermost/OB as MCP tools.

---

## Verdict — build vs adopt

**Keep hand-building (genuinely ours):**
- Governance FSM — floor / steering / differently-goaled reviewer charters.
- The Mattermost org surface (channel=project, effort=thread) + comms router.
- Per-project deploy-token routing (the `LC_<OWNER>_TOKEN` broker).
- Plan-approval gates + escalation backpressure.

**Adopt off-the-shelf (stop hand-rolling):**
- Git/PR/fork/merge → **GitHub MCP server** (D1/D4 + D0.f).
- Optionally thin the worker harness with the **Agent SDK** (hooks = our floor, permissions = our
  scope, session/resume = our wake).
- Consider **Managed Agents** only if/when we want inter-agent event transport.

**Caveats.** Agent SDK, MCP, and Managed Agents are all active-development (2026) — pin versions and
check docs at adopt time. GitHub MCP tool names are consolidating. Per-project token scoping remains
a broker concern on our side.

---

_Sources:_ Agent SDK overview · Building Effective Agents · github/github-mcp-server ·
Managed Agents overview · Claude Cookbook · Claude Code headless · modelcontextprotocol/servers
(URLs inline above). Summarized into `agent-org/IMPLEMENTATION-NOTES.md` §"Anthropic resources".
