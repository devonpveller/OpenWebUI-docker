# sl-compose-anchors — test plan

Item `sl-compose-anchors`, branch `work/sl-compose-anchors`, developer worktree
`wt-sl-compose-anchors`, base `development` @ **f291cb3** (the sl-env-split
merge). Rebased onto it 2026-09-19; every case below was re-run against that
base before this revision was submitted.

**What changed.** The hardening / scheduler / healthcheck-timing boilerplate that
the nine plane compose files wrote out longhand is now declared once per file as
a YAML extension field (`x-hardening`, and where they earn their place
`x-hardening-owui` / `x-hardening-ro`, `x-healthcheck-*`, `x-backup-sidecar`,
`x-pg-backup-sidecar`) and merged into the services with `<<: *name`. Each file
gained a header block naming its extension fields and stating the
list-replacement rule. `stack.manifest.toml`, `.env.example` and
`CLEANUP-PLAN.md` had every `path:line` citation into those files re-derived,
because every one of their line counts moved.

**No service gains or loses a setting.** That is the whole claim, and T1/T2 are
the cases that can refute it.

**Leases: none required.** Every case here is a `docker compose config` render,
a file read, or a scratch-directory experiment. Nothing starts, stops, builds,
recreates or touches a running container, and no case attaches anything to an
`ai-stack_*` network. If a case seems to need a running plane, it has been
misread — say so rather than taking a lease.

**Read the branch, not the working tree.** `core.autocrlf` is on and the
operator's checkout may hold unrelated edits. Where a case says "read the file",
either work inside a worktree/clone of `work/sl-compose-anchors` or use
`git show work/sl-compose-anchors:<path>`.

---

## Setup — the scratch harness every render case uses

Do this once. `$SC` is any scratch directory of yours, on a SHORT path
(`C:\tmp\sl` and not somewhere under the worktree — the clones below are deep).

**Two things in this section were learned the hard way; both are fixes, not
preferences.**

*First,* `docker compose config` RESOLVES `build.context` and every bind-mount
`source` to an ABSOLUTE path, so two trees at different roots differ in every one
of those strings before a compose file is read. Attempt 1 of this plan compared
raw JSON across two roots and could not pass. The normalizer rewrites the tree
root — and its PARENT, because `memory/docker-compose.yml`'s build context is
`../../mnemory`, a sibling of the repo — to fixed tokens. T1c proves that
rewrite hides nothing.

*Second,* since **sl-env-split** (merged as `f291cb3`, the base of this branch)
**nothing passes `--env-file`**: every plane loads `<plane>/.env` NATIVELY from
its project directory, and `COMPOSE_PROFILES` is per-plane too. So each tree
needs its plane `.env` files seeded, both trees from the SAME source
(`<plane>/.env.example`, which is tracked and carries no secret), and the
renderer passes `--profile` flags with `COMPOSE_PROFILES=` cleared in the
environment so "no profile" is expressible.

```sh
# 1. normalizer: sorted-key JSON; `nox` drops top-level x- keys; TOKEN=PATH
#    rewrites PATH to <TOKEN> inside any string. Longest PATH wins, so a tree
#    root is replaced before its parent.
cat > "$SC/normalize.py" <<'EOF'
import sys, json, re
mode = (sys.argv[1:2] or ["full"])[0]
subs = []
for a in sys.argv[2:]:
    if "=" not in a:
        continue
    tok, path = a.split("=", 1)
    path = path.rstrip("/\\")
    if not path:
        continue
    forms = set()
    for v in (path.replace("\\", "/"), path.replace("/", "\\")):
        forms.add(v)
        if len(v) > 1 and v[1] == ":":            # a drive letter renders in either case
            forms.add(v[0].upper() + v[1:])
            forms.add(v[0].lower() + v[1:])
    for v in forms:
        subs.append((len(v), re.compile(re.escape(v)), "<%s>" % tok))
subs.sort(key=lambda s: -s[0])
def walk(o):
    if isinstance(o, str):
        for _, pat, tok in subs:
            o = pat.sub(tok, o)
        return o
    if isinstance(o, list):
        return [walk(x) for x in o]
    if isinstance(o, dict):
        return dict((walk(k), walk(v)) for k, v in o.items())
    return o
d = json.load(sys.stdin)
if mode == "nox":
    for k in [k for k in d if k.startswith("x-")]:
        del d[k]
print(json.dumps(walk(d), sort_keys=True, indent=1))
EOF

# 2. renderer: writes <outdir>/<name>.json for all 15 plane x profile combos.
#    NO --env-file: each plane loads its own .env. COMPOSE_PROFILES is cleared
#    in the environment so an empty profile list means exactly that.
cat > "$SC/render.sh" <<'EOF'
#!/bin/sh
# render.sh <tree> <outdir> <mode> <scratch>
set -e
ROOT="$1"; OUT="$2"; MODE="${3:-full}"; SC="$4"
mkdir -p "$OUT"; cd "$ROOT"
ABS=$(pwd -W 2>/dev/null || pwd); PAR=$(dirname "$ABS")
render() {
  nm="$1"; f="$2"; shift 2
  args=""; for p in "$@"; do [ -n "$p" ] && args="$args --profile $p"; done
  if COMPOSE_PROFILES= docker compose -f "$f" $args config --format json > "$OUT/$nm.raw" 2>"$OUT/$nm.err"; then
    python "$SC/normalize.py" "$MODE" "ROOT=$ABS" "ROOT=$ROOT" "SIBLING=$PAR" < "$OUT/$nm.raw" > "$OUT/$nm.json"
    rm -f "$OUT/$nm.raw"; echo "OK   $nm"
  else
    echo "FAIL $nm : $(grep -v 'level=warning' "$OUT/$nm.err" | head -c 220)"
  fi
}
render frontend-bare     frontend/docker-compose.yml ""
render frontend-stock    frontend/docker-compose.yml stock
render frontend-gpu      frontend/docker-compose.yml gpu
render frontend-gpu-ts   frontend/docker-compose.yml gpu tailscale
render inference-bare    inference/docker-compose.yml ""
render inference-local   inference/docker-compose.yml local
render memory-bare       memory/docker-compose.yml ""
render search-bare       search/docker-compose.yml ""
render coder-bare        coder/docker-compose.yml ""
render portal-bare       portal/docker-compose.yml ""
render portal-internet   portal/docker-compose.yml internet
render agentorg-bare     agent-org/docker/docker-compose.yml ""
render agentorg-workers  agent-org/docker/docker-compose.yml workers
render agentorg-cloud    agent-org/docker/docker-compose.yml cloud
render agentorg-wc       agent-org/docker/docker-compose.yml workers cloud
EOF
```

