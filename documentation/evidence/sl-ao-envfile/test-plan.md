# Test plan — `sl-ao-envfile`

Item: the agent-org worker pool stops granting itself the whole root `.env`.
Branch `work/sl-ao-envfile`, base `development` at `bdcc7f1`.
Anchor: `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-ao-envfile.json`.
Findings: `documentation/notes/stack-layers-sl-ao-envfile-findings.md`.

**Written by the developer; executed by someone who did not write it.**

## Rules for this run

1. **No container is touched.** Every command here is a render, a read, or a
   check. `docker compose ... config` starts, stops and inspects nothing.
   `ao-worker-1` and `ao-worker-2` stay up throughout; their recreate is the
   LANDING step and belongs to the orchestrating session under the agent-org
   lease, not to this test.
2. **No `:local` image is built or tagged, and nothing attaches to an
   `ai-stack_*` network.**
3. Work in **scratch clones**, not in the developer's worktree and not in the
   main checkout. Short paths (`D:\t\...`) — the worktree path is long enough to
   need `core.longpaths`.
4. `$?` after a pipeline in bash is the LAST command's exit code, not the
   interesting one. Capture exit codes on their own line (`cmd > out 2> err;
   echo "exit=$?"`), never after a `| head`/`| tail`.
5. Report what you ran and what it printed. A case passes on its OUTPUT, not on
   this document's expectation being plausible.

## Setup (once)

```bash
mkdir -p /d/t && cd /d/t && rm -rf br dev
git -c core.longpaths=true clone -q --no-checkout "D:/Open WebUI/ai-stack/.claude/worktrees/wt-sl-ao-envfile" br
cd /d/t/br && git checkout -q <TIP>          # the submitted sha
cd /d/t && git -c core.longpaths=true clone -q --no-checkout "D:/Open WebUI/ai-stack" dev
cd /d/t/dev && git checkout -q bdcc7f1
# seed each plane env from its own example
cp /d/t/br/agent-org/docker/.env.example  /d/t/br/agent-org/docker/.env
cp /d/t/dev/agent-org/docker/.env.example /d/t/dev/agent-org/docker/.env
```

Do **not** create `/d/t/br/.env`. Case T1 depends on it being absent.

---

## T1 — a fresh clone of the branch renders agent-org with NO root `.env`; development cannot

Criterion: the plane is self-contained.

```bash
cd /d/t/br  && docker compose -f agent-org/docker/docker-compose.yml --profile workers config > /d/t/br.workers.yml 2> /d/t/br.workers.err; echo "br exit=$?"
cd /d/t/dev && docker compose -f agent-org/docker/docker-compose.yml --profile workers config > /d/t/dev.nofile.yml 2> /d/t/dev.nofile.err; echo "dev exit=$?"
cat /d/t/br.workers.err; echo "---"; cat /d/t/dev.nofile.err
```

Expect: `br exit=0` with an EMPTY stderr. `dev exit=1`, stderr containing
`env file D:\t\dev\.env not found` plus four
`The "LC_LLAMA_API_KEY" variable is not set` warnings.

Disproves it: `br` exiting non-zero, or any byte on `br`'s stderr, or `dev`
exiting 0 (which would mean the wildcard was not load-bearing and the premise of
the item is wrong).

---

## T2 — the normalized render diff is exactly the expected rows and nothing else

Criterion: acceptance #1. To get a comparable render out of development, give it
the root `.env` it demands:

```bash
cp /d/t/dev/.env.example /d/t/dev/.env
cd /d/t/dev && docker compose -f agent-org/docker/docker-compose.yml --profile workers config > /d/t/dev.workers.yml 2> /d/t/dev.workers.err; echo "dev exit=$?"
cat /d/t/dev.workers.err          # still 4 warnings, no error
# normalize the absolute repo prefix, then diff
python - <<'EOF'
import io
BS=chr(92)
for src,letter,out in (("/d/t/dev.workers.yml","dev","/d/t/dev.norm.yml"),("/d/t/br.workers.yml","br","/d/t/br.norm.yml")):
    s=io.open(src,encoding="utf-8").read()
    s=s.replace("D:"+BS+"t"+BS+letter+BS,"REPO"+BS).replace("D:/t/"+letter+"/","REPO/")
    io.open(out,"w",encoding="utf-8",newline="\n").write(s)
print("ok")
EOF
diff -u /d/t/dev.norm.yml /d/t/br.norm.yml
```

