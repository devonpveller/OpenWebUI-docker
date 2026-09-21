# sl-docs-posture - findings

Findings sink for the `sl-docs-posture` harness item (stack-layers), which is a
DOCUMENTATION-ONLY item: it writes down the local-first / cloud-capable posture
(DECISIONS D14, resolved 2026-09-20) and replaces the `stack.py init --force`
advice with `enable`. Anything that turned out to be a real problem in the
compose files, the configs or the scripts is written here instead of being
fixed, because fixing it would put a non-Markdown change in this item's diff.

**Attempt 1 FAILED (T1, T3, T8) and the method changed.** The component set was
originally derived from a grep for provider names, which missed four unprofiled
services outright. It is now derived from the RENDERS - see F12 - and F6 was
rewritten because its original claim was false. F7 through F12 are new in
attempt 2.

Everything below was measured on 2026-09-20 in the worktree
`.claude/worktrees/wt-sl-docs-posture` (branch `work/sl-docs-posture`, based on
`development` at `a2d3644`), by reading the named file to the end or running the
named command. No container, `:local` image or `ai-stack_*` network was touched.

---

## F1 - `ao-egress` does not have the allowlist its compose file gives it, and
## would DENY openrouter.ai

**Severity: real, fails closed.** The agent-org `cloud` profile would not work
as shipped if the operator turned it on today.

What the compose file says (`agent-org/docker/docker-compose.yml`, service
`ao-egress`):

```yaml
  ao-egress:
    build:
      context: ../../little-coder
      dockerfile: docker/Dockerfile.egress
    image: little-coder-egress:local
    profiles: ["cloud"]
    environment:
      - EGRESS_ALLOWLIST=${AO_EGRESS_ALLOWLIST:-openrouter.ai}
```

and the header above it: *"Allowlist egress proxy pinned to openrouter.ai
(mirrors lc-egress). The ONLY internet path in agent-org."*
`agent-org/docker/.env.example` carries `AO_EGRESS_ALLOWLIST=openrouter.ai` with
the comment *"Allowlist for ao-egress (OpenRouter host + CDN as needed)"*.

What the image does. `little-coder/docker/Dockerfile.egress` is nine effective
lines: `apk add tinyproxy`, `COPY docker/tinyproxy.conf
/etc/tinyproxy/tinyproxy.conf`, `COPY docker/egress-allowlist.txt
/etc/tinyproxy/egress-allowlist.txt`, `CMD ["tinyproxy", "-d", "-c",
"/etc/tinyproxy/tinyproxy.conf"]`. There is no `ENTRYPOINT` and no start script.
`little-coder/docker/tinyproxy.conf` sets `FilterDefaultDeny Yes` and
`Filter "/etc/tinyproxy/egress-allowlist.txt"`, and
`little-coder/docker/egress-allowlist.txt` contains exactly two active patterns,
`^(.*\.)?github\.com$` and `^(.*\.)?githubusercontent\.com$`.

**Nothing in that image reads `EGRESS_ALLOWLIST`.** `ao-egress` overrides no
`entrypoint`, no `command` and no volume onto that path, so the variable is
inert and the effective allowlist is the baked GitHub pair. With
`FilterDefaultDeny Yes`, a `CONNECT openrouter.ai:443` from `llm-gateway-cloud`
(whose `HTTP_PROXY`/`HTTPS_PROXY` point at `ao-egress:8888`) is refused.

`ao-git-egress`, in the same file, is the one that works: it mounts
`./egress/tinyproxy.conf` over the image's copy (its `Filter` points at
`/egress/egress-allowlist.txt` on the shared `ao-egress-config` volume) **and**
overrides `command:` to `/egress-reload.sh`, which seeds that file and SIGHUPs
tinyproxy when `agent-bridge` rewrites it. So the mechanism exists one service
away; `ao-egress` simply does not use it.