**Two clones, each seeded from the tracked `.env.example` files.** Clone rather
than `git worktree add`: a worktree mutates the shared repo's worktree list, and
the operator's checkout is not yours to add to.

```sh
WT=<a checkout of work/sl-compose-anchors>        # only the clone SOURCE
for pair in base:f291cb3 head:work/sl-compose-anchors; do
  d=${pair%%:*}; r=${pair##*:}
  git -c core.longpaths=true clone -q "$WT" "$SC/$d"
  git -C "$SC/$d" checkout -q "$r"
  for e in .env frontend/.env inference/.env memory/.env search/.env \
           coder/.env portal/.env agent-org/docker/.env; do
    cp "$SC/$d/${e%.env}.env.example" "$SC/$d/$e"
  done
done
sh "$SC/render.sh" "$SC/base" "$SC/before" nox "$SC"
sh "$SC/render.sh" "$SC/head" "$SC/after"  nox "$SC"
```

**Why the seeding is not optional.** `.env` and every `<plane>/.env` are
gitignored, so a fresh clone has none. `agent-org/docker/docker-compose.yml`
also carries `env_file: ../../.env` on two services, which compose resolves from
disk no matter what else is on the command line — without the root `.env` all
four agent-org combinations die with `env file <root>\.env not found`
(measured). And both trees must be seeded from the SAME source, because `config`
INLINES an `env_file`'s contents into the service environment: seeding one tree
from `.env.example` and the other from a real `.env` would make agent-org differ
on values this item never touched.

Every `render.sh` line must print `OK`, in both trees, **30 lines total**. A
`FAIL` is a case failure in itself, whichever tree it came from.
---

## T1 — every plane × profile renders byte-identically (the anchor's criterion 1)

**Run**

```sh
for f in "$SC"/before/*.json; do
  n=$(basename "$f")
  if cmp -s "$f" "$SC/after/$n"; then echo "IDENTICAL $n"; else echo "DIFFER $n"; diff "$f" "$SC/after/$n"; fi
done
```

**Passes** when all **15** names print `IDENTICAL`:
`frontend-bare`, `frontend-stock`, `frontend-gpu`, `frontend-gpu-ts`,
`inference-bare`, `inference-local`, `memory-bare`, `search-bare`, `coder-bare`,
`portal-bare`, `portal-internet`, `agentorg-bare`, `agentorg-workers`,
`agentorg-cloud`, `agentorg-wc`. Record the count, not just "all passed".

**Fails** on any `DIFFER`, on fewer than 15 files in either directory, or on any
`FAIL` line during rendering.

**Declared deviation from the criterion's wording — read this before judging.**
The anchor says the render is "identical before and after". It is, for
`services`, `networks` and `volumes` — but for thirteen of the fifteen combinations (all but the two inference ones)
the RAW render is not, because `docker compose config` echoes top-level `x-` keys
back out, and this change adds them. The `nox` mode above drops exactly those
top-level `x-*` keys and nothing else. T1b proves the stripping hides no service
change; if you would rather fail the item on the criterion's literal wording than
accept the normalization, say so and let the gate decide — do not quietly rewrite
the criterion.

### T1c — prove the TREE-ROOT rewrite hides nothing

The rewrite in the Setup is the reason T1 can pass at all, so it has to be shown
not to be hiding the very differences T1 exists to find. Two negative controls,
both rendered against the same `$SC/before`, so the only variable is the planted
defect. Work in a THIRD clone, a copy of `$SC/head`:

```sh
cp -r "$SC/head" "$SC/bug"; cd "$SC/bug"
python - <<'PYEOF'
import io
# a PATH change BEYOND the tree root: one bind source, retargeted
p = "frontend/docker-compose.yml"
t = io.open(p, encoding="utf-8", newline="").read()
old = "- ../status-pipe:/host_project/status-pipe:ro"
new = "- ../system-prompts:/host_project/status-pipe:ro"
assert t.count(old) == 1
io.open(p, "w", encoding="utf-8", newline="").write(t.replace(old, new, 1))
PYEOF
sh "$SC/render.sh" "$SC/bug" "$SC/bugout" nox "$SC"
cmp -s "$SC/before/frontend-gpu.json" "$SC/bugout/frontend-gpu.json" \
  && echo "NOT CAUGHT" \
  || diff "$SC/before/frontend-gpu.json" "$SC/bugout/frontend-gpu.json"
```

**Control 1 passes** when the diff shows exactly one changed line, the `source`
of that bind, reading `<ROOT>` + `\status-pipe` before and `<ROOT>` +
`\system-prompts` after. The rewrite replaces the root PREFIX only; every
character under it still compares, so a bind source, a build context or a mount
target that really moved is still caught.

