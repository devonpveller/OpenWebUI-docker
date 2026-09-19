# Test plan — sl-colo-frontend

**Item:** `sl-colo-frontend` (stack-layers PLAN §2.7, Part L.1; DECISIONS D16/D17/D18)
**Branch:** `work/sl-colo-frontend` **Base:** `development` @ `2f5c451`
(attempt 1 was based on `3a373e2`; rebased onto `2f5c451` after sl-driver-parity
merged, per stack-layers D18)
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-colo-frontend.json`
**Findings sink:** `documentation/notes/stack-layers-sl-colo-frontend-findings.md`

**What the change is.** Four files move from the repo root into the plane:
`Dockerfile.openwebui-gpu`, `dockerfile.tailscale`, `entrypoint.sh` and
`.dockerignore` → `frontend/`. Both `build.context` values in
`frontend/docker-compose.yml` change from `..` to `.`. The `..`-rooted BIND
mounts (`../status-pipe`, `../system-prompts`, `../data/tailscale`,
`../backup`, `../backups`) do **not** change — those trees are not moving. No
Dockerfile content, no base image tag and no `entrypoint.sh` behaviour changes.
Every pointer elsewhere in the repo that named a root path is repointed.

**Kind of evidence.** This is a path move plus documentation/pointer work. There
is no RED→GREEN repro to offer and none is claimed. What it owes instead is
verification against the source of truth: renders compared to the previous
merge, every moved file read to its end, and every citation re-derived by
construct. Say so plainly in the evidence.

**What the tester needs.** A worktree of this branch, Docker Compose (v5.3 here)
for `config` renders only, Python 3 for the JSON normalisation, Git Bash for the
sweeps (prefix `MSYS_NO_PATHCONV=1` when a grep pattern starts with `/`), and —
for T9 only — a short-path scratch clone of their own. **No image is built and
no container is touched by any case in this plan.** If a case cannot be run in
the tester's environment, that is `-PlanInadequate`, not a scoped pass.

Shorthand used below: `$W` = the worktree root, `$B` = `2f5c451` (the base).

**Revision 2 (2026-09-19).** Attempt 1 scored **14/15**: the move itself passed
every case, and **T14 failed** — two findings entries stated false mechanisms
and one reported a fix that was not delivered. The artifact fix is in
`scripts/recovery/update-stack.bat` (`:174` reverted to a name-only repoint) and
in the findings note (F4/F5 rewritten, F13/F14 added). Three cases in this plan
were themselves defective and are rewritten: **T9** gave a recipe that could not
reach the state it named and passed vacuously, **T10(a)**'s second grep would
have failed a correct branch, and **T14** told the tester to check a variable
definition at a line that only *uses* the variable. **T8**'s `:38` citation was
off by one (`:39`). Each is marked where it appears.

T9 also picked up something the rebase introduced rather than attempt 1: since
`$B`, `check-project-configs.ps1` compares a GENERATED service inventory against
the committed one, and in a clone with no `.env` that comparison fails
**identically on `$B`**. The case now names that signature, gives a recipe that
reaches exit 0, and gives a differential fallback for a tester who would rather
not copy a secrets file — both measured.

---

## T1 - History is preserved for all three named files

    for f in frontend/entrypoint.sh frontend/Dockerfile.openwebui-gpu \
             frontend/dockerfile.tailscale frontend/.dockerignore; do
      echo -n "$f: "; git -C $W log --follow --oneline -- "$f" | wc -l
    done
    git -C $W diff $B...work/sl-colo-frontend -M --name-status | grep -E '^R'
    git -C $W show --name-status --format='%h %s' <the move commit> | grep -E '^R'

**PASS:** each `--follow` count is **greater than 1** — history walks through
the rename (the developer measured 18 / 8 / 4 / 4). The whole-branch
`--name-status` shows `R100 Dockerfile.openwebui-gpu →
frontend/Dockerfile.openwebui-gpu`, `R100 dockerfile.tailscale →
frontend/dockerfile.tailscale` and `R099 entrypoint.sh →
frontend/entrypoint.sh` — the three files the anchor names.

**FAIL:** any `--follow` count is 1 (git sees an unrelated new file, history
lost), or any of those three shows as a `D`+`A` pair rather than an `R` at git's
default 50% similarity.

**Expected, and not a failure:** `.dockerignore` shows as `D` + `A` in the
**whole-branch** diff, because that diff squashes the move (R100) with two
later comment rewrites. The file's only non-comment content is `*` and
`!entrypoint.sh`, so rewriting the comment drops similarity to **24%**
(`git diff $B...work/sl-colo-frontend -M10% --name-status` prints the figure)
and the squashed view cannot see the rename.
`git log --follow -- frontend/.dockerignore` walks through it anyway (count 4),
which is the property that matters, and
`git show <the move commit> --name-status` shows the `R100` directly. Check both
rather than scoring the squashed view. The anchor names only the other three
files for this criterion.

## T2 - The `stock` profile renders, and is identical to the base render

    cd $W
    docker compose -f frontend/docker-compose.yml --env-file .env.example \
      --profile stock config --format json > /tmp/new-stock.json
    git -C $W stash list   # expect empty; the tree must be clean for the next step
    git -C $W worktree add /tmp/base-$B $B     # or any clean checkout of $B
    cd /tmp/base-$B && docker compose -f frontend/docker-compose.yml \
      --env-file .env.example --profile stock config --format json > /tmp/base-stock.json

Then normalise and diff (the helper in T5 does all four at once; use it).

**PASS:** exit 0; the rendered service set is exactly
`['openwebui-backup', 'openwebui-stock']`; **zero** differing leaves against the
base render once the absolute worktree prefix is accounted for (the `stock`
service has no `build:` block at all, so there is nothing for this item to
change here).

**FAIL:** a non-zero exit, a different service set, or any differing leaf.

## T3 - The `gpu` profile renders, and differs ONLY in `build.context`

Same as T2 with `--profile gpu`.

**PASS:** exit 0; service set exactly `['openwebui', 'openwebui-backup']`;
exactly **one** differing leaf, `.services.openwebui.build.context`, whose base
value is the repo root and whose new value is that root + `\frontend`.
`.services.openwebui.build.dockerfile` is **unchanged** (`Dockerfile.openwebui-gpu`
— it is resolved against the context, so it stays a bare filename).

**FAIL:** any second differing leaf. In particular a changed `volumes` source
(the `../status-pipe`, `../system-prompts`, `../data/tailscale` binds must
resolve to the SAME absolute host paths as on the base — they were `..`-rooted
relative to the compose file, and the compose file did not move), a changed
`image`, `container_name`, `networks` or `deploy` block, or a changed
`build.dockerfile`.

## T4 - The `gpu`+`tailscale` profile renders, and differs ONLY in the two `build.context` values

Same as T2 with `--profile gpu --profile tailscale`.

**PASS:** exit 0; service set exactly
`['openwebui', 'openwebui-backup', 'tailscale', 'tailscale-backup']`; exactly
**two** differing leaves, `.services.openwebui.build.context` and
`.services.tailscale.build.context`, both root → root + `\frontend`.
`tailscale`'s `network_mode: service:openwebui`, its single
`TAILSCALE_AUTH_KEY` env, its `../data/tailscale` bind and `tailscale-backup`'s
`../data/tailscale` / `../backups/tailscale` / `../backup/generic-tar-backup.sh`
binds are all byte-identical to the base.

**FAIL:** a third differing leaf, or any change to a bind source.

## T5 - The bare render and the two refusals are unchanged

Bare (no profile active):

    docker compose -f frontend/docker-compose.yml --env-file .env.example \
      --profile "" config --format json

The two combinations that must refuse **by design**:

    docker compose -f frontend/docker-compose.yml --env-file .env.example \
      --profile stock --profile gpu config ; echo "exit=$?"
    docker compose -f frontend/docker-compose.yml --env-file .env.example \
      --profile tailscale config ; echo "exit=$?"

**PASS:** bare renders `['openwebui-backup']` alone with zero differing leaves
against the base. `stock`+`gpu` exits **1** with
`container name "openwebui" is already in use by service`. `tailscale` alone
exits **1** with `service "tailscale" depends on undefined service "openwebui"`.
Both messages are the same as on the base.

**FAIL:** either refusal now succeeds, exits with a different code, or fails with
a **different** message — in particular one naming a missing Dockerfile or
context, which would mean the move broke project load rather than profile
resolution.

Normalisation helper for T2–T5 (flatten both renders to leaves and compare):

    python - <<'EOF'
    import json,sys
    def flat(d,p=""):
        o={}
        if isinstance(d,dict):
            for k,v in d.items(): o.update(flat(v,f"{p}.{k}"))
        elif isinstance(d,list):
            for i,v in enumerate(d): o.update(flat(v,f"{p}[{i}]"))
        else: o[p]=d
        return o
    a=flat(json.load(open(sys.argv[1]))); b=flat(json.load(open(sys.argv[2])))
    for k in sorted(set(a)|set(b)):
        if a.get(k)!=b.get(k): print(k,"\n  base:",repr(a.get(k)),"\n  new :",repr(b.get(k)))
    EOF

## T6 - The rendered build paths exist on disk, and every COPY resolves inside the new context

From the `gpu`+`tailscale` render, for each service with a `build:` block, assert
`os.path.isdir(context)` and `os.path.isfile(os.path.join(context, dockerfile))`.
Then read **both** Dockerfiles end to end and check every `COPY`/`ADD`:

    grep -n -E '^\s*(COPY|ADD)' $W/frontend/dockerfile.tailscale
    grep -n -E '^\s*(COPY|ADD)' $W/frontend/Dockerfile.openwebui-gpu

**PASS:** both contexts resolve to `<worktree>/frontend` and both Dockerfiles
exist there. `dockerfile.tailscale` has exactly one context-reading instruction,
`COPY entrypoint.sh /entrypoint.sh` (`:7`), and `frontend/entrypoint.sh` exists —
so the COPY resolves **inside** the new context. `Dockerfile.openwebui-gpu` has
**zero** `COPY`/`ADD` (it only `RUN`s pip/sed/grep against paths inside the base
image), so it needs nothing from the context at all.

**FAIL:** a Dockerfile names any source path that is not under `frontend/` — the
failure mode the move is most likely to produce, and one no `config` render can
see, because compose never opens the Dockerfile.

## T7 - `frontend/entrypoint.sh` is LF **in the blob**, not merely in the working tree

    git -C $W show work/sl-colo-frontend:frontend/entrypoint.sh | \
      python -c "import sys;b=sys.stdin.buffer.read();print('CR:',b.count(b'\r'),'LF:',b.count(b'\n'))"
    git -C $W check-attr text eol -- frontend/entrypoint.sh frontend/dockerfile.tailscale

**PASS:** `CR: 0`. `check-attr` reports `eol: lf` for `frontend/entrypoint.sh`.
Score the **branch**, not the checkout: `core.autocrlf` makes the working-tree
copy CRLF and that is expected and harmless — the blob is what the build gets.

**FAIL:** any CR byte in the blob, or `eol: unspecified`. Either means a fresh
checkout can hand `dockerfile.tailscale` a CRLF script; the image's `dos2unix`
would mask it, which is why this is checked at the blob and not in the image.

## T8 - Every moved file was read to its end for location-relative paths

The failure class that cost the sibling colocation items an attempt each: a path
inside a moved file that is relative to the FILE's old location (`../`,
`./`, `dirname "$0"`, `$PSScriptRoot`, `import.meta.url`).

    MSYS_NO_PATHCONV=1 grep -n -E "dirname|\\\$0|BASH_SOURCE|PSScriptRoot|import\.meta|\.\./|\./" \
      $W/frontend/entrypoint.sh $W/frontend/Dockerfile.openwebui-gpu \
      $W/frontend/dockerfile.tailscale $W/frontend/.dockerignore

Then read all four files in full — the grep is a prompt, not the check.

**PASS:** two classes of hit and nothing else.
1. The `entrypoint.sh` comment at **`:39`** (grep the construct — `grep -n
   "STATE_DIR maps to" frontend/entrypoint.sh` — rather than trusting this
   number; `:38` is the first line of the same comment block, which is what
   attempt 1 of this plan wrongly cited). This branch changed it from
   `./data/tailscale` to `<repo>/data/tailscale` — which is what the compose
   bind `../data/tailscale` (now relative to `frontend/`) actually resolves to.
   Everything else in `entrypoint.sh` is container-absolute (`/tmp/...`,
   `/var/lib/tailscale`, `/usr/local/bin/tailscaled`) or an env-driven hostname.
2. `frontend/.dockerignore:13-14`, which names OTHER planes' context spellings
   (`../little-coder`, `./config/watcher`, `./gateway`, `../../mnemory`). These
   are narrative about other compose files, not paths this file resolves, and
   the sentence says so in as many words ("each RELATIVE TO ITS OWN compose file
   and not to this one"). Attempt 1 carried the root-era spelling here without
   that qualification; check the qualification is present, because a bare list
   of `./`-prefixed paths in a moved file is exactly the defect this case hunts.

**FAIL:** any surviving `./` or `../` path in a moved file that was relative to
the repo root and is not labelled as belonging to another file, or a changed
line in `entrypoint.sh` other than the `:39` comment (compare
`git diff $B...work/sl-colo-frontend -M -- entrypoint.sh frontend/entrypoint.sh`
— it must be exactly **1 insertion, 1 deletion**, that comment).

## T9 - `check-project-configs.ps1` passes from a WHOLE-DELTA staged state, in the tester's own clone

This check is **staged-aware**: it renders what the index holds. Running it in
the developer's worktree proves nothing about a reviewer's merge, and running it
against a partially staged tree can pass on a state that will never exist. Do it
in a short-path scratch clone of your own — not in `$W`, whose path is long
enough to trip Windows path limits:

**The staging step is the whole case, and the obvious recipe does not work.** In
a fresh clone every file is already COMMITTED, so `git add -- .` stages nothing
and the check prints `[configs] nothing staged - skip` and exits **0** — a
vacuous pass. Attempt 1 of this plan shipped exactly that recipe and the tester
had to defeat it. Put the branch's whole delta back into the index first:

    git -c core.longpaths=true clone --no-local --branch work/sl-colo-frontend $W C:/t/scf
    cd C:/t/scf
    git reset --soft $B                 # the branch delta becomes STAGED, worktree untouched
    git add -A -- . ':!OB1'             # picks up the .dockerignore delete/add pair too
    git diff --cached --name-only | wc -l
    cp $W/.env C:/t/scf/.env            # see "the .env problem" below
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-project-configs.ps1 ; echo "EXIT=$?"
    rm -f C:/t/scf/.env                 # do not leave a copy of the secrets lying around

**The `.env` problem, measured on `$B` as well as here.** `.env` is gitignored,
so a clone has none, and since sl-driver-parity (`$B`) this check also compares
the GENERATED service inventory against the committed
`scripts/lib/stack-services.json`. Without `.env` the anchor project renders as
`file: null` instead of `docker-compose.yml`, the generated inventory differs,
and the check prints `[FAIL] projects.ai-stack: committed … != generated …` plus
`INVENTORY DRIFT` and exits **1** — on a clone of `$B` exactly as on a clone of
this branch. Two ways to handle it; **say in the evidence which you used**:
- **(a) preferred** — copy `.env` into the clone as above, run, then delete it.
  Measured: `EXIT=0`, no drift, no FAIL line.
- **(b) if you will not copy a secrets file** — run the check on a clone of
  `$B` staged the same way and `diff` the two outputs. Measured: the only
  difference is one informational line (`2 staged .json file(s) are
  strict-valid`, which `$B`'s own delta produces and this branch's does not).
  Identical `INVENTORY DRIFT` on both = environmental, not branch-caused.

**PASS:** all four of these, not just the exit code.
1. The staged count equals `git -C $W diff --name-only $B..work/sl-colo-frontend | wc -l`
   (**18** at the time of writing — re-derive it, do not trust the number). A
   count of 0 means the state was never reached and nothing below is evidence.
2. The output contains `[configs] all 8 compose projects render clean` — an
   explicit **project count**, not merely a silent success.
3. It contains `[configs] stack-services.json inventory matches the compose
   configs` with `frontend:4/4` in the rows line (the frontend render target
   passes `--profile gpu --profile tailscale`).
4. Route (a): `EXIT=0`. Route (b): the branch output and the `$B` output differ
   in nothing but that one informational line.

**FAIL:** any of the following.
- The output contains **`nothing staged`** or **`skip`** for the configs gate.
  That is the vacuous path: exit 0 while rendering no compose project at all,
  and it is scored as a FAIL of this case, not a pass. (Attempt 1's recipe
  produced exactly this.)
- Fewer than 8 projects render clean, or `frontend` is not `4/4`.
- A frontend render error naming a missing build context or Dockerfile, or a
  coverage mismatch.
- On route (b), any output difference beyond that one line — in particular an
  inventory FAIL naming a project on the branch but not on `$B`.
- You ran it in `$W` instead of your own clone — that is not the state being
  tested, and `$W`'s path is long enough to trip Windows path limits anyway.

`NOT VERIFIED` lines for `agent-org` (no render target) and for `open-brain`
(needs `OB1/docker/.env`, absent in a clone with an empty submodule) are
expected and are not failures — they appear identically on a clone of `$B`.

## T10 - Tree-wide citation sweep, BY CONSTRUCT, both path forms

Two things go stale on a move: the **path** in a citation, and the **line
number** in any citation into a file whose line count changed. Check both, and
include bare-basename forms (`entrypoint.sh:74`) and backslash forms
(`..\entrypoint.sh`) — a path-anchored sweep misses them.

(a) No live pointer names a root path:

    cd $W && MSYS_NO_PATHCONV=1 git grep -n -I \
      -e "Dockerfile\.openwebui-gpu" -e "dockerfile\.tailscale" -e "entrypoint\.sh" \
      -- . ':!OB1' ':!documentation/archive' ':!scripts/archive' \
         ':!documentation/notes' ':!documentation/evidence' ':!CLEANUP-PLAN.md'
    MSYS_NO_PATHCONV=1 git grep -n -I -E \
      "([.]{2}|[\\\\/])(Dockerfile\.openwebui-gpu|dockerfile\.tailscale|entrypoint\.sh)" \
      -- . ':!OB1' ':!documentation/archive' ':!scripts/archive' \
         ':!documentation/notes' ':!documentation/evidence' ':!CLEANUP-PLAN.md'

(b) Re-derive every line citation into the two moved/edited files by reading the
construct at that line, not by trusting the number:

    awk 'NR==54||NR==66||NR==70||NR==72||NR==74||NR==76||NR==93||NR==97||NR==99||NR==101||NR==103||NR==105 {printf "%3d: %s\n",NR,$0}' $W/frontend/entrypoint.sh
    awk 'NR==115||NR==153||NR==158||NR==160||NR==165||NR==246||NR==257||NR==263||NR==290||NR==291||NR==302||NR==311||NR==313||NR==314||NR==317||NR==318||NR==319||NR==320||NR==321||NR==323||NR==332||NR==333||NR==335||NR==336||NR==473||NR==481||NR==484 {printf "%3d: %s\n",NR,$0}' $W/frontend/docker-compose.yml

These are the lines `stack.manifest.toml:142-179` cites.

**PASS for (a):** every remaining hit falls in exactly one of three classes, and
the tester should classify each rather than counting:
1. **inside `frontend/`** — a sibling reference that is correct at the new depth
   (`frontend/.dockerignore:4,5,19`, `frontend/docker-compose.yml:126,160,281,288,293,301,306,332`,
   `frontend/dockerfile.tailscale:7,10,13,16`, `frontend/entrypoint.sh:2`);
2. **a repointed path** naming `frontend/...` or `frontend\...`;
3. **narrative or log text naming the file, not a path** — `workspace-stacks.md:23`
   (history of the 2026-08 rewrite), `portal/config/caddy/Caddyfile:229`,
   `scripts/checks/stack-watchdog.ps1:320,424,458,463`,
   `scripts/recovery/emergency-recovery.ps1:632`, `scripts/stack/test_stack.py:135`,
   and `.gitattributes:5`, which is an **unanchored git pattern**, not a path
   (T11 proves it still matches).
**The second grep is scored by CLASSIFICATION, not by emptiness.** Attempt 1 of
this plan required it to "return nothing outside `frontend/`", which is wrong:
the regex's `[\\/]` alternative matches the **repointed** form
`frontend/entrypoint.sh` itself, so a correct branch produces ~35 hits and a
tester scoring the wording literally would fail a sound artifact. Classify every
hit instead. It passes when each one is (i) the repointed `frontend/` or
`frontend\` form, (ii) inside `frontend/`, or (iii) another file entirely —
`portal/config/portal-cron/{Dockerfile,crontab}` (the portal's OWN
`entrypoint.sh`, in the portal's own context) and
`inference/compose/gateway.yml` (`docker/prod_entrypoint.sh`, a different file
inside the LiteLLM image). Its job is to catch a `..\Dockerfile.openwebui-gpu`
or `../entrypoint.sh` still pointing at the root, and there must be none.

**FAIL for (a):** any hit, in either grep, outside those classes — a runbook
command, a script path, a markdown link or a manifest citation still naming a
root path.

**PASS for (b):** every cited line holds the construct the citing text claims
(`:54` is `LLAMA_CPP_HOST=`, `:93` is the `llama-cpp|` route row, `:105` the
`mattermost|` row, compose `:158-165` is the `gpu` build block, `:290` is
`network_mode: service:openwebui`, and so on). Note that both files are
**line-neutral** on this branch — the compose edit is `context: ..` →
`context: .` and the entrypoint edit is one comment — so the numbers should not
have moved. Verify anyway; that assumption is exactly what went wrong on the
sibling items.

**FAIL for (b):** any citation whose line no longer holds the named construct.

**Also check the file this branch DID lengthen.** `workspace-stacks.md` gains 8
lines at `:115-122`. Sweep for citations into it by line
(`git grep -n "workspace-stacks\.md:[0-9]"`); the only hits must be in
`documentation/evidence/` (the exempt class). A live citation into that file by
line anywhere else is a FAIL.

## T11 - `.gitattributes` still covers the moved shell script (and was not anchored)

    git -C $W check-attr text eol -- frontend/entrypoint.sh
    grep -n "eol=lf" $W/.gitattributes

**PASS:** `eol: lf`. `.gitattributes:4` (`*.sh text eol=lf`) and `:5`
(`entrypoint.sh text eol=lf`) contain **no slash**, so git matches them at any
depth and the move could not strand them — which is why this branch changes
`.gitattributes` not at all. Confirm that: the file must be absent from
`git diff $B...work/sl-colo-frontend --name-only`.

**FAIL:** `eol: unspecified`, or a `.gitattributes` edit in the diff that was not
needed (churn), or a path-anchored rule (`/entrypoint.sh`) left in place.

## T12 - The live hazard: nothing running is disturbed, and no recreate is needed at landing

The claim this branch makes, to be checked rather than taken:

    docker inspect openwebui --format '{{.Config.Image}}{{range .Mounts}} {{.Type}}:{{.Source}}->{{.Destination}}{{end}}'
    docker inspect tailscale --format '{{.Config.Image}}{{range .Mounts}} {{.Type}}:{{.Source}}->{{.Destination}}{{end}}'

**PASS:** both containers run from pinned `:local` images (`openwebui:local`,
`tailscale:local`), and **neither has a bind whose source is any of the four
moved files**. `openwebui` binds the `frontend_openwebui-data` volume plus
`data/tailscale`, `status-pipe`, `system-prompts` (and, on the currently running
container, a stale `config` bind — see findings F12; it predates the merge at
`$B` and is not this item's). `tailscale` binds only `data/tailscale` and
`/dev/net/tun`. `entrypoint.sh` reaches the tailscale container by **COPY at
build time** (`frontend/dockerfile.tailscale:7`), not by mount — so a path move
in the repo cannot reach a running container. **Conclusion to record: landing
this branch needs NO recreate and NO restart of `openwebui` or `tailscale`; the
next deliberate `docker compose ... build` picks up the new context and paths.**

**FAIL:** either container shows a bind whose source is one of the moved files,
or is running from something other than its `:local` image — either would mean
landing the branch changes a running container and the conclusion above is
false.

**This case is read-only.** Do not restart, recreate or build anything. If the
containers are not running, record that and score the case on the compose
render's `image:` + `volumes:` instead (the same conclusion follows from
`frontend/docker-compose.yml:165,283` plus the bind list) — say in the evidence
which of the two you did.

## T13 - Diff what was REMOVED, not only what is present

    git -C $W diff $B...work/sl-colo-frontend -M --stat
    git -C $W diff $B...work/sl-colo-frontend -M -- .dockerignore frontend/.dockerignore
    git -C $W diff $B...work/sl-colo-frontend -M | grep -E '^-' | grep -v '^---'

**PASS:** every removed line is accounted for as either (i) the left side of a
repoint whose right side is present, or (ii) a sentence in the old root
`.dockerignore` whose replacement in `frontend/.dockerignore` says the same
thing about the new context. Specifically, the old file's "this **root**
`.dockerignore` does NOT affect" note and its "Root (context: .)" heading are
replaced, not dropped; the two functional lines `*` and `!entrypoint.sh` are
**unchanged**.

**FAIL:** a true statement removed with no replacement and no findings entry, or
a change to `*` / `!entrypoint.sh` (which would change what is shipped to the
daemon — out of scope for a move).

## T14 - The findings note is held to the artifact's standard

Read `documentation/notes/stack-layers-sl-colo-frontend-findings.md` and check
each entry against the file it describes — comments are not evidence, and a
right answer for a wrong reason fails.

**This case FAILED on attempt 1 and is the reason there is a second.** F4 and F5
both rested on "`%SCRIPT_DIR%` is `scripts/recovery/`", and the plan told the
tester to "check `:93` sets it" — `:93` is a **use** of the variable, not a
definition. `SCRIPT_DIR` is assigned nowhere in that file. Off the back of that
false premise, F4 asserted a fix that was not delivered. The general rule this
bought, and the first thing to do in this case:

> **Locate the DEFINITION of every variable a finding's reasoning depends on
> before scoring that finding, and say where it is.** A line that *reads*
> `%VAR%` / `$VAR` is not a line that sets it. Grep for the assignment form
> (`set "VAR=`, `VAR=`, `$VAR =`, `%~dp0`) and, if there is none, say so —
> an undefined variable in a shell or batch script expands to **nothing**
> silently, so every claim built on its value is wrong in a way that reading
> the surrounding prose will never reveal.

