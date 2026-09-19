# sl-compose-anchors — findings

Item: `sl-compose-anchors` (stack-layers PLAN 2.6 / CLEANUP-PLAN D.1), worktree
`wt-sl-compose-anchors`, base `development` @ 4934529, 2026-09-19.

Everything true that the item turned up and did not act on. **Each entry is
labelled with how it is known**: *read from source* (file + the construct, with
the line number as of this branch), *measured* (a command was run, and when), or
*not verifiable from this tree*.

---

## A. Compose semantics this item had to establish before it could build

### A1. A YAML anchor in the `include:` spine is NOT visible in an included file
*measured, compose v5.3.0, 2026-09-19.* A scratch project whose spine declared
`x-hardening: &hardening` and whose included file referenced `<<: *hardening`
does not render at all:

```
failed to parse <...>/sub/g1.yml: yaml: line 3, column 10: unknown anchor 'hardening' referenced
```

Each file is parsed on its own before the models are merged. That is why
`inference/` needs FOUR `x-hardening` declarations (one per group file) and the
spine `inference/docker-compose.yml` holds none — it has no services to attach
them to and could not lend them out if it did. Recorded in the spine's
`include:` bullet list (third bullet) and in each group file's header.

### A2. A top-level `x-` key in an INCLUDED file never reaches the merged model
*measured, compose v5.3.0, 2026-09-19.* Two included files declaring
`x-hardening` with **different** contents render clean — no include conflict,
and neither block appears in `docker compose config` output. Only the spine's
own top-level `x-` keys are echoed.

This is the reason the per-file declarations in `inference/compose/*.yml` are
kept identical **by convention rather than by a check**: divergence there fails
silently, not loudly. The first draft of those four headers claimed the opposite
("byte-identical, which is why it is not an include conflict"); the claim was
refuted by the experiment above before it was committed, and the headers now say
what was measured.

### A3. Elsewhere, `config` DOES echo top-level `x-` keys — so criterion 1 needs a stated normalization
*measured, 2026-09-19, all fifteen plane × profile renders.* For every plane
whose `x-` blocks live in the file compose is pointed at (all of them except
inference), the rendered JSON gains the `x-*` keys at top level. So the anchor's
criterion 1 — "byte-identical normalized render" — is satisfied **after** the
normalizer drops top-level `x-` keys; the `services`, `networks` and `volumes`
halves are byte-identical with nothing stripped.

The raw delta was checked structurally rather than asserted: across all fifteen
combinations the only difference is ADDED top-level `x-` keys —
`removed_top_keys=[]`, `changed_common_keys=[]` in every one, and inference's
two renders differ in nothing at all.

---

## B. The anchor's own numbers were stale

*read from source, this branch's base 4934529.* The anchor's criterion 3 lists
"frontend 3, inference 8, memory 3, search 4, coder 4, portal 12". Measured
declaration counts on the base commit:

| file | anchor said | actually |
|---|---|---|
| `frontend/docker-compose.yml` | 3 | **4** (openwebui-stock, openwebui, openwebui-backup, tailscale-backup) |
| `inference/` | 8 | 8, but split **2 / 1 / 3 / 2** across `compose/{upstreams,queue,gateway,backups}.yml` and **0** in the spine |
| `memory/docker-compose.yml` | 3 | 3 |
| `search/docker-compose.yml` | 4 | 4 |
| `coder/docker-compose.yml` | 4 | 4 |
| `portal/docker-compose.yml` | 12 | 12 |
| `agent-org/docker/docker-compose.yml` | *not listed* | **16** |

The ten files carried **51** declarations between them before this change and
carry **10** after. (Commit `d50fdec`'s message says "forty-eight"; that total
is wrong - the per-file numbers in it and in the table above are not.)

The frontend miscount predates the `stock` profile (sl-frontend-solo added a
fourth service carrying the block). agent-org is named in the anchor's
`artifact` field but omitted from the count list. The criterion was tested at
the real counts, and the difference is recorded here rather than by quietly
re-reading the criterion.

---

## C. Citations already broken on `development`, found by this item's sweep

The sweep re-derives every `path:line` into the eleven files whose line counts
changed. C0 states what "every" counts. C1 and C2 were already wrong before
this branch existed; C3 is a defect this branch introduced and fixed, kept here
because the way it evaded every gate is the transferable part.