**Control 2** — the tokens must not collapse distinct paths into one:

```sh
grep -o '<ROOT>[^"]*' "$SC/after/memory-bare.json" | sort -u
grep -o '<SIBLING>[^"]*' "$SC/after/memory-bare.json" | sort -u
```

**Passes** when the first command prints THREE different suffixes — the
`backup/mnemory-backup.sh` script mount, the `backups/mnemory` output directory
and the `memory/mnemory-gateway` build context — and the second prints
`<SIBLING>` + `\mnemory`, the build context that lives OUTSIDE the repo
(`memory/docker-compose.yml` builds `../../mnemory`, a sibling of the checkout;
that is why the parent directory needs its own token and cannot simply be folded
into `<ROOT>`).

**Fails** if the planted bind-source change is NOT caught — the rewrite would
then be too greedy, and every render case in this plan is void — or if either
command prints a bare token with no suffix where a real path was, or if
`<ROOT>` and `<SIBLING>` are not distinguishable in the output.

### T1b — prove the normalization hides nothing

**Run** the same renders in `full` mode (`sh "$SC/render.sh" ... full`) into
`before_full`/`after_full`, then:

```sh
python - <<'EOF'
import json, glob, os
SC = os.environ["SC"]
for f in sorted(glob.glob(SC + "/before_full/*.json")):
    n = os.path.basename(f)
    b = json.load(open(f)); a = json.load(open(SC + "/after_full/" + n))
    print("%-22s added=%s removed=%s changed=%s" % (
        n, sorted(set(a) - set(b)), sorted(set(b) - set(a)),
        sorted(k for k in set(a) & set(b) if a[k] != b[k])))
EOF
```

**Passes** when every line shows `removed=[]` and `changed=[]`, and `added` is
either `[]` (both inference combinations — see T6) or a list containing ONLY
names beginning `x-`.

**Fails** if any combination reports a removed key, a changed common key
(`services` is a common key — a change there means a service moved), or an added
key that is not an `x-` extension field.

---

## T2 — no service's resolved hardening changed (the anchor's criterion 2, the trap)

This is the case the item exists for: a merge key merges maps, but a LIST on a
service REPLACES the anchored list instead of appending to it, so a service that
carried `security_opt` plus something extra could silently lose an element.

**Run**

```sh
python - <<'EOF'
import json, glob, os
SC = os.environ["SC"]
KEYS = ["security_opt","cap_drop","cap_add","read_only","tmpfs","healthcheck",
        "logging","privileged","image","entrypoint","restart","user"]
cells = bad = 0
for f in sorted(glob.glob(SC + "/before/*.json")):
    n = os.path.basename(f)
    b = json.load(open(f)).get("services", {}); a = json.load(open(SC+"/after/"+n)).get("services", {})
    if sorted(b) != sorted(a):
        print("SERVICE SET DIFFERS in %s" % n); bad += 1; continue
    for svc in sorted(b):
        for k in KEYS:
            vb, va = b[svc].get(k, "<absent>"), a[svc].get(k, "<absent>")
            cells += 1
            if vb != va:
                print("MISMATCH %s/%s.%s: %r -> %r" % (n, svc, k, vb, va)); bad += 1
print("compared %d cells; mismatches: %d" % (cells, bad))
EOF
```

**Passes** at `mismatches: 0` with a cell count of **1176**. A materially smaller
count means the renders did not both load — check it, do not accept it.

**Fails** on any `MISMATCH` or `SERVICE SET DIFFERS` line. In particular these
five services carry hardening BEYOND the anchor and are where a replacement bug
would land, so name their rows in your evidence:

| service | must still resolve to |
|---|---|
| `tailscale-backup` (frontend) | `security_opt [no-new-privileges:true]` **and** `cap_drop [ALL]` |
| `lm-models-backup` (inference) | `security_opt [...]` **and** `cap_drop [ALL]` |
| `searxng` (search) | `cap_drop [ALL]` **and** `cap_add [CHOWN, SETGID, SETUID, DAC_OVERRIDE]` |
| `caddy` (portal) | `cap_drop [ALL]`, `cap_add [NET_BIND_SERVICE]`, `read_only true`, `tmpfs 64m` |
| `portal-init` (portal) | `cap_drop [ALL]`, `cap_add [CHOWN, FOWNER, DAC_OVERRIDE]`, **no** `read_only` |

**And the two the render cannot see.** Compose drops `read_only: false` (its
default) from `config` output, so `openwebui` and `openwebui-stock` show no
`read_only` key in either tree. Check those two against the SOURCE instead:
`git show development:frontend/docker-compose.yml` and the branch version must
both give each service `read_only: false` — on the branch it arrives through
`<<: *hardening-owui`. A tester who only reads the render cannot distinguish
"false" from "deleted"; that is the gap this paragraph closes.

**And the service that must NOT have gained anything:** `tailscale` (the netns
companion) has no `security_opt` on `development` and must still have none —
an empty/absent `security_opt` in `frontend-gpu-ts`. A merge key applied there
would be a security change, which is explicitly out of scope.

---

## T3 — the declaration count per file drops to one (the anchor's criterion 3)

**Run** in a checkout of the branch:

```sh
for f in frontend/docker-compose.yml inference/compose/upstreams.yml \
         inference/compose/queue.yml inference/compose/gateway.yml \
         inference/compose/backups.yml memory/docker-compose.yml \
         search/docker-compose.yml coder/docker-compose.yml \
         portal/docker-compose.yml agent-org/docker/docker-compose.yml; do
  printf "%-42s %s\n" "$f" "$(grep -c '^ *- no-new-privileges' "$f")"
done
```