Expect EXACTLY these changed lines, and no others:

| service | change |
|---|---|
| `agent-bridge` | **+** `LC_DEPLOY_TOKEN: ""`, **+** `LC_LLAMA_API_KEY: llama` |
| `ao-ot-1`, `ao-ot-2` | `LLAMACPP_API_KEY: ""` -> `LLAMACPP_API_KEY: llama` |
| `ao-worker-1`, `ao-worker-2` | **+** `LC_DEPLOY_TOKEN: ""`, **+** `LC_LLAMA_API_KEY: llama`; **-** `NAS_BACKUP_USER`, **-** `NAS_BACKUP_PASSWORD`, **-** `TEST_VALIDATION_LLM_KEY`; `LLAMACPP_API_KEY: ""` -> `llama` |

**Read the anchor's wording before judging this.** The anchor says "every other
service in the plane is identical", and `agent-bridge`, `ao-ot-1` and `ao-ot-2`
are NOT identical. The developer asserts both rows are correct and unavoidable;
your job is to decide whether that holds:

* `agent-bridge` has `env_file: - .env` (its OWN file, `agent-org/docker/
  docker-compose.yml:165-166`, explicitly in scope to KEEP). Anything added to
  `agent-org/docker/.env.example` therefore reaches it. Declaring the two values
  in that file is the only way compose can interpolate them, so the alternative
  to this row is not having the fix. Check also that the bridge does not READ
  either name: `grep -rn "LC_DEPLOY_TOKEN\|LC_LLAMA_API_KEY" agent-org/agent-bridge/app/`
  should return docstring/comment mentions only, no `os.environ` read.
* The `ao-ot-*` row is caused by `.env.example` gaining `LC_LLAMA_API_KEY`, which
  those services ALREADY interpolated (`LLAMACPP_API_KEY=${LC_LLAMA_API_KEY}`);
  the compose lines for them are unchanged. Confirm with
  `git diff bdcc7f1..<TIP> -- agent-org/docker/docker-compose.yml` that no
  `ao-ot-*` line was edited.
* The three REMOVED names on the workers are the point of the item: they are
  what the root `.env` still contained, arriving in two agent containers that
  read none of them.

Disproves it: any diff row outside the table; a worker still carrying a
root-only name; a worker MISSING one of the ten keys it had before (count them:
`AO_BRIDGE_URL`, `AO_SUBJECT`, `LC_CONFIG`, `LC_OPEN_TERMINAL_KEY`,
`LC_OPEN_TERMINAL_URL`, `LC_ROUTE_EXEC`, `LC_WORKSPACE`,
`LITTLE_CODER_NO_CTX_PROBE`, `LLAMACPP_API_KEY`, `OPEN_TERMINAL_API_KEY`, plus
the two new = 12); or judging the three non-worker rows acceptable without
checking the two greps above.

Note: `docker compose config` RESOLVES `env_file` into `environment`, so "the
service renders with no `env_file` key" is true of every service in every render
and proves nothing on its own. The evidence that the grant is gone is the
absence of the root-only names, plus T3.

---

## T3 — the compose source carries no `env_file` on either worker, and agent-bridge keeps its own

```bash
cd /d/t/br && grep -n "env_file" agent-org/docker/docker-compose.yml
```

Expect: exactly one non-comment `env_file:` in the file (agent-bridge, followed
by `- .env`). Any other hits must be COMMENT lines (leading `#`). Confirm the
two worker services have none by reading each block from
`ao-worker-1:` / `ao-worker-2:` to its `volumes:`.

Disproves it: an `env_file:` key inside either worker block; agent-bridge's
`- .env` removed (explicitly out of scope).

---

## T4 — the other two profile selections

```bash
for sel in "--profile workers --profile cloud" "" ; do
  for d in dev br; do
    (cd /d/t/$d && docker compose -f agent-org/docker/docker-compose.yml $sel config > /d/t/$d.sel.yml 2> /d/t/$d.sel.err; echo "$d [$sel] exit=$?")
    echo "--- $d stderr:"; cat /d/t/$d.sel.err
  done
done
```