### C0. What the sweep counted, since the first report gave a number with no unit

*measured against this branch, 2026-09-19.* The earlier figure "97 citation
points" was the raw hit count of a scratch scanner, false positives included
(bare ports such as `:8445`, continuations belonging to `frontend/entrypoint.sh`
rather than to the compose file named earlier on the same line, and `OB1/`
paths this item does not touch). It is retracted. The reproducible figures:

| unit | count |
|---|---|
| files citing into the eleven changed compose files | 3 |
| LINES in them naming at least one such line number | 35 |
| individual line NUMBERS those lines name | 66 |
| of those, numbers this branch changed | 65 |

The one that did not move is `inference/compose/upstreams.yml:24`, which sits
above the inserted `x-` block. The three files are `stack.manifest.toml`,
`.env.example` and `CLEANUP-PLAN.md`; `documentation/archive/**` cites the
2,249-line pre-split ROOT compose, a different file, and earlier items' evidence
records are out of scope by the precedent in `a2e4e3e`.

### C1. `stack.manifest.toml` — both portal citations off by exactly 4 lines
*read from source, `git show development:portal/docker-compose.yml`.*

* `# requires anchor: portal/docker-compose.yml:603-605 (app-net, external: true, name: ai-stack_app-net)` — on `development` :603 is `notify-net:` and :605 is a comment. The `app-net:` / `external: true` / `name: ai-stack_app-net` run is **607-609**.
* `# requires frontend: portal/docker-compose.yml:601-602 - caddy joins ai-stack_app-net "to reach openwebui:8080 and open_notebook"` — the quoted comment is at **605-606**; :601-602 are the `notify-net` comment pair.

Both now point at the constructs they describe (`:593-595` and `:591-592` on this
branch). A reader following the old numbers landed on the wrong network.

### C2. `CLEANUP-PLAN.md:755` — neither cited line was a build
*read from source.* The text is "agent-org egress builds at
`agent-org/docker/docker-compose.yml:440,516`". On `development` :440 is
`profiles: ["workers"]` (inside `ao-ot-2`) and :516 is
`OPENROUTER_API_KEY: ${OPENROUTER_API_KEY}` (inside `llm-gateway-cloud`). The two
egress `build:` keys were at **481** (`ao-git-egress`) and **557** (`ao-egress`)
— an offset of 41, i.e. written against a revision of the file that predates
~41 lines of insertions. Now `:520,590`.

### C3. Two header lines this branch wrote were never wrapped (F1, fixed)

*read from source; the defect and the fix are both on this branch.*
`inference/compose/backups.yml` carried a 171-character comment line containing
the four characters `\n#` as literal text, mid-sentence, where a line break was
intended; `search/docker-compose.yml` carried a 114-character one. Both headers'
sibling lines wrap between 54 and 88.

The mechanism is worth more than the fix. These headers were produced by a
generator invoked once per file, and the per-file tail was passed to it as a
shell argument that contained a `\n` escape inside single quotes — so nothing
ever turned it into a newline, and Python then inserted the two characters
verbatim. It survived every gate the item had: the YAML is valid (it is a
comment), the render is byte-identical, `check-project-configs` is green, and
the test plan had ten cases and not one of them read the prose. The anchor names
"the compose headers noting the extension fields" as part of the artifact, and
half the artifact was going untested. T11 exists because of this.

The fix moved `inference/compose/backups.yml` from 117 to 119 lines, so the
citations INTO it moved a second time: `.env.example`'s `:35,53` -> `:37,55` and
`:62,96,103-105` -> `:64,98,105-107`, and `stack.manifest.toml`'s
`backups.yml:69` -> `:71`. `search/docker-compose.yml` is unchanged in length
(two lines became two), so its citations did not move.

---

## D. True, out of scope, and not acted on