**PASS:** every entry carries one of the three provenance labels; every
`file:line` in it resolves to the construct claimed; every claim about what a
script DOES is verified by reading the whole function body, not its comment;
and every variable a claim depends on has a definition the tester located.
Specifically worth re-deriving, each by running the command rather than reading
the note:
- **F13** — `grep -c "SCRIPT_DIR"` = 27 with **zero** assignments and no
  `%~dp0`, on this branch AND on `$B`; then, in `cmd` with `@echo off` +
  `setlocal enabledelayedexpansion`, that `%SCRIPT_DIR%` is empty, that
  `cd /d "%SCRIPT_DIR%\..\.."` lands on `D:\`, and that `cd /d "%SCRIPT_DIR%"`
  errors with errorlevel 1 and leaves the cwd alone. Also check F13's *repair*
  claim (`set "SCRIPT_DIR=%~dp0"` after `:6` makes both spellings work) —
  it is `[measured]`, so measure it.
- **F4** — that `:174` is a **name-only** repoint and claims no fix:
  `git diff $B..work/sl-colo-frontend -- scripts/recovery/update-stack.bat`
  must show `'..\Dockerfile.openwebui-gpu'` → `'..\frontend\Dockerfile.openwebui-gpu'`
  and no other change of *shape* anywhere in the file. A reintroduced
  `%SCRIPT_DIR%\..\..\` form is a FAIL.
- **F5** — that the root compose project has zero services
  (`grep -c "^services:" docker-compose.yml` = 0) **and** that the calls fail
  earlier than that, at `D:\`, with `no configuration file provided`
  (`cd /d D:\ && docker compose ps`). The entry claims both; check both.
- **F6** — read `rebuild_tailscale.py`'s `find_project_root` and `main` to their
  ends, and confirm `grep -iE "dockerfile|entrypoint"` over the file is empty
  (that is why it needed no edit).
- **F2** — that no `eol=lf` rule is path-anchored, by `check-attr` at BOTH the
  old and the new path.
- **F14** — that `README.md` and `documentation/runbooks/UPDATE-MANAGEMENT.md`
  really contain no reference to any moved file, on `$B` as well as here.

**FAIL:** an entry stated more confidently than its label supports, an entry
whose stated *reason* is wrong even though its headline is right, an entry that
reports a change as delivered when it is not, or a `file:line` that does not
resolve. A false claim in the sink is worse than one in the deliverable — the
next item reads this.

## T15 - Lint and hooks, on the branch as committed

    cd $W && ruff check .
    git -C $W log --format='%H %s' $B..work/sl-colo-frontend

**PASS:** `All checks passed!`. Every commit on the branch was made **without**
`--no-verify` (the pre-commit hooks — secret guard, LF check, gateway-routing
check, compose/ps1 structural check, doc-placement — ran and passed; re-run them
by staging the whole delta in your T9 clone and committing a no-op if you want
independent proof). No image was built, rebuilt or retagged by this branch and
no `:local` tag moved.

**FAIL:** ruff findings; a commit that bypassed hooks; any evidence of a build,
a retag or a container restart.

---

## Cases NOT in this plan, and why

- **An actual `docker build` of either image.** Explicitly out of scope: the
  images are pinned `:local` and rebuilt deliberately only, and a CUDA torch
  reinstall is a multi-minute network build. T6 checks the thing a build would
  check about a context move — that every `COPY` source lives inside the new
  context — by reading the Dockerfiles, which is also the only way to catch it
  before a build runs.
- **Anything that starts, stops or recreates a container.** T12 is `docker
  inspect` only.
- **Fixing `update-stack.bat`, `rebuild_tailscale.py` or `dev-helper.ps1`.**
  All three are inert against the zero-service root project for reasons that
  predate this item; findings F4–F7 record them. This branch repoints the file
  names they contain and nothing else.