Expect: all four renders exit 0; `br` stderr EMPTY in both; `dev` stderr shows
four `LC_LLAMA_API_KEY` warnings in BOTH — including the bare one, because
compose interpolates the whole file regardless of profile selection.

Disproves it: a warning or error on `br`; a non-zero exit anywhere.

---

## T5 — re-measure the variable set yourself; do not accept the developer's list

Criterion: acceptance #2. This is the case that matters most, because a wrong
set here is a silent breakage at the next recreate.

```bash
cd /d/t/br
# A: what the image reads container-side
grep -rn "os\.environ\|getenv\|environ\.get" little-coder/src/littlecoder/
grep -rn "process\.env\|os\.environ\|getenv"  little-coder/pi-extension/
grep -rn "api_key_env\|open_terminal_key_env"  little-coder/src/littlecoder/ little-coder/config/
# B: the pre-split root example
git show 4934529:.env.example | grep -E "^[A-Za-z_][A-Za-z0-9_]*=" | cut -d= -f1 | sort -u > /d/t/B.txt
wc -l /d/t/B.txt          # expect 153 (157 assignments, 4 names assigned twice)
# A INTERSECT B, mechanically
while read v; do if grep -rq "\b$v\b" little-coder/src/littlecoder/ little-coder/pi-extension/; then echo "$v"; fi; done < /d/t/B.txt
```

Expect the intersection to be exactly: `LC_DEPLOY_TOKEN`, `LC_LLAMA_API_KEY`,
`LC_ROUTE_EXEC`, `OPEN_TERMINAL_API_KEY`. Then confirm against the workers'
`environment:` block that the last two were ALREADY set and the first two were
not — so exactly two names were added.

Check each claimed read by opening the file:

| name | claim | where |
|---|---|---|
| `LC_DEPLOY_TOKEN` | direct read, deploy/clone path | `little-coder/src/littlecoder/daemon.py:520`, `:554` — search `os.environ.get("LC_DEPLOY_TOKEN")` |
| `LC_LLAMA_API_KEY` | INDIRECT: config names it, code reads the name | `config.py:38` `api_key_env: str = "LC_LLAMA_API_KEY"`; `meta_wiring.py:42`, `:48`; `agent.py:271` |
| `OPEN_TERMINAL_API_KEY` | INDIRECT, already set by the block | `config.py:91`; `daemon.py:183`; `agent.py:265` |
| `LITTLE_CODER_VERSION` | in B, ruled OUT: build ARG only | `little-coder/docker/Dockerfile.agent:36-37` |

Line numbers rot. If one is off, find the construct and report the real number
rather than failing the case on the digits.

Also run the negative half — the live root file, names only, never values:

```bash
grep -E "^[A-Za-z_][A-Za-z0-9_]*=" "D:/Open WebUI/ai-stack/.env" | cut -d= -f1 | sort -u > /d/t/live.txt
while read v; do if grep -rq "\b$v\b" little-coder/src/littlecoder/ little-coder/pi-extension/; then echo "STILL-READ: $v"; fi; done < /d/t/live.txt
```

Expect no output. `LC_POLYSHDESIGN_TOKEN` looks like a worker variable and is
not one — it is read by the BRIDGE
(`agent-org/agent-bridge/app/modules/projects.py` `owner_token_env`) out of
agent-org's own `.env`.

Disproves it: any fifth name in the intersection; any name in the added pair
that no read supports; any live-root name the source reads.

---

## T6 — the check refuses a grant in EVERY value shape compose accepts, and carries no exemption

Criterion: acceptance #3. **Attempt 1 failed here**: two valid shapes rendered by
docker as real grants passed the check green. So this case is now a shape MATRIX,
and a pass requires every row.

### T6a — the matrix

Plant one shape at a time, stage it, run the check in staged mode, restore.
**Anchor the plant on the service HEADING at column 2 with a regex** — `"  ao-ot-1:\n"`
occurs TWICE in agent-org's compose file (the second is the six-space `depends_on`
entry), so a `count(...)==1` assert aborts and dropping the assert plants the block
inside `depends_on`, where it is not an `env_file` at all. Attempt 1's plan had that
bug; this is the fixed driver:

```bash
cd /d/t/br && git config core.hooksPath .githooks
cat > /d/t/shapes.py <<'PYEOF'
import io, os, re, subprocess, sys
ROOT, REL, SERVICE = sys.argv[1], sys.argv[2], sys.argv[3]
PS = os.path.join(ROOT, 'scripts', 'checks', 'check-env-file-scope.ps1')
TARGET = os.path.join(ROOT, *REL.split('/'))
SHAPES = [
 ('scalar',                '    env_file: ../../.env\n', True),
 ('scalar-quoted',         '    env_file: "../../.env"\n', True),
 ('scalar-comment',        '    env_file: ../../.env  # shared\n', True),
 ('flow-seq',              '    env_file: [../../.env]\n', True),
 ('flow-seq-quoted',       '    env_file: ["../../.env"]\n', True),
 ('flow-seq-two-one-bad',  '    env_file: [.env, ../../.env]\n', True),
 ('flow-seq-unterminated', '    env_file: [../../.env\n', True),
 ('block-item',            '    env_file:\n      - ../../.env\n', True),
 ('block-item-quoted-cmt', '    env_file:\n      - "../../.env" # shared\n', True),
 ('block-item-dotslash',   '    env_file:\n      - ./../../.env\n', True),
 ('longform-path',         '    env_file:\n      - path: ../../.env\n        required: false\n', True),
 ('longform-path-quoted',  '    env_file:\n      - path: "../../.env"\n        required: true\n', True),
 ('longform-required-1st', '    env_file:\n      - required: false\n        path: ../../.env\n', True),
 ('longform-flow-map',     '    env_file:\n      - {path: ../../.env, required: false}\n', True),
 ('deeper-root',           '    env_file:\n      - ../../../.env\n', True),
 ('absolute',              '    env_file:\n      - D:/x/.env\n', True),
 ('root-envtest',          '    env_file:\n      - ../../.env.test\n', True),
 ('cross-plane',           '    env_file:\n      - ../../coder/.env\n', True),
 ('interpolated',          '    env_file:\n      - ${SOME_ENV_FILE}\n', True),
 ('own-dir',               '    env_file:\n      - .env\n', False),
 ('own-dir-quoted-cmt',    '    env_file:\n      - ".env"  # the plane\'s own\n', False),
 ('own-dir-dotslash',      '    env_file:\n      - ./.env\n', False),
 ('own-dir-flow',          '    env_file: [.env]\n', False),
 ('own-dir-longform',      '    env_file:\n      - path: .env\n        required: false\n', False),
 ('blank-line-in-list',    '    env_file:\n      - .env\n\n      - ../../.env\n', True),
 ('climb-back-to-own-dir', '    env_file:\n      - ../docker/.env\n', True),
 ('parent-plane-own',      '    env_file:\n      - ../.env\n', False),
]
def run(a):
    p = subprocess.run(a, cwd=ROOT, capture_output=True, text=True)
    return p.returncode, (p.stdout or '') + (p.stderr or '')
base = io.open(TARGET, encoding='utf-8', newline='').read()
h = re.search(r'^  ' + re.escape(SERVICE) + r':[ \t]*$', base, re.M)
assert h, 'service heading not found at column 2'
cut = h.end() + 1
fails = 0
for name, block, red in SHAPES:
    io.open(TARGET, 'w', encoding='utf-8', newline='').write(base[:cut] + block + base[cut:])
    run(['git', 'add', '--', REL])
    code, out = run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',PS])
    line = next((l.strip() for l in out.splitlines() if ' -- ' in l), '')
    ok = 'OK ' if (code == 1) == red else 'BAD'
    fails += 0 if (code == 1) == red else 1
    print('%s %-22s expect=%-5s exit=%d  %s' % (ok, name, 'RED' if red else 'GREEN', code, line[:120]))
io.open(TARGET, 'w', encoding='utf-8', newline='').write(base)
run(['git', 'add', '--', REL]); run(['git', 'reset', '-q'])
print('--- mismatches:', fails)
sys.exit(1 if fails else 0)
PYEOF
python /d/t/shapes.py /d/t/br agent-org/docker/docker-compose.yml ao-ot-1
```