**Passes** when every file prints exactly `1`. Use this anchored regex, not a
bare `grep -c no-new-privileges`: several headers now MENTION the flag in prose,
and a comment is not a declaration. For reference, the BARE-string counts on the
branch (measured, not estimated) are:

| file | bare `grep -c` | declarations |
|---|---|---|
| frontend, memory, search, coder, agent-org | 2 | 1 |
| the four `inference/compose/*.yml` | 1 | 1 |
| **portal** | **4** | 1 |

Portal is 4, not 2: besides its header's one mention there are two OLDER
comments on `development` that name the flag in passing — on `caddy-backup`
("cap_drop ALL + read_only + no-new-privileges + non-root UID") and on
`authelia-backup` ("Hardening still applies (cap_drop ALL, read_only,
no-new-privileges, non-root UID)"). Neither is this branch's, and neither is a
declaration. The first version of this plan said "2 for … portal" from memory
instead of from the file; that is the mistake the anchored regex exists to make
irrelevant, and the row above is now what was actually counted.

**Fails** if any file prints `0` (the hardening was lost, not anchored) or `>1`
(a copy survived).

**Declared conflict with the criterion's numbers.** The criterion states the
BEFORE counts as "frontend 3, inference 8, memory 3, search 4, coder 4, portal
12". Two are wrong on `development`: frontend is **4** (the `stock` profile added
a fourth service carrying the block) and `agent-org` — named in the anchor's
`artifact` but absent from the count list — is **16**. Verify the before-counts
yourself with the same command in `$SC/base`; the expected values are
4 / 2,1,3,2 / 3 / 4 / 4 / 12 / 16 = **51 declarations across ten files, now 10**.
(Commit `d50fdec`'s message says "forty-eight". That number is WRONG - it is the
sum with agent-org's 16 replaced by the 13 watchtower labels, a transcription
slip found while writing the report. The per-file numbers in that commit and in
findings §B are right; only the total was not. Count it yourself.)
Findings §B records the discrepancy. Judge the criterion's intent (one
declaration per file), and flag the wording.

---

## T4 — `check-project-configs.ps1` passes with the WHOLE delta staged

The pre-commit gate is **staged-aware**: with nothing staged it prints
`nothing staged - skip` and exits 0, and with no `*.yml` staged it skips the
compose half entirely. A run that skips proves nothing. Force the whole-branch
delta into the index in a scratch clone of your own — never in the operator's
checkout, and never in the developer's worktree.

`$SC/head` from the Setup is already that clone, already seeded. Reuse it.

**Run**

```sh
cd "$SC/head"
git reset --soft f291cb3                 # the base sha; a -b clone has no `development` ref
git add -- . ':!OB1'
git diff --cached --name-only            # expect the 11 compose files + stack.manifest.toml
                                         #   + .env.example + CLEANUP-PLAN.md + the 2 new docs
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-project-configs.ps1
echo "exit=$?"
```

**Passes** on `exit=0` **and** an output line reading
`[configs] all 9 compose projects render clean` **and**
`[OK] scripts/lib/stack-services.json matches the manifest, the sidecar and the
compose renders`. Paste the whole output.

**Fails** on a non-zero exit, on any `COMPOSE INVALID` line, on a
`nothing staged - skip` line (you staged nothing — the case did not run), or on a
`docker compose unavailable` line (that is a plan inadequacy on your host, not a
pass: report `-PlanInadequate`).

### Three things a CLONE does that have nothing to do with this item

All three were measured on 2026-09-19; know them before you read a red as a
finding.

1. **No `.env` ⇒ a spurious inventory FAIL.** `.env` is gitignored, so a fresh
   clone has none, and the inventory verifier then generates a DIFFERENT
   `projects.ai-stack` row: eight `[ -- ] NOT VERIFIED - <plane>: .env is absent`
   lines, then
   `[FAIL] projects.ai-stack: committed {"file": null, "env_file": null, ...} !=
   generated {"file": "docker-compose.yml", "env_file": ".env", ...}`, then
   `INVENTORY DRIFT`. The generator marks the anchor unstartable only when it can
   see an env file. The Setup's seeding loop is what prevents this.
2. **Empty `OB1/` ⇒ `open-brain` is NOT VERIFIED, not failed.** A clone does not
   populate the submodule, so you will see
   `[configs] NOT VERIFIED: project 'open-brain' has 30 inventory row(s) and no
   render target` and `[ -- ] NOT VERIFIED - open-brain: OB1/docker/docker-compose.yml
   is not on disk`. Expected; `[OK]` is still reachable, because the verifier
   asserts over the planes it COULD render.
3. **The FULL pre-commit hook goes red in a clone, on a different check.** If you
   run `.githooks/pre-commit` rather than this one script,
   `check-corpus-exposure-producers.ps1` reports
   `FAIL - the scan examined 423 file(s) and found ZERO corpus insert sites ...
   the prune list, the allow-list or the scan root has excluded everything`.
   All thirteen recognised producers live in `OB1/`, which is empty in a clone;
   in a populated tree the same check scans 773 files and reports the 13 sites.
   It is a clone artifact and it is **not** this item's doing — this branch stages
   no `.ts`/`.py`/`.js` file at all. The authoritative pre-commit run is the one
   the developer's commits already passed in a populated worktree.

If you want the whole hook green, populate the submodule
(`git submodule update --init` in the clone) before running it; otherwise run
this one check, which is what the anchor's acceptance criterion names.

---

## T5 — every citation into a changed file resolves to the construct it names

All eleven compose files changed length, so every `path:line` pointing into them
moved. Check **both spellings**: the path form (`frontend/docker-compose.yml:408`)
and the bare continuation form a paragraph uses after naming the file once
(`… and :313 (agent-bridge joins …)`).

