# sl-compose-anchors — test plan

Item `sl-compose-anchors`, branch `work/sl-compose-anchors`, developer worktree
`wt-sl-compose-anchors`, base `development` @ 4934529.

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

Do this once. `$WT` is a checkout of `work/sl-compose-anchors`; `$SC` is any
scratch directory of yours.

```sh
# 1. normalizer: sorted-key JSON, optionally with top-level x- keys stripped
cat > "$SC/normalize.py" <<'EOF'
import sys, json
d = json.load(sys.stdin)
if (sys.argv[1:] or ["full"])[0] == "nox":
    for k in [k for k in d if k.startswith("x-")]:
        del d[k]
print(json.dumps(d, sort_keys=True, indent=1))
EOF

# 2. an env file with COMPOSE_PROFILES cleared, for the "no profile" renders.
#    (A --profile flag REPLACES the value, so it cannot express "none".)
sed 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=/' "$WT/.env.example" > "$SC/env.noprofiles"

# 3. renderer: writes <outdir>/<name>.json for all 15 plane x profile combos
cat > "$SC/render.sh" <<'EOF'
#!/bin/sh
set -e
ROOT="$1"; OUT="$2"; MODE="${3:-full}"; SC="$4"
mkdir -p "$OUT"; cd "$ROOT"
render() {
  nm="$1"; f="$2"; shift 2
  if [ -z "$1" ]; then env="$SC/env.noprofiles"; set --; else env=".env.example"; fi
  args=""; for p in "$@"; do args="$args --profile $p"; done
  if docker compose -f "$f" --env-file "$env" $args config --format json > "$OUT/$nm.raw" 2>"$OUT/$nm.err"; then
    python "$SC/normalize.py" "$MODE" < "$OUT/$nm.raw" > "$OUT/$nm.json"; rm -f "$OUT/$nm.raw"; echo "OK   $nm"
  else
    echo "FAIL $nm : $(head -c 300 "$OUT/$nm.err")"
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

Then materialise a BEFORE tree and an AFTER tree:

```sh
git -C "$WT" worktree add "$SC/base" development     # or a second clone at `development`
sed 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=/' "$SC/base/.env.example" > "$SC/env.noprofiles.base"
sh "$SC/render.sh" "$SC/base" "$SC/before" nox "$SC"   # uses $SC/env.noprofiles - see note
sh "$SC/render.sh" "$WT"      "$SC/after"  nox "$SC"
```

*Note:* the two trees' `.env.example` files are identical on this branch except
for five citation line numbers (T5), none of which is a variable. If you prefer,
point both renders at the SAME env file — the criterion is about the compose
files, and using one env file removes a variable from the comparison. Say which
you did.

Every `render.sh` line must print `OK`. A `FAIL` line is a case failure in
itself, whichever tree it came from.

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
and a comment is not a declaration. (Bare-string counts on the branch are 2 for
frontend/memory/search/coder/portal/agent-org and 1 for the four inference group
files — that difference is comments, and T3 is about declarations.)

**Fails** if any file prints `0` (the hardening was lost, not anchored) or `>1`
(a copy survived).

**Declared conflict with the criterion's numbers.** The criterion states the
BEFORE counts as "frontend 3, inference 8, memory 3, search 4, coder 4, portal
12". Two are wrong on `development`: frontend is **4** (the `stock` profile added
a fourth service carrying the block) and `agent-org` — named in the anchor's
`artifact` but absent from the count list — is **16**. Verify the before-counts
yourself with the same command in `$SC/base`; the expected values are
4 / 2,1,3,2 / 3 / 4 / 4 / 12 / 16 = **48 declarations across ten files, now 10**.
Findings §B records the discrepancy. Judge the criterion's intent (one
declaration per file), and flag the wording.

---

## T4 — `check-project-configs.ps1` passes with the WHOLE delta staged

The pre-commit gate is **staged-aware**: with nothing staged it prints
`nothing staged - skip` and exits 0, and with no `*.yml` staged it skips the
compose half entirely. A run that skips proves nothing. Force the whole-branch
delta into the index in a scratch clone of your own — never in the operator's
checkout, and never in the developer's worktree.

**Run**

```sh
git -c core.longpaths=true clone -b work/sl-compose-anchors "$WT" "$SC/cfg"   # short path!
cd "$SC/cfg"
cp "$WT/.env" .env                          # REQUIRED - see the note below
git reset --soft development
git add -- . ':!OB1'
git diff --cached --name-only            # expect the 11 compose files + manifest + .env.example
                                         #   + CLEANUP-PLAN.md + the 2 new docs
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-project-configs.ps1
echo "exit=$?"
```

**Passes** on `exit=0` **and** an output line reading
`[configs] all 8 compose projects render clean` **and** the
`stack-services.json` verifier reporting no drift. Paste the whole output.

**Fails** on a non-zero exit, on any `COMPOSE INVALID` line, on a
`nothing staged - skip` line (you staged nothing — the case did not run), or on a
`docker compose unavailable` line (that is a plan inadequacy on your host, not a
pass: report `-PlanInadequate`).

**The `.env` copy is not optional, and this was measured.** `.env` is gitignored,
so a fresh clone has none — and without it the inventory verifier in the second
half of this check emits a SPURIOUS failure that has nothing to do with this
item: eight `[ -- ] NOT VERIFIED - <plane>: .env is absent` lines, then
`[FAIL] projects.ai-stack: committed {"file": null, "env_file": null, ...} !=
generated {"file": "docker-compose.yml", "env_file": ".env", ...}` and
`INVENTORY DRIFT`. The generator marks the anchor unstartable only when it can
see the env file. Copy `.env` in and the same clone prints
`[OK] scripts/lib/stack-services.json matches ...`. Do not report that drift as
an item failure; if you cannot obtain `.env`, say so and score T4 against the
`all 8 compose projects render clean` line alone, noting the rest was not
verifiable in your environment.

---

## T5 — every citation into a changed file resolves to the construct it names

All eleven compose files changed length, so every `path:line` pointing into them
moved. Check **both spellings**: the path form (`frontend/docker-compose.yml:408`)
and the bare continuation form a paragraph uses after naming the file once
(`… and :313 (agent-bridge joins …)`).

**Run** — this reads each citation and prints the line it now lands on:

```sh
cd "$WT"
MSYS_NO_PATHCONV=1 grep -rnI --exclude-dir=OB1 --exclude-dir=.git -oE \
  "(([A-Za-z0-9_./-]*/)?(docker-compose|upstreams|queue|gateway|backups)\.yml):[0-9]+" \
  stack.manifest.toml .env.example CLEANUP-PLAN.md