Expect: 27 rows, **`mismatches: 0`**, exit 0 from the driver. Two rows carry the
reasoning, not just a verdict: `climb-back-to-own-dir` (`- ../docker/.env` from
`agent-org/docker/`) is REFUSED by the fail-closed backstop even though it resolves
somewhere legal, because a value that climbs and lands back home is what a
mis-parsed value looks like; `parent-plane-own` (`- ../.env` = `agent-org/.env`) is
ALLOWED, because a parent that is not the repo root is a plane's own file. Read the reasons, not
just the exits — a RED row for the wrong reason is still a defect worth reporting.

Then the same driver against a compose file one level BELOW its plane directory,
where `../.env` is the plane's OWN file and `../../.env` is the repo root — the pair
the resolution has to tell apart in every shape. Edit `SHAPES` to the ten rows below
and run it on `inference/compose/upstreams.yml` / `llama-cpp-upstream`:

| shape | expect |
|---|---|
| `env_file: ../.env` · `[../.env]` · `- ../.env` · `- path: ../.env` | GREEN (= `inference/.env`) |
| `env_file: ../../.env` · `[../../.env]` · `- ../../.env` · `- path: ../../.env` · `- {path: ../../.env, required: false}` | RED, `is the repo root env file` |
| `- .env` | GREEN |

### T6b — the two shapes are real grants, per docker, not per this plan

The point of T6a's flow-sequence and long-form rows is that docker HONOURS them.
Confirm it yourself rather than taking the claim:

```bash
cd /d/t/br && cp .env.example .env          # the root file the grant would deliver
# plant env_file: [../../.env] on ao-ot-1 (same regex anchor as above), then:
docker compose -f agent-org/docker/docker-compose.yml --profile workers config | grep -nE "NAS_BACKUP_USER|TEST_VALIDATION_LLM_KEY"
# repeat with the long form: - path: ../../.env / required: false
```

Expect BOTH shapes to inject the root file's names into `ao-ot-1`, a service that
carries none of them otherwise — render exit 0, two leaked names each. That is what
makes a green from the check on those shapes a defect and not a style preference.
Restore the file afterwards.

### T6c — unplanted, before/after, and no exemption

```bash
cd /d/t/br
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-env-file-scope.ps1 -All; echo "exit=$?"   # expect 0
git status --short                                                                                                 # expect clean
```

`- .env` on agent-bridge is ALLOWED: it is in the file throughout every run above and
must never appear in any output. State that you observed its absence.

The before/after that answers "why did the two grants pass at all":

```bash
cd /d/t/dev && printf '\n# scratch\n' >> agent-org/docker/docker-compose.yml && git add agent-org/docker/docker-compose.yml
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-env-file-scope.ps1; echo "OLD exit=$?"
cp /d/t/br/scripts/checks/check-env-file-scope.ps1 /d/t/new-check.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File /d/t/new-check.ps1 -Root "D:/t/dev"; echo "NEW exit=$?"
```

Expect OLD: two `(pre-existing, not blocked)` lines then
`no new shared-.env grants staged`, **exit 0**. NEW on the identical staged state:
two violations, **exit 1**.

Finally read the script end to end: **no service name, no path allow-list, no HEAD
comparison.** `grep -n "ao-worker\|exempt\|allow\|HEAD:"` must return nothing but
prose in the header.

Disproves it: any matrix mismatch; a shape docker honours that the check passes; a
legitimate plane-own `../.env` or own-directory `.env` flagged; any per-service
exemption surviving; the OLD/NEW pair not differing.

---

## T7 — the two names are declared in the example, and the warnings are gone

Criterion: acceptance #4.

```bash
cd /d/t/br && grep -n "^LC_DEPLOY_TOKEN=\|^LC_LLAMA_API_KEY=" agent-org/docker/.env.example
```

Expect both, as real assignments (not prose), with the comment block above them
stating the D10 rule (a value two planes read is declared in EACH, `coder/.env`
carries them too) and that the LLM key is a LiteLLM VIRTUAL key. Cross-check the
D10 half is true: `grep -n "^LC_LLAMA_API_KEY\|^LC_DEPLOY_TOKEN" coder/.env.example`.