**The counting unit, because an earlier revision of this plan gave a number with
no unit.** It said "97 citation points", which matches nothing a tester can
reproduce — it was the raw hit count of a scratch scanner, its own false
positives included (bare ports like `:8445`, continuations belonging to
`frontend/entrypoint.sh` rather than to the compose file named earlier on the
line, and `OB1/` paths this item does not touch). Retract it. Measured against
this branch, after the rebase onto `f291cb3`:

| unit | count |
|---|---|
| files carrying citations into the eleven changed compose files | **8** |
| LINES in them naming at least one such line number | **40** |
| individual line NUMBERS those lines name | **75** |
| of those, numbers this branch changed relative to `f291cb3` | **71** |

The eight files are `stack.manifest.toml` (29 lines / 55 numbers),
`CLEANUP-PLAN.md` (1/2), `coder/.env.example` (2/2), `frontend/.env.example`
(1/2), `inference/.env.example` (4/9), `memory/.env.example` (1/1),
`search/.env.example` (1/2) and `documentation/runbooks/env-split-migration.md`
(1/2). Five of those are NEW to this sweep: sl-env-split moved the root
`.env.example`'s citations out into the per-plane files, so a sweep written
against the old base would have missed them entirely.

The four numbers that did not change are all in `stack.manifest.toml`:
`upstreams.yml:24`, which sits above the inserted `x-` block, and three
agent-org numbers that were briefly WRONG at those values — findings §C4 says
how, and it is the reason this case is worth executing rather than skimming.
Nothing else in this repo cites into the eleven files: `documentation/archive/**`
cites the 2,249-line pre-split ROOT compose, a different file, and earlier items'
evidence records are deliberately out of scope (end of this case).

**Run** — list every citation, then resolve each one:

```sh
cd "$SC/head"
MSYS_NO_PATHCONV=1 grep -nE \
  "(docker-compose|upstreams|queue|gateway|backups)[.]yml:[0-9]+" \
  stack.manifest.toml CLEANUP-PLAN.md documentation/runbooks/env-split-migration.md \
  coder/.env.example frontend/.env.example inference/.env.example \
  memory/.env.example search/.env.example
```

then, for each hit, `sed -n '<N>p' <target>` and compare with the sentence —
including the bare `:NNN` and `, NNN` continuations, which the grep pattern above
does NOT show. That is the point of the table below: it names them.

**Passes** when each of these reads as its sentence claims.

| file:line | cites | must be |
|---|---|---|
| `stack.manifest.toml:118` | `inference/docker-compose.yml:94-96, 99-101` | `llm-net:` … `name: ai-stack_llm-net`; `app-net:` … `name: ai-stack_app-net` |
| `:130` | `upstreams.yml:122-128 and :173-179` | both `deploy:` … `capabilities: [gpu]` blocks |
| `:131` | `upstreams.yml:24, :76` | the MODEL STORE comment; the `/models:ro` bind |
| `:132` | `upstreams.yml:166` | `- LLAMA_ARG_MODEL=/models/bge-m3-f16.gguf` |
| `:143` | `upstreams.yml:63,:151, queue.yml:64, backups.yml:71` | four `profiles: [local]` lines |
| `:163`, `:166` | `frontend:502-510`, `(:513)` | `default:` … `name: ai-stack_app-net`; `owui-net:` |
| `:176`, `:177`, `:179` | `frontend:344-369`, `:344,347`, `:346` | the tailscale env run; the two `LLAMA_CPP_*_HOST` lines; `LLAMA_CPP_ENABLED` |
| `:182`, `:184`, `:185`, `:186` | `frontend:286`, `:350`, `:352`, `:351,:353-354` | `SEARXNG_QUERY_URL`; `OPEN_NOTEBOOK_HOST`; `_ENABLED`; `_PORT` / `_TS_PORT` / `_API_PORT` |
| `:191`, `:192`, `:193` | `frontend:366`, `:368`, `:356-365` | `QUARTZ_HOST`; `QUARTZ_ENABLED`; the Quartz routing comment |
| `:203` | `frontend:324-335` | the tailscale `# NO env_file HERE` comment … `TAILSCALE_AUTH_KEY` |
| `:207`, `:232`, `:236` | `frontend:198-205`/`297-303`, `:162-193`, `:198-205, 297-303` | `build:`…`image: openwebui:local`; `deploy:`…`capabilities: [ gpu ]`; the whole `openwebui-stock` block |
| `:208`, `:241`, `:250` | `frontend:323` ×3 | `network_mode: service:openwebui` |
| `:255`, `:256`, `:257` | `memory:153-155`, `:54`, `:56` | `llm-net:`…`name:`; `LLM_BASE_URL`; `EMBED_BASE_URL` |
| `:273`, `:280` | `search:187-189`, `:43-46` | `default:`…`name: ai-stack_default`; `cap_add:`…`/dev/net/tun` |
| `:292`, `:293`, `:294` | `coder:226-228`, `:46 and :93`, `:65-66` | the llm-net seam; two `- llm-net` joins; `NO_PROXY`/`no_proxy` |
| `:440`–`:456` | `agent-org:764-766, 183, 313, 420-421, 499-500, 345, 437, 399, 480, 198, 208, 197, 207` | see the sentences; each names its construct |
| `:487`, `:492` | `portal:618-620`, `:616-617` | `app-net:`/`external: true`/`name: ai-stack_app-net`; the two-line `# The ai-stack seam` comment |
| `:500`, `:501` | `gateway.yml:173`, `:182-184` | `llm-gateway-ui:`; its `networks:` / `- llm-net` / `- app-net` |
| `CLEANUP-PLAN.md:755` | `agent-org:520,590` | the two egress `build:` keys (`ao-git-egress`, `ao-egress`) |
| `coder/.env.example:32`, `:64` | `frontend:281`, `coder:197` | the commented-out `TERMINAL_SERVER_CONNECTIONS`; `LITTLE_CODER_BACKUP_INTERVAL` |
| `frontend/.env.example:144` | `frontend:408 and :472` | the openwebui-backup and tailscale-backup `BACKUP_INTERVAL` lines |
| `inference/.env.example:35`, `:104` | `gateway.yml:115`, `queue.yml:75-76` | the `COMPOSE_PROFILES` passthrough; the admission-sizing comment pair |
| `inference/.env.example:156`, `:163` | `backups.yml:37,55`, `:64, 98, 105-107` | `llm-gateway-backup:` and its entrypoint; the disable comment, `LM_MODELS_BACKUP_INTERVAL`, the `if [ -z … ]` guard |
| `memory/.env.example:47` | `memory:127` | `MNEMORY_BACKUP_INTERVAL` |
| `search/.env.example:65` | `search:154-161` | the `gateway` service's `environment:` … `REDIS_URL` |
| `env-split-migration.md:190` | `agent-org:351` and `:443` | `ao-worker-1`'s and `ao-worker-2`'s `env_file:` |

