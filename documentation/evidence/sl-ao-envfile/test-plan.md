# Test plan — `sl-ao-envfile`

Item: the agent-org worker pool stops granting itself the whole root `.env`.
Reopened as **`sl-ao-envfile2`** after `sl-ao-envfile` was rejected at review as a
MISFIT: a commented `env_file:` key let a docker-honoured grant pass, which the
item's own four documents assert cannot happen. Same branch, same worktree.
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

## T6 — the check SEES every grant (extent), READS every shape, and refuses indirection by policy

Criterion: acceptance #3. **Attempt 1 failed here**: two valid shapes rendered by
docker as real grants passed the check green. So this case is now a shape MATRIX,
and a pass requires every row.

### T6a-i — the value-shape matrix

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

### T6a-ii — the same driver from a `compose/` fragment

Run it against a compose file one level BELOW its plane directory,
where `../.env` is the plane's OWN file and `../../.env` is the repo root — the pair
the resolution has to tell apart in every shape. Edit `SHAPES` to the ten rows below
and run it on `inference/compose/upstreams.yml` / `llama-cpp-upstream`:

| shape | expect |
|---|---|
| `env_file: ../.env` · `[../.env]` · `- ../.env` · `- path: ../.env` | GREEN (= `inference/.env`) |
| `env_file: ../../.env` · `[../../.env]` · `- ../../.env` · `- path: ../../.env` · `- {path: ../../.env, required: false}` | RED, `is the repo root env file` |
| `- .env` | GREEN |

### T6a-iii — YAML indirection is refused BY POLICY, and the allowlist boundary

Attempt 3 failed here: the check FOLLOWED aliases, and an anchor defined twice broke
the lookup (it kept the first definition; YAML takes the last preceding one). The
decision that followed is the thing this case now tests - **the check no longer
interprets YAML semantics at all**:

> an `env_file` value must be a plain path literal; YAML anchors and aliases are
> refused by policy (rewrite as the path)

A policy refusal prints that rule verbatim. Match on this exact string when you
judge a row's REASON, and report any drift between it and the script's
`$script:IndirectionMessage`:
`env_file values must be plain path literals; YAML anchors and aliases are refused by policy (rewrite as the path)`.

So **every alias row is RED, including an alias to a perfectly legal path.** That is
the deliberate trade, not an oversight - if you think a green belongs on the
`*own_env -> .env` row, say so, but the design says red.