The warning half is T1/T4's stderr: **exit 0 WITH warnings is a FAIL**, so read
the `.err` files, do not infer from the exit code.

Disproves it: either name missing or commented out; a `variable is not set`
warning naming either one on any branch render; `coder/.env.example` not
carrying them (which would make the D10 sentence false).

---

## T8 — the four documents say what is true NOW

Criterion: acceptance #5. Read each against
`/d/t/br/agent-org/docker/docker-compose.yml`, not against memory.

1. `documentation/runbooks/env-split-migration.md` step 4b — must say the
   wildcard is GONE, must not instruct the operator to work around a grant that
   no longer exists, must still tell a migrating operator to check both names in
   `agent-org/docker/.env` and to `--force-recreate` the pool. The measurement
   table it keeps must match what you measured in T5.
2. `documentation/notes/stack-layers-sl-env-split-findings.md` §14 — its
   historical narrative is allowed to stay historical; the CLOSED paragraph at
   its end must be accurate. Check specifically that the note no longer cites
   line numbers into agent-org's compose file (its old `:300`/`:398` were stale
   before this item touched anything).
3. `agent-org/README.md` — the new Environment section. Every sentence is a
   claim: compose loads `agent-org/docker/.env` natively; the workers have no
   `env_file`; the two names are in each worker's `environment:`;
   `coder/.env` declares the same two names with its own values; a running
   worker keeps its environment so a change needs `--force-recreate`. Attempt 1
   also said `.env.example` was "the complete template", which
   `AO_OT1_IMAGE`/`AO_OT2_IMAGE` refuted; check the replacement sentence the same
   way - grep the compose file for names it gives a `:-` default, and the example
   for the two now documented there as commented optional overrides.