**Fails** if any citation lands on an unrelated construct or past the end of its
file. Three ambiguity traps, each with a wrong answer that still looks right:
`agent-org:520,590` are the SECOND and THIRD `build:` in that file (the first,
`:153`, is `agent-bridge`); `search:154` is the THIRD `environment:` (vpn and
searxng come first); `agent-org:351,443` are the SECOND and THIRD `env_file:`
(`:165` is `agent-bridge`). Resolve by owning service, not by first match.

**Two of these were ALREADY broken at 4934529** and were repaired by construct
rather than by offset — findings §C. `stack.manifest.toml`'s two portal
citations were off by exactly four lines (they named `notify-net`, not
`app-net`); sl-env-split found the same defect independently and landed first,
so the repair you see is theirs, shifted by this item's −14 lines in that file.
`CLEANUP-PLAN.md:755` was off by 41 — it named a `profiles:` line and an env
var, not the two egress builds — and that repair is this item's. Verify BOTH the
old breakage (`git show 4934529:<file>`) and the new correctness; a repair you
cannot show was needed is not verified.

**Out of scope, deliberately:** dated evidence records from earlier items
(`documentation/notes/*-findings.md`, `documentation/evidence/*/test-plan.md`)
were NOT rewritten. They state what was true on a date. The precedent is
`a2e4e3e`, whose sweep touched `stack.manifest.toml`, `scripts/stack/README.md`
and its own evidence, and no other item's. If you disagree, raise it — do not
treat it as an undeclared miss.

---

## T6 — the include-semantics claims in the headers are true

The four `inference/compose/*.yml` headers and the spine's third `include:`
bullet make two factual claims about compose. Both are testable in a scratch
directory in under a minute, and the FIRST draft of these headers got the second
one wrong — so do not take them on trust.

**Run**

```sh
mkdir -p "$SC/inc/sub" && cd "$SC/inc"
printf 'name: t1\nx-h: &h\n  security_opt:\n    - no-new-privileges:true\ninclude:\n  - sub/g.yml\nservices:\n  s:\n    <<: *h\n    image: busybox\n' > spine.yml
printf 'services:\n  c:\n    <<: *h\n    image: busybox\n' > sub/g.yml
docker compose -f spine.yml config ; echo "exit=$?"          # claim 1

mkdir -p "$SC/inc/sub2"; cd "$SC/inc"
printf 'name: t2\ninclude:\n  - sub2/a.yml\n  - sub2/b.yml\n' > spine2.yml
printf 'x-h: &h\n  security_opt:\n    - no-new-privileges:true\nservices:\n  a:\n    <<: *h\n    image: busybox\n' > sub2/a.yml
printf 'x-h: &h\n  security_opt:\n    - seccomp=unconfined\n  cap_drop:\n    - ALL\nservices:\n  b:\n    <<: *h\n    image: busybox\n' > sub2/b.yml
docker compose -f spine2.yml config ; echo "exit=$?"          # claim 2
```

**Passes** when:
1. the first render **fails** with `unknown anchor 'h' referenced` — an anchor in the spine is not visible in an included file, which is why each group file declares its own;
2. the second render **succeeds** (`exit=0`), service `a` resolves `no-new-privileges:true` and `b` resolves `seccomp=unconfined` + `cap_drop [ALL]`, and **no `x-h` key appears in the output** — an included file's top-level `x-` key never reaches the merged model, so differing declarations do NOT conflict; they diverge silently.

**Fails** if either behaves otherwise — the headers would then be stating
something false, which is worse than stating nothing.

**Cross-check against the real plane:** claim 2 predicts that `inference` is the
one project whose RAW render is unchanged (its `x-` blocks are all in included
files). T1b's `added=[]` for `inference-bare` and `inference-local`, against
`added=[x-…]` for every other combination, is that prediction landing. If T1b
showed `x-` keys added to inference, claim 2 is wrong.

---

## T7 — a checker that cannot go red is not a check

Plant the exact bug this item is about and prove T1/T2 catch it.

**Run**, in a scratch COPY of the branch (never the worktree, never a running
plane):