```

then, for each hit, `sed -n '<N>p' <target>` and compare with the sentence.

**Passes** when each of the citations below reads as its sentence claims. These
are the ones this item moved; check every one, and check the bare `:NNN`
continuations named in the right-hand column:

| file:line | cites | must be |
|---|---|---|
| `stack.manifest.toml:118` | `inference/docker-compose.yml:94-96, 99-101` | `llm-net:` … `name: ai-stack_llm-net`; `app-net:` … `name: ai-stack_app-net` |
| `:129` | `upstreams.yml:122-128 and :173-179` | both `deploy:` … `capabilities: [gpu]` blocks |
| `:130` | `upstreams.yml:24, :76` | the MODEL STORE comment; the `${LM_MODELS_DIR…}:/models:ro` bind |
| `:131` | `upstreams.yml:166` | `- LLAMA_ARG_MODEL=/models/bge-m3-f16.gguf` |
| `:142` | `upstreams.yml:63,:151, queue.yml:64, backups.yml:69` | four `profiles: [local]` lines |
| `:162`, `:165` | `frontend:502-510`, `(:513)` | `default:` … `name: ai-stack_app-net`; `owui-net:` |
| `:176`, `:177`, `:179` | `frontend:344-369`, `:344,347`, `:346` | the tailscale env run; the two `LLAMA_CPP_*_HOST` lines; `LLAMA_CPP_ENABLED` |
| `:182`, `:184`, `:185`, `:186` | `frontend:286`, `:350`, `:352`, `:351,:353-354` | `SEARXNG_QUERY_URL`; `OPEN_NOTEBOOK_HOST`; `_ENABLED`; `_PORT` / `_TS_PORT` / `_API_PORT` |
| `:191`, `:192`, `:193` | `frontend:366`, `:368`, `:356-365` | `QUARTZ_HOST`; `QUARTZ_ENABLED`; the Quartz routing comment |
| `:203` | `frontend:324-335` | the tailscale `# NO env_file HERE` comment … `TAILSCALE_AUTH_KEY` |
| `:207`, `:232`, `:236` | `frontend:198-205`/`297-303`, `:162-193`, `:198-205, 297-303` | `build:`…`image: openwebui:local`; `deploy:`…`capabilities: [ gpu ]`; the whole `openwebui-stock` block |
| `:208`, `:241`, `:250` | `frontend:323` ×3 | `network_mode: service:openwebui` |
| `:255`, `:256`, `:257` | `memory:153-155`, `:54`, `:56` | `llm-net:`…`name:`; `LLM_BASE_URL`; `EMBED_BASE_URL` |
| `:274`, `:282` | `search:187-189`, `:43-46` | `default:`…`name: ai-stack_default`; `cap_add:`…`/dev/net/tun` |
| `:294`, `:295`, `:296` | `coder:226-228`, `:46 and :93`, `:65-66` | the llm-net seam; two `- llm-net` joins; `NO_PROXY`/`no_proxy` |
| `:443`–`:456` | `agent-org:764-766, 183, 313, 420-421, 499-500, 345, 437, 399, 480, 198, 208, 197, 207` | see the sentences; each names its construct |
| `:490`, `:492` | `portal:593-595`, `:591-592` | `app-net:`/`external: true`/`name: ai-stack_app-net`; the two-line `# The ai-stack seam` comment |
| `:500`, `:501` | `gateway.yml:173`, `:182-184` | `llm-gateway-ui:`; its `networks:` / `- llm-net` / `- app-net` |
| `.env.example:158,162,229` | `memory:127`, `frontend:408`, `coder:197` | the three `BACKUP_INTERVAL=` lines |
| `.env.example:333,346` | `backups.yml:62, 96, 103-105`, `:35,53` | the disable comment / `LM_MODELS_BACKUP_INTERVAL` / the `if [ -z … ]` guard; `llm-gateway-backup:` and its entrypoint |
| `CLEANUP-PLAN.md:755` | `agent-org:520,590` | the two egress `build:` keys |