4. `.githooks/README.md` row 5 — the pre-commit table, the first place anyone
   looks to learn what check 5 blocks on. Attempt 1 left it describing the
   grandfathering clause the item deleted ("a commit ADDING a service that grants
   itself a shared .env (pre-existing grants are reported, not blocked)"). Read
   the row against the script header and against T6c's OLD/NEW pair.

Disproves it: any sentence in any of the three that the compose file refutes;
a live-sounding instruction to do something about a grant that is gone.

---

## T9 — gates

Run in the branch clone, with the whole delta staged against `bdcc7f1`:

```bash
cd /d/t/br
ruff check .                                                            ; echo "ruff=$?"
python scripts/stack/stack.py inventory --check > /d/t/inv.txt 2>&1     ; echo "inventory=$?"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-project-configs.ps1 > /d/t/cfg.txt 2>&1 ; echo "configs=$?"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-hook-attestation.ps1 -Branch work/sl-ao-envfile -Base development ; echo "attest=$?"
# stray CR in changed blobs
git diff --name-only bdcc7f1..HEAD | while read f; do case "$f" in *.ps1) continue;; esac; git show "HEAD:$f" | grep -qU $'\r' && echo "CR: $f"; done
# BOM / non-ASCII in the changed .ps1
python - <<'EOF'
import io
d=io.open("scripts/checks/check-env-file-scope.ps1","rb").read()
print("BOM" if d[:3]==b"\xef\xbb\xbf" else "no BOM", "| non-ascii bytes:", sum(1 for b in d if b>127))
EOF
```

Expect: `ruff=0`; `inventory=0`; `configs=0`; attestation 0 with every commit
attested - the count is whatever the branch carries, do NOT check it against a
number in this plan (attempt 1's plan said 5 when there were 6); no `CR:` lines;
`no BOM | non-ascii bytes: 0`. Note the .ps1 blob in git is LF - `.gitattributes`
says `*.ps1 text eol=crlf`, i.e. CRLF in the working tree, LF in the object - so
do NOT run the CR sweep over the .ps1 (the loop above skips it).

**Both `inventory --check` and `check-project-configs.ps1` need a root `.env` to
exist in the clone** - without one the anchor project cannot be rendered and the
`projects.ai-stack` row comes out as a FAIL that has nothing to do with this
item. So run T9 in a clone where you have done `cp .env.example .env`, which
means NOT the T1 clone (T1 requires the root file to be absent) - either make a
third clone or run T9 after T1/T2, once `/d/t/br/.env` exists. Measured: with the
root file present, both gates exit 0 at the tip and `inventory --check`'s output
is byte-identical to `bdcc7f1`'s; without it, both exit 1 on that one row.

If `inventory` is non-zero anyway, re-run it at `bdcc7f1` under the SAME seeding
and compare byte for byte before calling it a defect of this item.

Disproves it: any non-zero; a CR in a non-`.ps1` blob; a BOM or any non-ASCII
byte in the check script.

---

## T10 — every sentence this item introduces, and what settles it

The item adds prose in five files. Each row is a CLAIM; the right-hand column is
what decides it. A row you cannot settle from the repo is a FAIL, not a
judgement call.

| # | claim | stated in | settled by |
|---|---|---|---|
| 1 | Neither worker has an `env_file` key | compose comments, runbook 4b, README, findings §1 | T3 (grep the compose source) |
| 2 | The two added names are the only ones the image reads that the wildcard carried and the block did not already set | commit message, runbook 4b table, findings §1 | T5 (re-run the intersection) |
| 3 | `LC_DEPLOY_TOKEN` is the clone path's GLOBAL FALLBACK, overridden by a per-request token | compose comment, README table, findings §1 | `daemon.py:520`/`:554` — `req.token or os.environ.get(...)`; the bridge side is `orchestrator.py` `_project_token` |
| 4 | `LC_LLAMA_API_KEY` is read INDIRECTLY and `LLAMACPP_API_KEY` does not cover it | compose comment, README, runbook, findings | `config.py:38` + `meta_wiring.py:42/:48`; and `grep -rn LLAMACPP_API_KEY little-coder/src little-coder/pi-extension` returning ONE hit, `agent.py:270`, where little-coder WRITES it into the child env from `api_key_env` - it never reads it |
| 5 | `LITTLE_CODER_VERSION` is a build ARG and needs no container entry | runbook 4b, findings §1 | `little-coder/docker/Dockerfile.agent:36-37` |
| 6 | Compose loads `agent-org/docker/.env` natively from the project directory, so `${...}` interpolates without `env_file` | compose comment, README, findings | T2/T4 renders resolving `LC_LLAMA_API_KEY` to `llama` with no `--env-file` anywhere |
| 7 | D10: a value two planes read is declared in EACH; `coder/.env` carries both names with its OWN values | `.env.example` comment, README, findings | `grep -n "^LC_LLAMA_API_KEY\|^LC_DEPLOY_TOKEN" coder/.env.example` |
| 8 | The pool's LLM key is a LiteLLM VIRTUAL key, minted from the inference plane | `.env.example` comment, README | `coder/.env.example`'s wording for the same variable; the J.1 cutover doc named in CLAUDE.md |
| 9 | The check passed the two grants because of HEAD grandfathering, not a regex miss and not a named exemption | commit message, findings §3, runbook 4b | T6's OLD/NEW pair on an identical staged state, plus reading the removed clause in `git show bdcc7f1:scripts/checks/check-env-file-scope.ps1` |
| 10 | The check now refuses any target resolving to the repo root `.env`, at any depth, with no exemption | script header, findings §3, runbook 4b | T6 (planted `../../.env`; and `../../../.env` from a deeper file if you want the "any depth" half) |
| 11 | A plane's own `.env`, including a parent that is not the root, is still allowed | script header, findings §3 | T6's `- .env` and `- ../.env` rows |
| 12 | `-All` was blind inside an agent worktree and is not any more | findings §3a, commit message | run `-All` from `D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-ao-envfile` with `git show bdcc7f1:...` saved to a temp file (blind, exit 0) vs the branch copy (sees the tree) |
| 13 | `inference/compose/*.yml` were outside the check's file set and are now inside it | findings §3b, commit message | T6's `inference/compose/upstreams.yml` plants, and `git show bdcc7f1:scripts/checks/check-env-file-scope.ps1` showing the basename-only filter |
| 14 | The three removed worker variables (`NAS_BACKUP_*`, `TEST_VALIDATION_LLM_KEY`) were what the wildcard still delivered | findings §2 | T2's diff |
| 15 | `agent-bridge` receives the two names but reads neither, and already did on the live host | findings §2 | `grep -rn` in `agent-org/agent-bridge/app/` returning only docstrings; the live `agent-org/docker/.env` already declaring both (names only — do NOT print values) |
| 16 | The live root `.env` holds 23 assignments, none of which the worker source reads | findings §4 | the T5 negative half |
| 17 | No container was touched by the developer, and the recreate is the landing step | findings §4, commit messages, anchor | `docker ps --format "{{.Names}}\t{{.CreatedAt}}"` for `ao-worker-1`/`-2` showing a creation time OLDER than the branch's first commit. **Read-only — do not restart them.** |

### Attempt 2 - the sentences THIS round introduces

Attempt 1 failed on T6/T8/T10. Rows 18-27 are the claims the repair adds; row 10
is restated because its wording is what the failure refuted.

| # | claim | stated in | settled by |
|---|---|---|---|
| 10' | The check refuses any target resolving to the repo root `.env` **in every value shape compose accepts**, at any depth, with no exemption | script header, findings section 3/3c, runbook 4b, `.githooks/README.md:27` | T6a's 27-row matrix and T6's fragment table - the claim now says "in every value shape", and attempt 1's version was refuted by two of them |
| 18 | The verdict parses the value into PATHS before resolving: scalar, flow sequence, block sequence, long-form `path:` (inline, on a continuation line, or as a flow mapping), quoted, commented | script header, `Get-EntryPath`/`Get-InlineValueEntries`, findings 3c | T6a matrix rows - each shape planted alone and staged |
| 19 | A value that does not parse into plain path text is REFUSED, not normalized | script header, findings 3c | T6a rows `flow-seq-unterminated` (`is not plain path text ('[../../.env')`) and `interpolated` (`${SOME_ENV_FILE}`) |
| 20 | A value that climbs with `..` and resolves back inside the compose file's own directory is refused (the fail-closed backstop for shapes nobody has thought of) | script header, findings 3c | T6a row `climb-back-to-own-dir` - RED with the climb message |
| 21 | `env_file: [../../.env]` and `- path: ../../.env` are REAL grants: docker honours both | findings 3c, this plan's T6b | T6b - render each on `ao-ot-1` with a root `.env` present and see the root names appear in a service that has none |
| 22 | It was a REGRESSION this item introduced, not a hole it inherited | findings 3c, commit message | run `git show bdcc7f1:scripts/checks/check-env-file-scope.ps1` on the same two plants: its `Test-BroadTarget` (`-match '\.\.[\\/]'`) goes red on both |
| 23 | `.githooks/README.md` row 5 describes the check as it now behaves | `.githooks/README.md:27` | T8 item 4, read against the script header and T6c |
| 24 | "Every variable a service in this plane needs SET is declared there"; names the compose file gives a `${VAR:-default}` are deliberately absent | `agent-org/README.md` | grep the compose file for `:-` defaults; confirm every interpolated name that is NOT in `.env.example` has one |
| 25 | `AO_OT1_IMAGE`/`AO_OT2_IMAGE` are documented in `agent-org/docker/.env.example` as COMMENTED optional overrides | `.env.example`, `agent-org/README.md` | grep the example for `#AO_OT1_IMAGE=`; confirm both lines are commented |
| 26 | Adding them to the example changes NO render | implied by 25 | re-run T2's normalized diff against the attempt-1 tip `4b714de` as well as against `bdcc7f1`: the tip-to-tip render diff must be EMPTY |
| 27 | The base's no-root-`.env` gate failure aborts EARLIER than the tip's, on agent-org's render refusal, not on the `projects.ai-stack` row | findings section 4 | run `stack.py inventory --check` at `bdcc7f1` and at the tip, each in a clone with no root `.env`, and read both messages |


---

## Verdict

PASS requires every case above to pass on its own output, with T2's three
non-worker diff rows explicitly judged (not waved through), T5 re-measured
from source rather than read off this document, and T6a/T6b run in full -
attempt 1 passed a T6 that planted one shape, and the two it did not plant
were the defect.