```sh
cp -r "$WT" "$SC/bug" && cd "$SC/bug"
# make the anchor's list shorter than what a service needs, the way a careless
# edit would: drop ALL from portal's x-hardening, so eleven services silently
# lose cap_drop through the merge key.
python - <<'EOF'
import io
p = "portal/docker-compose.yml"
t = io.open(p, encoding="utf-8", newline="").read()
t = t.replace("x-hardening: &hardening\n  security_opt:\n    - no-new-privileges:true\n  cap_drop:\n    - ALL\n",
              "x-hardening: &hardening\n  security_opt:\n    - no-new-privileges:true\n", 1)
io.open(p, "w", encoding="utf-8", newline="").write(t)
EOF
sh "$SC/render.sh" "$SC/bug" "$SC/bugout" nox "$SC"
cmp -s "$SC/before/portal-internet.json" "$SC/bugout/portal-internet.json" \
  && echo "NOT CAUGHT" || echo "CAUGHT by T1"
```

then run T2's comparison with `$SC/bugout` in place of `$SC/after`.

**Passes** when T1 prints `CAUGHT by T1` **and** T2 reports mismatches naming
`cap_drop` on the twelve portal services (`"ALL"` → `<absent>`).

**Fails** if either reports no difference — that would mean the render comparison
is blind to a dropped list element, and every other case in this plan is
worthless. Delete `$SC/bug` afterwards.

**A second planting worth 60 seconds** (optional but recommended): in another
scratch copy, give `tailscale-backup` its own `security_opt: [no-new-privileges:true]`
line in ADDITION to `<<: *backup-sidecar` and confirm T1 stays IDENTICAL — an
override that restates the anchored value is a no-op, which is why the
list-replacement hazard is silent and why the headers spell it out.

---

## T8 — repo gates on the branch

**Run** in a checkout of `work/sl-compose-anchors` (`$SC/head` will do):

```sh
ruff check .
python -m pytest scripts/stack -q
python scripts/stack/stack.py inventory --check
```

**Passes** on `All checks passed!`, `107 passed`, and an `inventory --check`
whose LAST line is
`[OK] scripts/lib/stack-services.json matches the manifest, the sidecar and the
compose renders`.

**The informational lines above that `[OK]` differ between a clone and a
populated worktree, and the first version of this plan named only the worktree's
shape.** Both are correct; score the `[OK]`, not the preamble.

* **In a CLONE (empty `OB1/`)** — measured: exactly two lines precede it,
  `[ -- ] NOT VERIFIED - open-brain: OB1/docker/docker-compose.yml is not on disk`
  and `[ ~~ ] mutually exclusive - openwebui: produced by 2 mutually exclusive
  services`. There are NO "declared, not rendered" PROFILE lines, because those
  are produced by comparing the manifest against the pinned OB1 compose, which is
  not there to compare against.
* **In a POPULATED worktree** — the same two lines plus eleven `[ ~~ ] declared,
  not rendered` lines for the `notebook` / `research` / `wiki` profiles and the
  eight OB1 services that carry them, and a closing `[ ~~ ] ^ those resolve
  themselves when the submodule gitlink bumps`. Pre-existing, expected, and not
  this item's: the gitlink has not moved on this branch (`git diff development
  -- OB1` is empty).

**Fails** on a ruff finding, any test failure, or an `inventory --check` that
reports `INVENTORY DRIFT` or a `[FAIL]` row. The inventory reads container rows
out of the compose renders, so a drift report here would mean T1 missed
something. A `[FAIL] projects.ai-stack` row specifically means you forgot the
`cp .env.example .env` from the Setup — see T4's clone-artifact list, and fix the
environment rather than recording a failure.

---

## T9 — nothing true was silently removed

**Run**

```sh
git -C "$WT" diff development...work/sl-compose-anchors -- \
  frontend inference memory search coder portal agent-org | grep '^-' | grep -v '^---'
```

**Passes** when every removed line is one of: a `security_opt:` / `- no-new-privileges:true`
pair, a `cap_drop:` / `- ALL` pair inside a sidecar now covered by an anchor, a
`read_only:` / `tmpfs:` pair on the two openwebui services, an `image:` /
`entrypoint:` / `restart:` line on one of the four sidecars now covered by
`x-backup-sidecar` / `x-pg-backup-sidecar`, or one of the healthcheck timing
lines now covered by an `x-healthcheck-*` anchor — and every one of those is
re-established by the anchor it moved into (T2 is the proof).

**Fails** if any removed line is a comment carrying information that no longer
appears anywhere, or a setting no anchor supplies. Two comments were moved rather
than deleted and should be found in their new home:
`# OpenWebUI needs write access for temp files` (now on `x-hardening-owui`) and
`# plan §2 hardening floor — read-only root FS` (now on `x-hardening-ro`; on
`development` it sat on three of the eleven `read_only: true` services -
`portal-alerter`, `authelia`, `caddy` - and the other eight carried the bare
key, so the anchor states once what was documented inconsistently). Two comments were ADDED at the sidecars
whose `entrypoint: /bin/sh` moved into an anchor, so the bare `command: -c` is
not left looking orphaned — check they say what the anchor actually supplies.

---

## T10 — the findings note is held to the artifact's standard

Findings: `documentation/notes/stack-layers-sl-compose-anchors-findings.md`.

**Run/verify** every claim in it against the file it names, on the branch:

