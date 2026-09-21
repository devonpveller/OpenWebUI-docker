# sl-docs-posture - findings

Findings sink for the `sl-docs-posture` harness item (stack-layers), which is a
DOCUMENTATION-ONLY item: it writes down the local-first / cloud-capable posture
(DECISIONS D14, resolved 2026-09-20) and replaces the `stack.py init --force`
advice with `enable`. Anything that turned out to be a real problem in the
compose files, the configs or the scripts is written here instead of being
fixed, because fixing it would put a non-Markdown change in this item's diff.

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

## F6 - checked, not a defect: the search plane's `vpn` is the one egress
## container that starts from a fresh clone's examples

`search/docker-compose.yml`'s `vpn` carries no `profiles:` key, so it renders and
starts whenever the search plane does. `search/.env.example` ships
`MULLVAD_WG_PRIVATE_KEY=change-me-real-wg-private-key` - a placeholder and not a
blank, because the `${...:?}` guard rejects empty - so the container starts and
the WireGuard tunnel cannot establish. This is the plane's purpose rather than a
leak: `searxng` is on `search-net` (`internal: true`) and
`search/searxng/settings.yml` sets `outgoing.proxies` to `http://vpn:8888`, so
there is no other route out for a search query. The acceptance wording "no
egress service starts" is therefore true of every plane except this one, and
both posture sections say so explicitly rather than letting a tester discover
the exception.