**Fails** if any citation lands on an unrelated construct or past the end of its
file.

**Two of these were ALREADY broken on `development`** and were repaired by
construct rather than by offset — findings §C. `stack.manifest.toml`'s two portal
citations were off by exactly 4 lines (they named `notify-net`, not `app-net`),
and `CLEANUP-PLAN.md:755` was off by 41 (it named a `profiles:` line and an env
var, not the two egress builds). Verify BOTH the old breakage (in `$SC/base`) and
the new correctness; a repair you cannot show was needed is not verified.

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

**Run** in a checkout of `work/sl-compose-anchors`:

```sh
ruff check .
python -m pytest scripts/stack -q
python scripts/stack/stack.py inventory --check
```

**Passes** on `All checks passed!`, `107 passed`, and an `inventory --check` whose
last line is `[OK] scripts/lib/stack-services.json matches the manifest, the
sidecar and the compose renders`. The `[ ~~ ]` lines about OB1 profiles
"declared, not rendered" are pre-existing and expected (the submodule gitlink has
not moved); they are not failures.

**Fails** on a ruff finding, any test failure, or an `inventory --check` that
reports drift. The inventory reads container rows out of the compose renders, so
a drift report here would mean T1 missed something.

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
* §C1/§C2 — read `git show development:portal/docker-compose.yml` lines 601-609 and `git show development:agent-org/docker/docker-compose.yml` lines 440, 481, 516, 557.
* §D1 — read `portal/docker-compose.yml`: `portal-init` has no `read_only` and `user: "0:0"`; `portal-cron` has no `user:` key between its own service key and `cloudflared:`. Then read `SECURITY.md:55` and `:163`.
* §D3 — `grep -cE '^ *- "com\.centurylinklabs\.watchtower\.enable=false"'` over the nine plane files must total **47** with the per-file split the note gives.
* §D6 — `grep -rn '^ *logging:' ` over the nine plane files must return nothing.

**Passes** when every claim checks out at the strength it is stated.

**Fails** on any claim that is false, or stated more confidently than its own
provenance label supports. A wrong entry here is worse than a wrong entry in the
compose files, because the next item reads this file and acts on it.