* §A1/§A2 — the two experiments are T6. If T6 passes, §A is verified.
* §B — the before-counts; T3's second command.
* §C1/§C2 — read `git show 4934529:portal/docker-compose.yml` lines 601-609 and `git show 4934529:agent-org/docker/docker-compose.yml` lines 440, 481, 516, 557. Both defects are described against 4934529, the base this item was WRITTEN on; sl-env-split merged first and repaired the portal pair independently (§C1 says so).
* §C4 — the rebase finding: `git show f291cb3:agent-org/docker/docker-compose.yml | wc -l` is 752 and the branch's is 768, so the manifest's agent-org citations HAD to move; check they read 764-766 / 183 / 313 / 420-421,499-500 now.
* §D1 — read `portal/docker-compose.yml`: `portal-init` has no `read_only` and `user: "0:0"`; `portal-cron` has no `user:` key between its own service key and `cloudflared:`. Then read `SECURITY.md:55` and `:163`.
* §D3 — `grep -cE '^ *- "com\.centurylinklabs\.watchtower\.enable=false"'` over the nine plane files must total **47** with the per-file split the note gives.
* §D6 — `grep -rn '^ *logging:' ` over the nine plane files must return nothing.

**Passes** when every claim checks out at the strength it is stated.

**Fails** on any claim that is false, or stated more confidently than its own
provenance label supports. A wrong entry here is worse than a wrong entry in the
compose files, because the next item reads this file and acts on it.

---

## T11 — the headers themselves, which the anchor names as artifact

The anchor's `artifact` ends "…; the compose headers noting the extension
fields." The first version of this plan tested the YAML exhaustively and never
once read the prose, so half the deliverable went unexamined — and that is how a
171-character line containing the literal characters `\n#` in the middle of a
sentence survived into `inference/compose/backups.yml`. Read the headers.

**Run**

```sh
cd "$SC/head"
FILES="frontend/docker-compose.yml inference/docker-compose.yml \
 inference/compose/upstreams.yml inference/compose/queue.yml \
 inference/compose/gateway.yml inference/compose/backups.yml \
 memory/docker-compose.yml search/docker-compose.yml coder/docker-compose.yml \
 portal/docker-compose.yml agent-org/docker/docker-compose.yml"

# a. every anchor a file DECLARES, beside every anchor its header NAMES
for f in $FILES; do
  echo "== $f"
  echo "   declares: $(grep -oE '^x-[a-z-]+' $f | sort | tr '\n' ' ')"
  echo "   header:   $(sed -n '1,/^name:\|^services:\|^x-/p' $f | grep -oE 'x-[a-z-]+' | sort -u | tr '\n' ' ')"
done

# b. no added comment line is over-length or carries an escape that was never
#    expanded. The second command spells the two characters with chr(92) on
#    purpose: a grep pattern for a literal backslash-n is itself easy to mangle
#    when this plan is copied, which is exactly how the defect got in.
git diff f291cb3 -- '*.yml' | grep '^+#' | awk 'length($0) > 101 { print length($0)-1": "$0 }'
git diff f291cb3 -- '*.yml' | python -c "import sys; bs=chr(92); [sys.stdout.write(l) for l in sys.stdin if l[:1]=='+' and bs+'n' in l]"
```

**Passes** when all of:

1. **Declared == named**, per file. The expected sets are frontend
   `x-backup-sidecar x-hardening x-hardening-owui x-healthcheck-http`; portal
   `x-hardening x-hardening-ro x-healthcheck-http`; agent-org `x-backup-sidecar
   x-hardening x-healthcheck-lc x-healthcheck-pg x-pg-backup-sidecar`; memory,
   search, coder and each of the four `inference/compose/*.yml` just
   `x-hardening`.
   **`inference/docker-compose.yml` is the deliberate exception**: it declares
   NONE and its header still NAMES `x-hardening`, because the whole point of
   its third `include:` bullet is to say why the spine cannot host one and the
   group files must each declare their own (T6 claim 1). "Declared == named"
   is the rule for the other ten; for the spine the rule is "declares nothing,
   explains why".
2. **Both `git diff` commands print nothing.** Over-length lines and unexpanded
   escapes are the F1 defect class; the sibling lines in every one of these
   headers wrap between 54 and 88 characters.
3. **Each header states the list-replacement rule** — that a merge key merges
   maps, and that a list written on a service REPLACES the anchored list rather
   than appending. Every one of the ten header blocks must say it; it is the one
   thing a future editor has to know, and the whole reason the item exists.
4. **The factual claims in each header are true of that file.** Spot-check at
   least these four, each of which asserts a countable thing:
   * frontend: "all four services that carry it (tailscale, the netns companion, never did and does not gain it here)". `grep -nE '^    <<: \*' frontend/docker-compose.yml` gives exactly four service-level merges — two `*hardening-owui` and two `*backup-sidecar`, both of which nest `<<: *hardening` — and the `tailscale:` block carries neither a merge key nor a `security_opt`. Do not count with `grep -c '<<: \*hardening'`: that pattern also matches `*hardening-owui`, and it counts the nested merges inside the `x-` blocks as well.
   * portal: "the floor ALL TWELVE services carry, portal-init included" and "x-hardening-ro … the eleven long-running services. portal-init is the one exception" — `grep -c '^    <<: \*hardening$'` is **1** (portal-init) and `grep -c '^    <<: \*hardening-ro$'` is **11**, and `x-hardening-ro` nests `<<: *hardening`, so all twelve resolve the floor.
   * agent-org: "the interval/timeout/retries the three probed services share" for `x-healthcheck-pg`, and "mattermost's probe is deliberately outside it: same interval and timeout, but 5 retries and 60s" — read mattermost's healthcheck and confirm.
   * `inference/compose/queue.yml`: "This file holds ONE service, so the anchor de-duplicates nothing here on its own" — confirm it has exactly one service.

**Fails** on any header that names an anchor it does not declare (or declares one
it does not name), on any over-length or escape-mangled added comment line, on a
header missing the list-replacement rule, or on a header claim that the file
contradicts. A header that is confidently wrong is worse than no header: it is
the thing the next editor will trust instead of reading the YAML.