Plant a PRELUDE before `services:` as well as a block under the service (insert at
`re.search(r'^services:[ \t]*$', base, re.M).start()` and offset the service cut by
the prelude's length). Rows:

| # | prelude | block under `ao-ot-1` | expect |
|---|---|---|---|
| 1-4 | `x-root-env: &root_env ../../.env` | `env_file: *root_env` / `  - *root_env` / `[*root_env]` / `- path: *root_env` | RED, policy |
| 5 | `x-shared:` / `  env: &blk_env ../../.env` | `env_file: *blk_env` | RED, policy |
| 6 | `x-envs: &envs [../../.env]` | `env_file: *envs` | RED, policy |
| 7 | none | `env_file: *nosuch` | RED, policy |
| 8 | `x-own-env: &own_env .env` | `env_file: *own_env` | **RED, policy** - the trade |
| 9 | `x-a: &root_env .env` + `x-b: &root_env ../../.env` | `env_file: *root_env` | **RED, policy** - attempt 3's defect |
| 10 | none, anchor appended at END of file | `env_file: *late_env` | RED, policy (alias before anchor) |
| 11-12 | none | `env_file: &e ../../.env` / `env_file: &e .env` | RED, policy |
| 13 | none | `env_file:` / `  - &shared ../../.env` | RED, policy |
| 14-15 | none | `env_file: !!str ../../.env` / `!mytag ../../.env` | RED, policy |
| 16-17 | none | `env_file: >` + indented path / `env_file: |` + indented path | RED, policy |
| 18 | none | `env_file: ${SOME_ENV_FILE}` | RED, policy |
| 19 | `x-tpl: &tpl` / `  env_file:` / `    - ../../.env` | anything; the service may `<<: *tpl` | RED **at the `x-` block's line**, not the service's |
| 20-21 | none | `- D:/x/.env` / `- /etc/shared/.env` | RED, allowlist / outside repo |
| 22-23 | none | `- "../../my env/.env"` / a path containing a TAB | RED, allowlist |
| 24 | none | `env_file:<TAB>../../.env` (tab as separator) | RED, repo root env file |
| 25 | none | `- ../../.env\` (trailing backslash) | RED, repo root env file |
| 26-27 | none | `- config/dev.env` / `- ~/.env` | RED, belongs to another directory |
| 28 | none | the whole file rewritten with CRLF line endings | RED, repo root env file |
| 29-31 | none | `- .env` / `[.env]` / `- path: .env` | **GREEN** - a plain path literal in every shape still passes |

Row 19 is the merge-key claim and the one to read carefully: the check does not
understand `<<: *tpl` and does not need to, because the `x-` block's own `env_file:`
line is scanned where it is written. Confirm the violation's LINE NUMBER is the `x-`
block's, not the service's.

Rows 29-31 are what stops this being "refuse everything". If they go red the check is
broken in the other direction, and that is a FAIL too.

**Read the reasons, not only the exits.** A policy row that goes red via the allowlist
message, or an allowlist row that goes red via the policy message, means the value took
a different route than this table claims - report it.

### T6a-iv — WHERE the value sits, not only what it says

Attempt 4 failed here, and the three tables above did not catch it because every row in
them varies the CONTENT of the value and none varies its PLACEMENT. The scanner decided
what the value WAS before deciding where it ENDED, so any line it did not recognise
silently ended the block and was never read. Two plain-path spellings went green:

```yaml
    env_file:                        env_file:
      ../../.env                       -
                                         ../../.env
```

The extent rule the fix implements, which these rows test: **the value is every line
indented deeper than the `env_file:` key; a blank or comment-only line does not end it;
it ends at the first line at or below the key's indent; inside the extent a line the
reader does not recognise is treated as a VALUE, not as the end of the block.**

Use the T6a-i driver with a two-field tuple (name, block, expect) — no prelude needed:

| # | block under `ao-ot-1` | expect |
|---|---|---|
| 1-2 | `env_file:` / `  ../../.env` — plain, then quoted | RED, repo root env file |
| 3 | `env_file:` / `  .env` (next-line scalar, plane's own) | **GREEN** |
| 4-6 | `env_file:` / `  -` / `    ../../.env` — plain, quoted, and `path: ../../.env` | RED |
| 7 | `env_file:` / `  -` / `    .env` | **GREEN** |
| 8 | `env_file:` / `  - .env` / `      - ../../.env` (second item indented deeper) | RED — see the note below |
| 9 | a `# comment` line between the key and `- ../../.env` | RED |
| 10 | a BLANK line between the key and `- ../../.env` | RED |
| 11 | a comment AND a blank, then a next-line scalar `../../.env` | RED |
| 12 | `env_file:` / `../../.env` at EXACTLY the key's indent | **GREEN** — see the note |
| 13 | `env_file:` / `  - .env` / `image: x` at a shallower indent | **GREEN** (the shallower line ends the extent) |
| 14-15 | `- path: ../../.env` with `required: false` on a DEEPER line, then on the SAME | RED |
| 16 | two items, only the second bad | RED on the second |
| 17 | a TAB-indented item | RED |
| 18 | `env_file:` / `  - .env` then a sibling `labels:` key and its own list | **GREEN** (the extent must not swallow the rest of the service) |

Rows 3, 7, 12, 13 and 18 are the other direction: if the extent rule over-reaches, they
go red and that is a FAIL.

**Three rows where the check and docker deliberately disagree.** Measure docker
yourself (`cp .env.example .env`, plant, `docker compose ... config`, grep for
`NAS_BACKUP_USER`) and judge whether the disagreement is the right way round:

* Row 8: docker renders exit 0 and does NOT deliver the root file (it loads
  `agent-org/docker/.env` only). The check says RED anyway. The claim is that a line
  reading `- ../../.env` inside an env_file block should not pass merely because YAML's
  handling of a deeper dash is surprising.
* Rows 14-15, deeper variant: the file does not render at all
  (`mapping values are not allowed in this context`). The check says RED; the renderer
  refuses it for its own reason. Two refusals, one file.
* Row 12: docker refuses the file (`could not find expected ':'`) because a bare
  SCALAR at the key's indent cannot be read as the value. The check says nothing,
  which is correct rather than lenient - it is not a grant. **Note the word scalar.**
  Attempt 5 wrote this as "a value at the key's indent is not the value", which is
  false of a SEQUENCE - and that over-generalisation is exactly what T6a-v exists to
  pin down.

Rows 1-2 and 4-6 are the ones to render as well as check: each must put all three root
names on `ao-ot-1` under docker, which is what makes a green from the check a defect.

### T6a-v — the block sequence at the key's OWN indent

Attempt 5's regression, and the shape its own extent rule excluded. YAML lets a block
sequence sit at its parent key's indent, and compose files are commonly written that
way:

```yaml
    env_file:
    - ../../.env
```

Attempt 4's scanner refused this; attempt 5's passed it, and docker delivers all three
root names. The clause under test: **a line at exactly the key's indent whose body
starts with `-` is part of the value; anything else at or below the key's indent still
ends the extent.**

| # | block under `ao-ot-1` | expect |
|---|---|---|
| 1 | `env_file:` / `- ../../.env` at the key's indent | RED |
| 2 | the same, quoted | RED |
| 3 | `env_file:` / `- .env` at the key's indent | **GREEN** |
| 4 | two items at the key's indent, only the second bad | RED on the second |
| 5 | a bare `-` at the key's indent, item on the next deeper line | RED |
| 6 | `env_file:` / `- .env` / `image: x`, both at the key's indent | **GREEN** (a non-dash line still ends the extent) |

Render row 1 as well as checking it: all three root names must land on `ao-ot-1`, which
is what makes a green from the check a defect. Row 6 is the boundary in the other
direction - if it goes red the carve-out is swallowing the service.

### T6a-vi — a trailing comment on the key is not a value

The reject. `env_file:  # note` matches the inline capture, so the inline branch took
it, found nothing once the comment was stripped, and skipped the block value below -
which docker reads and honours. Green at EVERY tip before this one, `bdcc7f1` included.

| # | block under `ao-ot-1` | expect |
|---|---|---|
| 1 | `env_file:  # the shared root file` / `  - ../../.env` | RED |
| 2 | `env_file:  # note` / `  ../../.env` (next-line scalar) | RED |
| 3 | `env_file:  # note` / `- ../../.env` at the key indent | RED |
| 4-6 | the same three naming `.env` | **GREEN** |

Render rows 1-3: each must put all three root names on `ao-ot-1`. Then run the OLD blob
against the same plants to see the green this replaced:

```bash
git show ab430e7:scripts/checks/check-env-file-scope.ps1 > /d/t/old.ps1
# plant row 1, stage, then:
powershell -NoProfile -ExecutionPolicy Bypass -File /d/t/old.ps1 ; echo "old=$?"   # 0
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-env-file-scope.ps1 ; echo "new=$?"   # 1
```

### T6a-vii — the six rows the attempt-6 tester wrote

They lived only in that tester's evidence file, which the plan's own rule says is a row
the detector cannot defend. They are now in `regression-matrix.py` ROWS, reconstructed
from their names and checked against the tester's reported per-tip pattern AND their
reported docker behaviour. Verify the reconstruction rather than trusting it: run each
and compare to the attempt-6 evidence.

| row | expect | docker |
|---|---|---|
| `dash-key-indent-2sp` (`-` + two spaces) | RED | leaks 3 |
| `dash-key-indent-tab` (`-` + tab) | RED | render error |
| `dash-key-indent-then-path` (`- path:` at the key indent) | RED | leaks 3 |
| `bare-dash-key-then-path` (bare `-` at key indent, `path:` deeper) | RED | leaks 3 |
| `comment-between-key-dashes` | RED | leaks 3 |
| `sibling-key-then-other-list` (`hostname:` ends the extent, a later `dns:` list is not env_file) | **GREEN** | no grant |

`bare-dash-key-then-path` is the interesting one: green at every tip before attempt 6,
so it is a shape no earlier round caught rather than one this round restored.

### T6a-viii — the three the reviewer planted

| row | expect | docker |
|---|---|---|
| flow map with a QUOTED `path` key: `- {"path": ../../.env, required: false}` | RED | leaks 3 |
| multi-line flow sequence: `env_file: [` / `  ../../.env` / `]` | RED | leaks 3 |
| backslash separators: `- ..\..\.env` | RED | leaks 3 |

All three are in ROWS. Check the REASON on each: the flow map is caught as a repo-root
target, the multi-line sequence as `[` failing the plain-path-text allowlist, the
backslash form as a repo-root target after separator normalisation.

### T6b — these shapes are real grants, per docker, not per this plan

The point of T6a-i's flow-sequence and long-form rows is that docker HONOURS them.
Confirm it yourself rather than taking the claim:

```bash
cd /d/t/br && cp .env.example .env          # the root file the grant would deliver
# plant env_file: [../../.env] on ao-ot-1 (same regex anchor as above), then:
docker compose -f agent-org/docker/docker-compose.yml --profile workers config | grep -nE "NAS_BACKUP_USER|NAS_BACKUP_PASSWORD|TEST_VALIDATION_LLM_KEY"
# repeat with the long form:  - path: ../../.env / required: false
# and with the alias:         x-root-env: &root_env ../../.env before services:,
#                             then  env_file: *root_env  (and  - *root_env)
```

Expect ALL FOUR spellings to inject the root file's names into `ao-ot-1`, a service
that carries none of them otherwise — render exit 0; the two alias spellings deliver
all three names the seeded root file holds.

Two more worth rendering, because they are the reason the check stopped resolving
YAML at all:

* the DUPLICATE anchor - `x-a: &root_env .env` then `x-b: &root_env ../../.env`,
  service `env_file: *root_env`. Render exit 0, all three root names on `ao-ot-1`:
  YAML took the LAST definition, and attempt 3's lookup had kept the first.
* the MERGE KEY - `x-tpl: &tpl` / `  env_file:` / `    - ../../.env`, and on
  `ao-ot-1` replace `<<: *hardening` with `<<: [*hardening, *tpl]` (a second bare
  `<<:` key is a YAML duplicate-key error, so merge both in one). Render exit 0,
  all three names - and the check reports the `x-` block's line. That is what
makes a green from the check on those shapes a defect and not a style preference.
Restore the file afterwards.

### T6c-0 — the regression matrix: does this script still see what its predecessors saw?

**Run this first.** Five rounds produced five defects, and four of the five repairs
shipped the next counter-example. This is the case that would have caught that, and it
is the one most likely to catch a sixth.

```bash
cd /d/t/br
python documentation/evidence/sl-ao-envfile/regression-matrix.py /d/t/br /d/t/regression-matrix.md
echo "exit=$?"
diff -u documentation/evidence/sl-ao-envfile/regression-matrix.md /d/t/regression-matrix.md
```

It plants every row from T6a-i through T6a-viii, stages each, and runs the
`check-env-file-scope.ps1` blob from EVERY attempt tip (`4b714de`, `1bf6802`,
`86b5a7b`, `2168396`, `5ee330c`, `ab430e7`) and from the tip under test.
**102 rows x 7 tips = 714 cells, and it takes about 9 minutes** - one PowerShell
launch per cell. Start it first and let it run while you do T1-T5.

Expect: **exit 0**, and the regenerated table identical to the committed one except for
the `HEAD` sha line if you are testing a different commit. The assertions inside it:

* no row's result at this tip differs from its expected value;
* **no row is GREEN at this tip where any earlier tip was RED**, unless it is in the
  generator's `DELIBERATE_GREENS` map with a stated reason. On the submitted tip exactly
  two rows qualify - `plane-own-flow` and `plane-own-longform`, RED at attempt 1 only,
  because attempt 1 refused any target containing `..` and both are `../.env` from
  `inference/compose`, the inference plane's own file.

Read the table, do not just read the exit code. Each of the five defects appears as a
lone `G` ending at the round that fixed it; attempt 5's regression is the opposite
shape, a `G` in the middle of a row of `R`s. If you add a row of your own to any
matrix, add it to the generator too - a row that exists only in the plan is a row this
detector cannot defend.

Note the generator STAGES plants and resets after each row: run it in a scratch clone,
never in a worktree you care about.

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
| 10' | The check refuses any target resolving to the repo root `.env` **in every value shape compose accepts**, at any depth, with no exemption | script header, findings section 3/3c, runbook 4b, `.githooks/README.md:27` | T6a-i's 27-row matrix and T6a-ii's fragment table - the claim now says "in every value shape", and attempt 1's version was refuted by two of them |
| 18 | The verdict parses the value into PATHS before resolving: scalar, flow sequence, block sequence, long-form `path:` (inline, on a continuation line, or as a flow mapping), quoted, commented | script header, `Get-EntryPath`/`Get-InlineValueEntries`, findings 3c | T6a-i matrix rows - each shape planted alone and staged |
| 19 | A value that does not parse into plain path text is REFUSED, not normalized | script header, findings 3c | T6a-i rows `flow-seq-unterminated` (`is not plain path text ('[../../.env')`) and `interpolated` (`${SOME_ENV_FILE}`) |
| 20 | A value that climbs with `..` and resolves back inside the compose file's own directory is refused (the fail-closed backstop for shapes nobody has thought of) | script header, findings 3c | T6a-i row `climb-back-to-own-dir` - RED with the climb message |
| 21 | `env_file: [../../.env]` and `- path: ../../.env` are REAL grants: docker honours both | findings 3c, this plan's T6b | T6b - render each on `ao-ot-1` with a root `.env` present and see the root names appear in a service that has none |
| 22 | It was a REGRESSION this item introduced, not a hole it inherited | findings 3c, commit message | run `git show bdcc7f1:scripts/checks/check-env-file-scope.ps1` on the same two plants: its `Test-BroadTarget` (`-match '\.\.[\\/]'`) goes red on both |
| 23 | `.githooks/README.md` row 5 describes the check as it now behaves | `.githooks/README.md:27` | T8 item 4, read against the script header and T6c |
| 24 | "Every variable a service in this plane needs SET is declared there"; names the compose file gives a `${VAR:-default}` are deliberately absent | `agent-org/README.md` | grep the compose file for `:-` defaults; confirm every interpolated name that is NOT in `.env.example` has one |
| 25 | `AO_OT1_IMAGE`/`AO_OT2_IMAGE` are documented in `agent-org/docker/.env.example` as COMMENTED optional overrides | `.env.example`, `agent-org/README.md` | grep the example for `#AO_OT1_IMAGE=`; confirm both lines are commented |
| 26 | Adding them to the example changes NO render | implied by 25 | re-run T2's normalized diff against the attempt-1 tip `4b714de` as well as against `bdcc7f1`: the tip-to-tip render diff must be EMPTY |
| 27 | The base's no-root-`.env` gate failure aborts EARLIER than the tip's, on agent-org's render refusal, not on the `projects.ai-stack` row | findings section 4 | run `stack.py inventory --check` at `bdcc7f1` and at the tip, each in a clone with no root `.env`, and read both messages |


### Attempt 3 - the sentences THIS round introduces

Attempt 2 failed on T6/T8/T10 again, on a shape the guard's denylist did not list.
Rows 28-34 are what the repair adds; rows 10' and 19 are restated, because the wording
of both is what the failure refuted.

| # | claim | stated in | settled by |
|---|---|---|---|
| 10'' | The check refuses any target resolving to the repo root `.env` in every value shape compose accepts, **including a YAML alias**, with no exemption | script header, findings 3d, runbook 4b, `.githooks/README.md:27` | superseded by row 10''' below - the ROW NUMBERS it cited belong to attempt 3's T6a-iii table, which no longer exists |
| 19' | What may reach the resolver is an **ALLOWLIST** - `^[A-Za-z0-9_./\\~-]+$` after quotes and comments are stripped - and anything else is REFUSED and printed with the raw token | script header (`$script:PlainPathText`), findings 3d, runbook 4b, hook table | T6a-iii rows 20-27 (the allowlist boundary in the CURRENT table); read the script and confirm there is no remaining character DENYlist |
| 28 | An alias is followed, not refused: `&name <scalar>` looked up anywhere in the same file, and the looked-up text must itself be plain path text | script header, `Get-AnchorMap`, findings 3d | WITHDRAWN - see the attempt-4 table |
| 29 | An alias with no `&name <scalar>` in the file is refused, not ignored | findings 3d | WITHDRAWN - see the attempt-4 table |
| 30 | `env_file: *root_env` is a REAL grant docker honours | findings 3d, T6b | T6b's alias spellings - all three root names land on `ao-ot-1` |
| 31 | The `..` backstop could not have caught the alias, because the raw value has no `..` | findings 3d | read the backstop: it tests the RESOLVED text now, and reason about the alias case - the `..` lives in the anchor |
| 32 | The allowance is the compose file's own directory or a parent below the repo root - a SUBdirectory is refused too, on purpose | script header, findings 3d/3e | T6a-iii row 26 |
| 33 | `~` passes the allowlist and is then refused by the directory rule, which is what compose would look for too (it does not expand `~` in env_file) | findings 3d | T6a-iii row 27 |
| 34 | `.githooks/README.md:27` and runbook 4b's closing paragraph describe the allowlist, the alias lookup and the refuse-otherwise behaviour | those two files | T8 items 1 and 4 - and check that the regex they quote is character-for-character the one in the script |


### Attempt 4 - the sentences THIS round introduces

Attempt 3 failed on T6 and T10 row 28 (the alias lookup). Rows 35-42 are the claims
the repair adds; rows 28 and 29 are WITHDRAWN, because the behaviour they described -
following an alias to its anchor - has been deleted.

| # | claim | stated in | settled by |
|---|---|---|---|
| ~~28~~ | ~~An alias is followed to `&name <scalar>`~~ | WITHDRAWN | the behaviour is deleted; `grep -n "Get-AnchorMap" scripts/checks/check-env-file-scope.ps1` returns nothing |
| ~~29~~ | ~~An alias with no anchor is refused as such~~ | WITHDRAWN | it is now refused as policy, like every other alias |
| 10''' | The check refuses any target resolving to the repo root `.env` in every value SHAPE compose accepts, and refuses YAML INDIRECTION outright instead of resolving it | script header, findings 3e, runbook 4b, `.githooks/README.md:27` | T6a-i, T6a-ii and T6a-iii together |
| 35 | An `env_file` value must be a plain path literal; `*alias`, `&anchor`, `!tag`, `>`/`|` block scalar and `${VAR}` are refused BY POLICY with the token printed, never resolved | script header, `$script:YamlIndirection`, findings 3e, runbook 4b, `.githooks/README.md:27` | T6a-iii rows 1-18 - and read the messages: each must name the policy, not a symptom |
| 36 | The alias lookup is DELETED, not disabled | commit message, findings 3e | `grep -n "Get-AnchorMap\|anchors" scripts/checks/check-env-file-scope.ps1` returns nothing but prose |
| 37 | An alias to a LEGAL path is refused too, deliberately | script header, findings 3e, T6a-iii row 8 | T6a-iii row 8 goes RED; judge whether the trade is acceptable and say so |
| 38 | The duplicate-anchor case docker honours: YAML takes the LAST definition | findings 3e | T6b's duplicate-anchor render - all three root names |
| 39 | Shape parsing is unchanged and still passes a plain path literal in every shape | findings 3e | T6a-i and T6a-ii re-run unchanged (0 mismatches), T6a-iii rows 29-31 GREEN |
| 40 | A merge key needs no special handling because the `x-` block's own `env_file:` line is scanned where it is written | script header, findings 3e, hook table, runbook 4b | T6a-iii row 19 - check the violation's LINE NUMBER is the `x-` block's; plus T6b's merge render |
| 41 | Three rounds produced three defects of one kind - a guard re-implementing YAML is always one corner behind | findings 3e, commit message | read 3c, 3d and 3e in sequence and judge whether the generalisation is earned or retrofitted |
| 42 | `.githooks/README.md:27` and runbook 4b state the policy, and the allowlist regex they quote is byte-for-byte the script's | those two files | extract `$script:PlainPathText` from the script and grep both documents for that exact string - do not eyeball it |


### Attempt 5 - the sentences THIS round introduces

Attempt 4 failed on T6, T8 and T10 row 39. Rows 43-49 are the claims the repair adds;
row 39 is restated, because "shape parsing is unchanged and still passes a plain path
literal in every shape" was true of the shapes and false of the PLACEMENTS.

| # | claim | stated in | settled by |
|---|---|---|---|
| 39' | Every plain path literal is read wherever it sits under the key, and the four shapes still behave as before | script header, findings 3f | T6a-iv in full, plus T6a-i/ii/iii re-run unchanged |
| 43 | The value's EXTENT is decided by indentation before any shape is read: every line deeper than the key, ending at the first line at or below it | script header, `Scan-ComposeText`, findings 3f, runbook 4b, `.githooks/README.md:27` | T6a-iv rows 12, 13, 18 for the boundary; 1-11, 14-17 for what is inside |
| 44 | A blank line and a comment-only line do not end the extent | script header, findings 3f, runbook 4b | T6a-iv rows 9, 10, 11 |
| 45 | A line inside the extent that the reader does not recognise is treated as a VALUE, never as the end of the block | script header, findings 3f, runbook 4b, hook table | T6a-iv rows 1-3 (the next-line scalar reaches the verdict at all) |
| 46 | A bare `-` means the next deeper line is the item | script header, findings 3f | T6a-iv rows 4-7 |
| 47 | Tab indents compare monotonically with space indents (tab advances to the next multiple of 8) | script header, findings 3f | T6a-iv row 17 |
| 48 | Both spellings attempt 4 missed are real grants docker honours | findings 3f, T6a-iv | render rows 1-2 and 4-6 and count the root names on `ao-ot-1` |
| 49 | The check is deliberately stricter than docker on rows 8 and 14-15, and deliberately quieter on row 12 | findings 3f, T6a-iv | render all three and compare; then judge the direction, do not just confirm the measurement |
| 50 | `.githooks/README.md:27` and runbook 4b describe the extent rule, and both quote the allowlist regex AND the policy message byte-for-byte | those two files | extract `$script:PlainPathText` and `$script:IndirectionMessage` from the script and grep both documents for each exact string |


### Attempt 6 - the sentences THIS round introduces

Attempt 5 failed on T6 and T10 row 43. Rows 51-55 are the claims the repair adds; row 43
is restated because its wording is what excluded the shape.

| # | claim | stated in | settled by |
|---|---|---|---|
| 43' | The extent is every line indented deeper than the key, PLUS a line at exactly the key's indent whose body starts with `-`; anything else at or below the key's indent ends it | script header, `Scan-ComposeText`, findings 3g, runbook 4b, `.githooks/README.md:27` | T6a-v rows 1-6, and T6a-iv rows 12, 13, 18 for the other boundary |
| 51 | The carve-out is for the DASH only: a bare scalar at the key's indent is not the value, because YAML cannot read it as one and docker refuses the file | script header, findings 3g | T6a-v row 3 vs T6a-iv row 12, and render T6a-iv row 12 to see docker refuse it |
| 52 | `env_file:` with `- ../../.env` at the key's indent is a REAL grant docker honours | findings 3g, T6a-v | render T6a-v row 1 and count the root names on `ao-ot-1` |
| 53 | Attempt 4's script caught this shape, so it is a regression attempt 5 introduced rather than a hole that survived | findings 3g, commit message | the `dash-at-key-indent` row of the regression matrix: `R R R R G R` |
| 54 | Attempt 5's stated reasoning for the key-indent row ("a value at the key's indent is not the value") was an over-generalisation - true of scalars, false of sequences | findings 3g, T6a-iv note | read both rows together; a correct verdict resting on a wrong sentence is still a defect |
| 55 | The regression matrix asserts that no row is GREEN here where an earlier tip was RED, except two documented deliberate greens | `regression-matrix.py`, `regression-matrix.md`, findings 3g, T6c-0 | T6c-0: rerun the generator and compare its output to the committed table |


### Reopen (sl-ao-envfile2) - the sentences THIS round introduces

`sl-ao-envfile` was rejected at review as a misfit. Rows 56-61 are the claims the
repair adds; row 43 is restated again, because the extent sentence was incomplete in a
way that made the reject possible.

| # | claim | stated in | settled by |
|---|---|---|---|
| 43'' | A trailing comment on the `env_file:` key neither ends the value nor replaces it - the block below is still read | script header, `Scan-ComposeText`, findings 3h, runbook 4b, `.githooks/README.md:27` | T6a-vi rows 1-6 |
| 56 | An inline capture that yields no entries falls THROUGH to the block scan instead of skipping it | script header, findings 3h | read the `$entries.Count` guard; then T6a-vi rows 1-3 |
| 57 | All three comment-on-key shapes are grants docker honours | findings 3h, T6a-vi | render rows 1-3 and count the root names |
| 58 | It was green at every earlier tip including `bdcc7f1`, so it is inherited - but a misfit rather than a carry, because this item's four documents assert the opposite | findings 3h, the reopen anchor | the `T6a-vi` rows of the regression table (`G G G G G G | R`); then read the runbook and hook-table sentences and judge the misfit call yourself |
| 59 | This is the third round whose defect is an EMPTY READ treated as an ABSENT VALUE | findings 3h | read 3f, 3g and 3h together and judge whether the generalisation is earned |
| 60 | The attempt-6 tester's six rows and the reviewer's three are now in ROWS, and the tester's reconstruct faithfully | findings 3h, `regression-matrix.py`, T6a-vii/viii | compare the table's `T6a-vii` columns against the attempt-6 evidence file's own table, row by row |
| 61 | The generator's usage string names its own file, and `DELIBERATE_GREENS` documents that it is keyed by row name | `regression-matrix.py` | read the docstring and the comment above the map |


---

## Verdict

PASS requires every case above to pass on its own output, with T2's three
non-worker diff rows explicitly judged (not waved through), T5 re-measured
from source rather than read off this document, and T6a-i/T6a-ii/T6a-iii/T6b run
in full -
attempt 1 passed a T6 that planted one shape and the two it did not plant were
the defect; attempt 2 passed a 37-shape matrix and the shape it did not plant was
the defect; attempt 3 passed 52 and the shape it did not plant - one anchor name
defined twice - was the defect. The answer to that sequence is not a longer
table, it is the policy in T6a-iii: the check stopped interpreting YAML. So the
most useful thing you can do here is try to find a grant the check does not SEE.
Attempts 4 and 5 were both that: not a value judged wrongly, a value never read.
Three questions worth attacking, in order of how much they have cost: is there a
PLACEMENT under or beside the key that the extent rule misses; does any row the
regression matrix covers behave differently from the committed table; and is
there a value that is not a plain path literal and still reaches the resolver?
Plant it, render it to see whether docker honours it, and report both.