**Not fixed here** (compose/image change). A fix is one of: give `ao-egress` the
`ao-git-egress` treatment (its own conf + a start script that writes the filter
from `EGRESS_ALLOWLIST`), or add the OpenRouter pattern to the baked
`egress-allowlist.txt`, or drop the misleading `environment:` block. Whoever
takes it should note that the `cloud` profile is still gated on the unresolved
P0.5 decision (agent-org `IMPLEMENTATION-NOTES.md`, OD-10: *all-local, Pc
skipped*), so this is latent rather than urgent.

The posture text states this truthfully rather than repeating the compose
header's claim: root `README.md` ("Before you enable the agent-org cloud lane,
read its allowlist") and `agent-org/README.md` (the same, with the mechanism).

---

## F2 - three NON-Markdown files still name `init --product research --force` as
## the operator's step, and a test pins one of them

This item's diff is Markdown only, so these were left alone. Each is the same
wrong advice the five documentation sites carried: on a host that already has a
`.stack/state.json`, `init --force` REPLACES that file (see F3's measurement)
and `enable research` is the verb.

| File | What it says | Note |
|---|---|---|
| `scripts/stack/stack.py`, in `cmd_inventory` | the `[ ~~ ]` follow-up line printed under `declared, not rendered`: *"run `python scripts/stack/stack.py init --product research --force` (or `enable research`) once"* | Guarded by `if inventory.declared_not_rendered:`, and `scripts/stack/README.md` records that the set is empty for every plane since the `fe3e045` bump - so the line is currently unreachable. Changing the string is a code change AND a test change: `scripts/stack/test_stack.py` asserts `"init --product research" in out`. |
| `scripts/stack/stack.ps1`, header comment | *"The operator closes that ONCE, either with `stack.py init --product research --force` (writes all four to .stack/state.json) …"* | A comment in a PowerShell file; ASCII no-BOM rules apply to any edit. |
| `stack.manifest.toml`, the `[planes.ob1.profiles.*]` preamble | *"The operator closes that either way, and both were measured to work: `stack.py init --product research --force` (or `enable research`) …"* | A comment in the manifest. |

Suggested wording for all three, matching what shipped in the docs: **"`stack.py
enable research` merges the four profiles into the existing state file; `init
--force` would REPLACE that file - use it only on a host you intend to start
over."**

---

## F3 - `init --force` REPLACES, `enable` MERGES: the measurement

Not a defect - the measurement this item's claims rest on, recorded so a tester
does not have to re-derive it from the source.

From the source: `cmd_init` (`scripts/stack/stack.py`) constructs
`fresh = State({}, path, False)`, applies the selection to `fresh`, and saves
`fresh` - the previous contents are never read. `cmd_enable` operates on the
`state` the CLI loaded and calls `state.save()` on it. `init --product X` calls
`cmd_enable(…, fresh, …)`, which is why both print the same summary block.

Run against two scratch state files under the session scratchpad, both seeded by
`enable frontend`, `enable inference`, `enable memory`, `enable search`,
`enable coder`, `enable agent-org`:

```text
BEFORE (both): agent-org, coder, frontend, inference, memory, search      (6)

$ python scripts/stack/stack.py --state <scratch-1> init --product research --force
enabled product research:
  inference
  frontend
  search
  ob1  profiles: idea-refinery, research, wiki, notebook
state: <scratch-1>
wrote <scratch-1>
  … then the full `list` dump …
AFTER: frontend, inference, ob1, search                                   (4)
       memory, coder and agent-org are GONE

$ python scripts/stack/stack.py --state <scratch-2> enable research
enabled product research:
  inference
  frontend
  search
  ob1  profiles: idea-refinery, research, wiki, notebook
state: <scratch-2>
AFTER: agent-org, coder, frontend, inference, memory, ob1, search         (7)
       ob1 profiles: idea-refinery, research, wiki, notebook
```

The first four lines of output are identical. Only `init` adds `wrote <path>`
and the `list` dump. Without `--force` on an existing file:
`refused: <path> already exists (re-run with --force to overwrite it)`, exit 1.

**Why it matters here:** the main checkout's `.stack/state.json`, read 2026-09-20
(read-only, not modified), lists **seven** planes - `anchor, coder, frontend,
inference, memory, ob1, search`. `init --product research --force` run there
would leave `frontend, inference, ob1, search`, dropping `coder`, `memory` and
the explicit `anchor` entry, while printing a summary that looks like success.
Note that this is already one plane MORE than the six `sl-ob1-gitlink` recorded
on 2026-09-20 (`anchor, coder, frontend, inference, memory, search`) - `ob1` has
been enabled since, which is that item's landing step being taken. The state
file moves; the replace/merge distinction does not.

---

## F4 - the `init --force` grep survivors in `documentation/evidence/`, and why
## they were left

`git grep -n 'init --force'` and `git grep -nE 'init.*--product'` also hit four
sealed evidence files. None was edited: a test plan is a record of what a tester
executed, and rewriting the command in it falsifies the record. Each is the
fresh/scratch case or a statement about what the driver prints:

| File | Why it is not an instruction to an existing host |
|---|---|
| `documentation/evidence/sl-ob1-gitlink/test-plan.md` (T8, T15, and the env-prep row) | Every invocation carries `--state C:/…/t8.json` or `t15.json` - a throwaway path. T8's own title is the measurement. |
| `documentation/evidence/sl-driver-parity/test-plan.md:846` | Describes what `inventory --check` PRINTS (the `stack.py` string in F2), not what to run. |
| `documentation/evidence/sl-driver-parity/test-plan.md:1195` | An out-of-scope note: "the OB1 gitlink bump and the one-time `stack.py init --product research` that follows it". Superseded by the correction added to `documentation/notes/stack-layers-sl-driver-parity-findings.md`, which is the live note. |
| `documentation/evidence/stack-layers/sl-manifest-test-plan.md:631-634`, `documentation/evidence/sl-ob1-profiles/test-plan.md:380` | Both run against `--root "$SL/root" --state "$SL/root/t.json"`, a scratch tree. |

The two live notes DID get a dated correction rather than a rewrite:
`stack-layers-sl-ob1-gitlink-findings.md` (section 4) and
`stack-layers-sl-driver-parity-findings.md` (the "one-time step" paragraph). In
both, the original text stands and the correction sits beneath it.

---

## F5 - checked, not a defect: `AO_GITHUB_APP_ID` is absent from
## `agent-org/docker/.env.example`

The posture tables name `AO_GITHUB_APP_ID` + `AO_GITHUB_APP_OWNER` as what turns
the capability plane on, and a reader will look for them in the example and not
find them. That is deliberate and documented: `agent-org/README.md` states that
"the ones the compose file gives a `${VAR:-default}` are deliberately absent",
and both are interpolated as `${AO_GITHUB_APP_ID:-}` / `${AO_GITHUB_APP_OWNER:-}`.
No check enforces example/compose parity for this plane (`scripts/checks/` has
`check-env-file-scope.ps1`, which polices the opposite direction - a service
taking a wildcard `env_file`). Both posture sections say the names are absent
from the example and why, so the reader is not left hunting.

---

## F6 (REWRITTEN after attempt 1 FAILED) - THREE containers reach the internet
## from a fresh clone's examples, not one

The original F6, and the README sentence it backed, said the search plane's
`vpn` was "the one egress container that starts from a fresh clone's examples".
That is false, and it contradicted the artifact's own `lc-egress` row two tables
above it. The tester caught it; this is the corrected measurement.

Derived by rendering every plane from the seeded examples
(`docker compose config --format json`) and classifying each service by the
internality of the networks it joins, then reading the source of each candidate:

| Container | Plane | Why it reaches the internet |
|---|---|---|
| `vpn` | search | Unprofiled; brings up a WireGuard tunnel to Mullvad itself at start. The plane's purpose. Cannot connect on the example's placeholder key. |
| `openwebui-backup` | frontend | Unprofiled, renders under `stock` - i.e. in the QUICKSTART deployment - and its `command:` begins `apk add --no-cache pigz` on every container start, against the Alpine CDN. The compose comment beside its `networks:` list says exactly this. It is the only runtime package install in any compose file here. |
| `lc-egress` | coder | Unprofiled; dual-homed on `lc-net` (`internal: true`) and the project bridge. It initiates nothing itself, but it is up and it is `open-terminal`'s only route out. |

`search`'s `vpn` is still the only one that is *meant* to carry traffic, and the
placeholder-key point is still true. What was wrong was the absolute.

---

## F7 - `openbrain-mcp` fetches any URL a caller names, unproxied, and nothing
## gates it

**Severity: real, and the broadest egress in the stack.** Not fixed here
(documentation-only item); it wants an owner.

`OB1/integrations/kubernetes-deployment/index.ts`, inside `ingestOne`, which
backs the `ingest_url` and `ingest_urls` tools, calls `fetch(url)` with
`redirect: "follow"` and a `User-Agent` header and nothing else. The URL is
caller-supplied and redirects are followed. A search for `proxy` over that whole
file returns **zero** hits - unlike `openbrain-research`, which builds a proxied
client from `FETCH_PROXY_URL`. `openbrain-mcp` carries no compose profile and
renders on `obnet` (a bridge), so it is live whenever Open Brain is.

**And it is reachable from outside.** `openbrain-gateway/app.py`'s default write
allowlist is `WRITE_TOOLS = _tool_set("GATEWAY_WRITE_TOOLS", {"capture_thought",
"ingest_url", "ingest_urls"})`, so a remote client holding
`OPENBRAIN_GATEWAY_KEY` can name a URL and have this host fetch it. The cloud
door is therefore not simply "a door in": the gateway process makes no outbound
call, but two of the tools it forwards do. The root `README.md` row was
corrected to say this rather than "nothing outbound".

Options for whoever takes it: give `ingestOne` the proxied client
`openbrain-research` already builds; or drop `ingest_url`/`ingest_urls` from the
cloud door's default write allowlist (a `GATEWAY_WRITE_TOOLS` override does that
with no code change); or both. Both are OB1-side or env-side changes.

---

## F8 - `openbrain-grounding-backfiller` fails OPEN: its proxy fallback is
## direct, and defaults to on

`OB1/integrations/grounding-backfiller/index.ts` sets `WIKI_BASE` to
`https://en.wikipedia.org` by default and `REFETCH_ALLOW_DIRECT` to `"true"` by
default, and its `refetchOne` tries the proxied fetch first, then - when the
result is thin and `REFETCH_ALLOW_DIRECT` is on - repeats it DIRECT.

The service is unprofiled, on `obnet` + `llm-net` + `ai-stack_default`, and its
`FETCH_PROXY_URL` defaults to `http://vpn:8888` exactly like
`openbrain-research`'s. The difference is the fallback: a thin proxied response
silently becomes an unproxied one. `REFETCH_ALLOW_DIRECT=false` in
`OB1/docker/.env` closes it.

This is why the posture table now says "**this one** connects TO the privacy
boundary" on the `openbrain-research` row and warns against generalising: of
OB1's three URL fetchers, one is proxy-bound, one falls back to direct, and one
(F7) has no proxy at all.

---

## F9 - `openbrain-wiki`'s `WIKI_GIT_REMOTE` is the present-but-inert pattern,
## and was missing from the first table

`OB1/docker/docker-compose.yml` sets `WIKI_GIT_REMOTE: ""` with the real SSH URL
commented out directly beneath it and `WIKI_GIT_SSH_KEY` still pointed at a
deploy key. Blank means local-commits-only; restoring the URL force-pushes the
compiled vault to a private GitHub repo. Not a defect - it is exactly the shape
the posture section exists to enumerate, and it is now a row.

---

## F10 - FLAGGED, not settled: `mattermost` telemetry

`mattermost` renders on `ao-net`, a plain bridge, and nothing in this repo sets
`MM_LOGSETTINGS_ENABLEDIAGNOSTICS`. Whether Mattermost Team Edition phones home
on its defaults is an upstream fact that cannot be established from this tree,
so it is listed in the README's "network-capable, no outbound call" section as
FLAGGED rather than cleared, and named again in `agent-org/README.md`. Settling
it means reading the deployed version's defaults or watching the container's
traffic - neither is a documentation change.

---

## F11 - scope: three egressing things are NOT containers

A compose render cannot see them, so the "component" framing had to widen. All
three are in this repo and all three are host processes:

| Thing | The call | Off when |
|---|---|---|
| `scripts/sysadmin-mcp/telegram_notify.py` + `telegram_listener.py` | POST to `https://api.telegram.org/bot{tok}/...`; the LISTENER polls, which makes it an inbound control path into the host that no firewall rule sees | no bot token / chat id |
| `scripts/claude-sessions-bridge/bridge.py` | runs the `claude` CLI headless with `MODEL = os.environ.get("BRIDGE_MODEL", "opus")`, so every turn is a call to Anthropic from the host; also posts to Telegram | the operator does not run it |
| `owui/tools/github_chat_mcp_tools.py`, `owui/tools/fileshed.py` | `GITHUB_API_BASE = "https://api.github.com"`; `fileshed` permits `curl`/`wget` and network `git` subcommands inside the `openwebui` container, behind its own valves | the plugin is not pasted into OWUI - they are deploy-by-paste, tracked in `owui/manifest.csv` |

The claude-sessions bridge is the one place in the repo that talks to a frontier
provider at all, which is why the posture section's opening sentence is scoped
to "no COMPONENT sends a prompt ... by default" and the host-side paragraph
names it immediately after.

---

## F12 - the classification, for anyone re-deriving it

Stage 1 (render, then classify by network) over every plane and profile, with
`external:` names resolved against the anchor (`ai-stack_llm-net` internal;
`ai-stack_app-net` and `ai-stack_default` bridges). Services with NO
non-internal network, i.e. with no route off the host at all:

- **inference**: `llm-gateway`, `llm-gateway-db`, `llm-gateway-backup`,
  `llm-queue`, `llama-cpp-upstream`, `llama-cpp-embed-upstream`,
  `lm-models-backup` - seven of eight. Only `llm-gateway-ui` (on `app-net`) has
  a route, and its source makes no call.
- **memory**: `mnemory`.
- **search**: `redis`, `searxng`.
- **coder**: `little-coder`, `open-terminal`.
- **portal**: `authelia`, `authelia-watcher`, `authelia-notif-bridge`,
  `integrity-tripwire`.
- **agent-org**: `ao-ot-1`, `ao-ot-2`, `ao-worker-1`, `ao-worker-2`.
- **OB1**: none. All 30 are on `obnet`, a bridge, so the whole plane goes to
  stage 2 and the source read is what separates them.

Stage 2 (read the candidate's source for an outbound call) is what puts
`llm-gateway-ui`, the search `gateway`, `openbrain-ext`, the portal watchers,
`status-pipe/` and the backup sidecars other than `openwebui-backup` into
"network-capable, no outbound call", and it is what found F7 and F8.

**Neither stage alone is sufficient**, which is the method lesson of attempt 1:
stage 1 alone would list all 30 OB1 services indiscriminately; stage 2 alone - a
grep for provider names, which is what attempt 1 did - missed F7, F8, F9 and the
`openwebui-backup` `apk add`, every one of them unprofiled and therefore on by
default.