### D1. `SECURITY.md` overstates the portal hardening floor for two services
*read from source, `portal/docker-compose.yml` on this branch.*
`SECURITY.md:55` ("Every portal container: `read_only: true`, `cap_drop: [ALL]`,
`no-new-privileges: true`, `tmpfs /tmp`, non-root UID (10000–10007)") and
`SECURITY.md:163` (same claim, "Audit-verified 2026-05-29") are true of ten of
the twelve services. The exceptions:

* **`portal-init`** has no `read_only` key, no `tmpfs`, and `user: "0:0"` — by design: it exists to `chown` the volumes, and its own comment says `root — required to chown volumes`.
* **`portal-cron`** carries no `user:` key at all (the service block runs from `portal-cron:` to `cloudflared:` with none).

Nothing changed here: `portal-init`'s exception is now *visible* rather than
implicit, because it is the one service that merges `*hardening` instead of
`*hardening-ro`. Fixing the SECURITY.md sentences is a documentation item; the
safe wording is "every long-running portal container", which is what the code
supports.

### D2. `documentation/runbooks/backup-conventions.md` teaches the longhand
*read from source.* Ingredient 2 ("Compose-level hardening") and the copy-paste
template at :94-125 still show an inline `security_opt:` / `cap_drop:` block —
and a `crond` scheduler, which every sidecar in this repo moved off on
2026-08-21 in favour of the sleep loop. A sidecar added to `frontend/` or
`agent-org/docker/` today should merge that file's `x-backup-sidecar` instead.
Left alone deliberately: the anchor's artifact is the compose files and their
headers, and the runbook is a lifecycle surface with its own item.

### D3. CLEANUP-PLAN D.1's anchor list is now three-quarters delivered
*read from source, `CLEANUP-PLAN.md:549-551`.* D.1 names four anchors to
introduce: `x-hardening`, `x-watchtower-disable`, `x-healthcheck-http`,
`x-backup-sidecar`. Three now exist per plane file. **`x-watchtower-disable` is
not done**: `- "com.centurylinklabs.watchtower.enable=false"` is still written
out **47 times** across the nine plane files (frontend 3, inference 2+1+3+2,
memory 3, search 4, coder 4, portal 12, agent-org 13). It is a single-element
list on `labels:`, so it carries exactly the replacement hazard this item is
about, and it was not in this anchor's artifact. D.1's own counts ("44
`security_opt` copies", "43 labels") describe the 2,249-line pre-split root
compose file that no longer exists; they are historical, not current.

### D4. `read_only: false` is invisible in the render
*measured, 2026-09-19.* Compose drops `read_only: false` (the default) from
`config` output, so the two `openwebui` services show no `read_only` key in
either the before or the after render. The per-service comparison therefore
proves that pair unchanged from the SOURCE (both carried it, both still resolve
it through `*hardening-owui`), not from the render. A tester checking only the
render cannot tell `read_only: false` from its absence — which is also why the
key was put into an anchor rather than deleted as a no-op.

### D5. `tailscale` has no `security_opt` and did not gain one
*read from source, `frontend/docker-compose.yml`.* The netns companion carries
`cap_add: [NET_ADMIN, NET_RAW]` and no `security_opt`. It does not merge
`*hardening`. Adding one would be a security change with its own item
(explicitly out of scope here), and the resolved-value table confirms it still
renders with an empty `security_opt`.

### D6. No `logging:` block exists on any service in any plane file
*read from source.* The anchor's artifact mentions `x-logging` "if present". It
is not present anywhere in the nine plane files, so no such anchor was created.

---

## E. What was deliberately NOT anchored

*read from source.* Recorded so the next reader does not mistake these for
misses:

* **`tmpfs` sizes in portal** run 4m / 16m / 32m / 64m per service. That is a per-service sizing decision, not a policy, and folding it into `x-hardening-ro` would have forced nine services to override a list — the exact replacement hazard this item exists to avoid.
* **`healthcheck.test`** stays explicit everywhere. Only the timing sets are anchored, and only where every one of `interval`/`timeout`/`retries`/`start_period` matched — except in `portal` and `agent-org`, where the anchor holds the three keys that matched and `start_period` stays on the service (15s vs 30s in portal; 40s vs 30s in agent-org). Each file's header says which.
* **`restart: unless-stopped`** is not in `x-hardening` anywhere. It is in the two sidecar anchors, because "the sidecar shape" is what those describe.
* **`inference/compose/queue.yml` has one service**, so its `x-hardening` de-duplicates nothing on its own. It is declared anyway so all four group files read the same way; the header says so rather than implying a saving that is not there.
