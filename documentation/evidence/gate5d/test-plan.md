# gate5d test plan - check-ob1-integration-images.ps1 (pre-commit gate 5d)

Item: `gate5d`. Branch: `work/gate5d`. Author of this plan: the developer (wt-gate5d).
The tester executes it in THEIR OWN worktree (`scripts/agent-harness/new-worktree.ps1 -Id <id>`),
which has its own OB1 clone at pin `a07103b`. Every command below is run from the tester's
worktree root unless stated. `<WT>` = that worktree's absolute path.

What is under test: one new script, two hook-surface edits.

- `scripts/checks/check-ob1-integration-images.ps1`
- `.githooks/pre-commit` (5d block inserted after the 5c block, before `# --- 6. ATTESTATION`)
- `.githooks/README.md` (row `5d` after row `5c`)

Read the anchor first: `powershell -NoProfile -File scripts/agent-harness/queue.ps1 -Show -Id gate5d`.
Each `T` below names the acceptance criterion it exercises. A case PASSES only if the
observed output matches the "Expect" block in substance (exit code, the named files, the
named line). Paste actual output into the result, not a paraphrase.

Two facts the tester must know before T7:

1. `git config --get core.hooksPath` is an ABSOLUTE path to the operator's checkout
   (`D:\Open WebUI\ai-stack\.githooks`). A plain `git commit` in a worktree therefore runs the
   OPERATOR's hook, which does not have 5d until this item merges. Every real-hook case below
   passes `-c core.hooksPath="<WT>\.githooks"` so the worktree's own edited hook runs. This is
   the same mechanism the README's "Which hook gated this tree?" section describes; the
   attestation line will carry the hash of the worktree's hook file.
2. `.githooks/commit-msg` rejects, on a gitlink-bump commit, any 7-40 hex token containing a
   digit that is not an OB1 commit. Keep scratch commit messages free of SHAs other than real
   OB1 ones.

## T0 - preconditions

```
git -C "<WT>" rev-parse --short HEAD:OB1          # expect a07103b
git -C "<WT>/OB1" rev-parse --short HEAD          # expect a07103b
git -C "<WT>/OB1" status --short | Measure-Object # expect Count 0
git -C "<WT>/OB1" cat-file -e 48c0363^{commit}; echo $LASTEXITCODE   # expect 0
docker version --format '{{.Server.Version}}'     # expect a version string
deno --version                                    # expect deno 2.x (5c needs it in T7)
docker image ls denoland/deno:2.3.3 --format '{{.ID}}'   # expect an id (base image cached)
```

If `48c0363` is missing: `git -C "<WT>/OB1" fetch origin` (the SHA is on the OB1 remote).

## T1 - RED against the real incident pin (acceptance check #1)

```
powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin 48c0363 -NewPin a07103b; echo $LASTEXITCODE
```

Expect: exit code `1`. Output contains ALL of:
- `touches image-bearing integration(s): research-curator, research-service`
- `research-service/Dockerfile copies by glob/directory` ... `static half skipped`
- a line naming `integrations/research-curator/Dockerfile`, `research-curator/index.ts:39`,
  `'pool.ts'`, and the Dockerfile's COPY list
  (`deno.json, index.ts, claims.ts, backfill-thread-embeddings.ts, consolidate-threads.ts`)
- `FIX in OB1: add 'COPY pool.ts ./'`
- `FAIL: 1 relative import(s) reachable from an entrypoint are not in their image`
- `No docker build was attempted`

Also expect: `docker image ls --format '{{.Repository}}' | Select-String '^ob1-gate/'` prints
nothing (no image was built). Wall time under ~5 s.

Developer's run (2026-09-06): exit 1, 2180 ms, output matched every line above.

## T2 - static half: glob COPY skips static, name-list COPY is walked (acceptance #2)

T1 already shows both shapes in one run: research-service (`COPY *.ts ./`) prints the
"copies by glob/directory ... static half skipped" line; research-curator (name list) is
walked and refused. Two more shapes:

```
# A DIRECTORY COPY (entity-extraction-worker: `COPY _shared ./_shared` + `COPY index.ts ./`)
# must NOT false-positive: files under the copied directory count as covered.
# 467d945~1..467d945 touches only integrations/entity-extraction-worker/index.ts.
git -C OB1 diff --name-only 467d945~1 467d945      # expect exactly that one path
git -C OB1 show 467d945:integrations/entity-extraction-worker/Dockerfile | grep COPY   # expect the _shared line
powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin 467d945~1 -NewPin 467d945; echo $LASTEXITCODE
```

Expect: exit `0`; output contains, in this order:
- `OB1 5d511cb..467d945 touches image-bearing integration(s): entity-extraction-worker`
- `integrations/entity-extraction-worker/Dockerfile covers all 3 file(s) reachable from index.ts by relative import.`
  (index.ts, _shared/helpers.ts, _shared/config.ts - the directory COPY covers the two beneath it)
- `build: ob1-gate/entity-extraction-worker:467d945 built and 'deno check index.ts' resolved inside it`
- `OK - 1 integration image(s) build and resolve at OB1 467d945 (entity-extraction-worker).`
- NOT acceptable: any line naming `_shared/helpers.ts` or `_shared/config.ts` as missing.

Developer's run: exit 0, 11 s (7 s build), tag removed.

Also confirm by-pin mode refuses a non-commit: `-OldPin 4b825dc642cb6eb9a060e54bf8d69288fbee4904`
(git's empty tree) with any `-NewPin` must print `-OldPin '...' is not a commit in the OB1
clone` and exit 1. The empty-tree comparison exists only in hook mode, for a repo whose HEAD
has no OB1 gitlink yet.

## T3 - build half GREEN and the throwaway tag (acceptance #3, first half)

Create a scratch OB1 commit that fixes the curator Dockerfile WITHOUT touching the OB1
working tree (plumbing with a temporary index). In Git Bash, from `<WT>/OB1`:

```
export GIT_INDEX_FILE="$PWD/../.gate5d-tmp-index"
git read-tree a07103b
git show a07103b:integrations/research-curator/Dockerfile | sed 's#^COPY claims.ts ./#COPY claims.ts ./\nCOPY pool.ts ./#' > /tmp/Dockerfile.fixed
blob=$(git hash-object -w /tmp/Dockerfile.fixed)
git update-index --cacheinfo 100644,$blob,integrations/research-curator/Dockerfile
tree=$(git write-tree); fix=$(git commit-tree $tree -p a07103b -m "scratch(gate5d): curator Dockerfile copies pool.ts - local only, never pushed")
git branch -f scratch/gate5d-curator-fixed $fix
unset GIT_INDEX_FILE; rm -f ../.gate5d-tmp-index
git rev-parse --short HEAD        # still a07103b - the working tree was not touched
git status --short                # empty
```

Then, from the worktree root:

```
powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin 48c0363 -NewPin scratch/gate5d-curator-fixed; echo $LASTEXITCODE
docker image ls --format '{{.Repository}}:{{.Tag}}' | Select-String '^ob1-gate/'
```

Expect: exit `0`; output contains
- `integrations/research-curator/Dockerfile covers all 3 file(s) reachable from index.ts`
- `build: ob1-gate/research-curator:<sha7> built and 'deno check index.ts' resolved inside it`
- `build: ob1-gate/research-service:<sha7> built and 'deno check index.ts' resolved inside it`
- `OK - 2 integration image(s) build and resolve at OB1 <sha7> (research-curator, research-service). Image builds + module graph only - no service was started, no env or compose wiring was checked.`
- the `docker image ls` afterwards prints NOTHING (tags removed).
- `<sha7>` is the short SHA of your scratch commit, never `local`.

Developer's run: exit 0, 14 s (4 s + 6 s builds), both tags gone afterwards.

## T4 - MUTATION: fixed Dockerfile, broken import - must fail the BUILD half (acceptance #3, second half)

The static half must stay green (every relative import is COPYed) and `deno check` inside
the image must refuse. From `<WT>/OB1` in Git Bash, starting from the T3 index recipe:

```
export GIT_INDEX_FILE="$PWD/../.gate5d-tmp-index"
git read-tree scratch/gate5d-curator-fixed
git show a07103b:integrations/research-curator/index.ts | sed 's#import { ResilientPool } from "./pool.ts";#import { ResilientPoolX } from "./pool.ts";#' > /tmp/index.mut
grep -n ResilientPoolX /tmp/index.mut           # expect line 39
blob=$(git hash-object -w /tmp/index.mut)
git update-index --cacheinfo 100644,$blob,integrations/research-curator/index.ts
tree=$(git write-tree); mut=$(git commit-tree $tree -p a07103b -m "scratch(gate5d): mutation - fixed Dockerfile, broken import - local only, never pushed")
git branch -f scratch/gate5d-mutation $mut
unset GIT_INDEX_FILE; rm -f ../.gate5d-tmp-index
```

```
powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin a07103b -NewPin scratch/gate5d-mutation; echo $LASTEXITCODE
docker image ls --format '{{.Repository}}:{{.Tag}}' | Select-String '^ob1-gate/'
```

Expect: exit `1`; output contains
- `touches image-bearing integration(s): research-curator` (only - the diff is curator-only)
- `integrations/research-curator/Dockerfile covers all 3 file(s)` (static GREEN)
- `--- last 20 of N log line(s) ---` followed by deno diagnostics that include
  `Module '"./pool.ts"' has no exported member 'ResilientPoolX'` (or the TS2305/TS2552
  wording your deno version prints) and `at file:///app/index.ts:39`
- `FAIL: 'deno check index.ts' inside ob1-gate/research-curator:<sha7> FAILED (exit 1): the image builds but its module graph does not resolve`
- `docker image ls` afterwards prints nothing.

Developer's run: exit 1, 6.5 s; the tail quoted `at file:///app/index.ts:39:10`,
`TS2552 ... Did you mean 'ResilientPoolX'?`, `Found 3 errors.`, `error: Type checking failed.`

## T5 - REFUSES when Docker is unreachable (acceptance #4)

Use a pin pair that passes the static half so the run reaches the docker probe.
`a07103b~1..a07103b` touches only research-service (glob COPY).

```
$env:DOCKER_HOST = 'tcp://127.0.0.1:9'
powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin a07103b~1 -NewPin a07103b; echo $LASTEXITCODE
Remove-Item Env:DOCKER_HOST
```

Expect: exit `1`; a single FAIL sentence beginning `Docker is not reachable (docker version
failed: ...` and containing `This gate REFUSES rather than warning and passing`. No image
is built. NOT acceptable: any exit 0, any "WARNING ... skipping" wording.

Optional stronger form (only if the tester holds the relevant plane leases and the operator
agrees): stop Docker Desktop and run the same command without DOCKER_HOST.

Developer's run: exit 1, the sentence quoted the connectex refusal for 127.0.0.1:9.

## T6 - the two skips, each under one second, one line each (acceptance #5)

```
git diff --cached --name-only            # must be EMPTY (nothing staged) for the first case
$sw=[Diagnostics.Stopwatch]::StartNew(); powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1; "EXIT=$LASTEXITCODE $([int]$sw.Elapsed.TotalMilliseconds)ms"
```

Expect: `[check-ob1-integration-images] no OB1 gitlink staged - skipped.` then `EXIT=0`,
elapsed under 1000 ms.

```
# recipes-only bump: df7d543~1..df7d543 changes two files under recipes/ and nothing under integrations/
git -C OB1 diff --name-only df7d543~1 df7d543          # expect only recipes/... paths
$sw=[Diagnostics.Stopwatch]::StartNew(); powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin df7d543~1 -NewPin df7d543; "EXIT=$LASTEXITCODE $([int]$sw.Elapsed.TotalMilliseconds)ms"
```

Expect: one line `OB1 b69cdbf..df7d543 changes 0 path(s) under integrations/, none in a
directory that carries a Dockerfile at df7d543 - no image to build, skipped.` then `EXIT=0`,
elapsed under 1000 ms (developer measured 759 ms; the machine was otherwise idle - if the
tester's host is under load, record the figure and rerun once; the criterion is the idle
figure, and two runs over 1000 ms on an idle host FAILS this case).

Same-pin form, also a skip: `-OldPin 48c0363 -NewPin 48c0363` -> `changes 0 path(s) ...
skipped`, exit 0.

## T7 - wiring: the hook file and the README (acceptance #6)

```
grep -n 'check-ob1-integration-images' .githooks/pre-commit .githooks/README.md
grep -n '^# --- 5c\|^# --- 5d\|^# --- 6\.' .githooks/pre-commit
cat .githooks/pre-merge-commit | grep -n 'exec'
grep -c $'\r' .githooks/pre-commit
```

Expect: one hit in pre-commit (the `powershell.exe ... -File './scripts/checks/check-ob1-integration-images.ps1'`
line) and one in README.md (row `| 5d |`); the section markers appear in the order 5c, 5d, 6;
pre-merge-commit's `exec "$(dirname "$0")/pre-commit"` is present (so a clean merge that
bumps the gitlink runs 5d); zero CR bytes in pre-commit. Also `sed -n 130,140p .githooks/pre-commit`
shows the identical five-line shape as 5b/5c: the `powershell.exe` line, `if [ $? -ne 0 ]; then`,
an `echo "Pre-commit validation failed (...)!"`, `exit 1`, `fi`. Confirm the script header's
"A MERGE COMMIT GETS THIS TOO" paragraph says the same.

## T8 - the real hook run: RED then GREEN through `git commit` (acceptance #7)

This is the case the by-pin mode cannot stand in for: under a hook git exports GIT_DIR and
GIT_INDEX_FILE, which override `git -C OB1`; only a real commit exercises Git-InOB1.

Setup: HEAD:OB1 is already a07103b, so "a bump TO a07103b" needs a parent whose pin is older.
Make one with plumbing (no hook runs on `commit-tree`), then move the branch onto it with
`reset --soft` so the index (still pinning a07103b) becomes a staged bump 48c0363 -> a07103b.
From the worktree root in Git Bash:

```
TIP=$(git rev-parse HEAD); echo "$TIP" > ../gate5d-tip.txt      # the branch tip to restore
export GIT_INDEX_FILE="$PWD/.gate5d-parent-index"
git read-tree HEAD
git update-index --cacheinfo 160000,$(git -C OB1 rev-parse 48c0363),OB1
tree=$(git write-tree); parent=$(git commit-tree $tree -p HEAD -m "scratch(gate5d): parent pinning OB1 48c0363 - never pushed")
unset GIT_INDEX_FILE; rm -f .gate5d-parent-index
git reset --soft $parent
git diff --cached --name-only            # expect: OB1
git ls-files -s OB1                      # expect: 160000 a07103b71dc... 0  OB1
git -C OB1 rev-parse --short HEAD        # expect: a07103b (disk == staged)
```

RED:

```
git -c core.hooksPath="<WT>/.githooks" commit -m "scratch(gate5d): bump OB1 to a07103b - RED, never pushed"; echo "EXIT=$?"
```

Expect: the chain runs 1..5c first (5b runs `node --test` over OB1/recipes at a07103b and
5c runs `deno check` on daily-digest - both should pass, as a07103b came through them when
the line pinned it; if 5b or 5c refuse, STOP and record it - that is a finding about the
tester's environment or the pin, not about 5d). Then 5d prints the same refusal as T1
(`research-curator/index.ts:39 imports 'pool.ts'` ... `FAIL: 1 relative import(s) ...`),
the hook prints `Pre-commit validation failed (an OB1 integration image does not build or
resolve for the staged gitlink)!`, and `git commit` exits non-zero. `git rev-parse HEAD`
still equals `$parent` (no commit was made).

GREEN (needs the T3 scratch branch in THIS worktree's OB1 clone):

```
git -C OB1 checkout -q scratch/gate5d-curator-fixed
git add OB1
git ls-files -s OB1                      # expect the scratch SHA
git -c core.hooksPath="<WT>/.githooks" commit -m "scratch(gate5d): bump OB1 to the curator-fixed scratch commit - GREEN, never pushed"; echo "EXIT=$?"
```

Expect: 5b, 5c pass; 5d prints `touches image-bearing integration(s): research-curator,
research-service`, `covers all 3 file(s)`, two `build: ob1-gate/...` lines, and
`OK - 2 integration image(s) build and resolve`; then the attestation line; `EXIT=0` and
`git log -1 --format=%s` shows the GREEN message. (commit-msg accepts the message: it has
no SHA-shaped token.)

RESTORE - mandatory, in this order:

```
git reset -q --hard $(cat ../gate5d-tip.txt)      # drops the scratch parent and the GREEN commit
git -C OB1 checkout -q a07103b
git -C OB1 branch -D scratch/gate5d-curator-fixed scratch/gate5d-mutation
git status --short                              # expect empty
git rev-parse --short HEAD:OB1; git -C OB1 rev-parse --short HEAD   # both a07103b
docker image ls --format '{{.Repository}}:{{.Tag}}' | grep '^ob1-gate/'    # expect nothing
rm -f ../gate5d-tip.txt
```

The attestation log (`<git-common-dir>/hook-attest.log`) will carry two extra lines for
trees that no longer exist; that is expected and harmless (the checker only reads it back
for commits that exist).

## T9 - the header says what a green does NOT prove (acceptance #8)

```
sed -n 1,120p scripts/checks/check-ob1-integration-images.ps1 | grep -n 'DOES NOT PROVE\|real database\|Env wiring\|Compose changes\|MERGE COMMIT'
```

Expect: all five phrases present, in the `.DESCRIPTION` block before `#>`. Read the
paragraph and judge it against the anchor's wording: "a green 5d is read as 'the image builds
and resolves', not 'the service works'".

## T10 - hygiene claims (anchor: never :local, never an ai-stack_* network)

```
grep -n "':local'\|:local\b" scripts/checks/check-ob1-integration-images.ps1
grep -n "ai-stack_" scripts/checks/check-ob1-integration-images.ps1
grep -n "'--network', 'none'" scripts/checks/check-ob1-integration-images.ps1
```

Expect: `:local` appears only in prose (the header / a comment), never in a `-t` argument;
`ai-stack_` appears only in prose; the `docker run` call carries `'--network', 'none'`.
During T3, `docker ps` in a second shell must never show a container on an `ai-stack_*`
network (the check container is short-lived; `docker events --filter type=container` during
the run shows its create/start/die with no network attach beyond `none`).

## T11 - the walk beyond the first hop (acceptance #2, transitive; attempt-2 addition)

Attempt 1 failed on refutation R3: a one-line intermediate module
(`export * from "./deep.ts";`) was walked as CHARACTERS because PowerShell unrolled the
one-element array `Read-AtPin` returned, so the static half printed "covers all 4 file(s)"
and only the build half refused. Every case below runs WITHOUT Docker: a refusal comes from
the static half before the docker probe, and the two controls are run with `DOCKER_HOST`
pointed at a dead port so the static verdict is printed and the run then stops at the
docker probe (the `Docker is not reachable` sentence is EXPECTED there and is not the thing
under test).

Setup: mint the scratch commits with a temporary index (OB1 working tree untouched). Save
this as `mint.sh` and run it from `<WT>/OB1` in Git Bash:

```
#!/bin/sh
# mint.sh <branch> <parent> [<repo-path>=<local-file>]...
set -e
branch="$1"; parent="$2"; shift 2
export GIT_INDEX_FILE="$(pwd)/../.gate5d-mint-index"
git read-tree "$parent"
for pair in "$@"; do
  p="${pair%%=*}"; f="${pair#*=}"
  blob=$(git hash-object -w "$f")
  git update-index --add --cacheinfo 100644,$blob,"$p"
done
tree=$(git write-tree)
c=$(git commit-tree "$tree" -p "$parent" -m "scratch(gate5d): $branch - local only, never pushed")
git branch -f "$branch" "$c"
unset GIT_INDEX_FILE; rm -f ../.gate5d-mint-index
echo "$branch=$(git rev-parse --short $c)"
```

Then, from `<WT>/OB1` (`S` = any scratch directory; `C=integrations/research-curator`):

```
C=integrations/research-curator; S=/tmp/gate5d; mkdir -p $S
git show a07103b:$C/Dockerfile | sed 's#^COPY claims.ts ./#COPY claims.ts ./\nCOPY pool.ts ./#' > $S/Dockerfile.fixed
git show a07103b:$C/index.ts > $S/index.base.ts        # 642 lines at a07103b
sh mint.sh scratch/gate5d-fixed a07103b "$C/Dockerfile=$S/Dockerfile.fixed"

# (a) two-hop: index.ts -> a.ts -> b.ts; a.ts COPYed, b.ts not
{ cat $S/index.base.ts; echo 'import "./a.ts";'; } > $S/index.c1.ts
printf '// hop one\nimport { b } from "./b.ts";\nexport const a = b;\n' > $S/a.ts
printf 'export const b = 1;\n' > $S/b.ts
sed 's#^COPY pool.ts ./#COPY pool.ts ./\nCOPY a.ts ./#' $S/Dockerfile.fixed > $S/Dockerfile.c1
sh mint.sh scratch/gate5d-c1-twohop scratch/gate5d-fixed "$C/index.ts=$S/index.c1.ts" "$C/a.ts=$S/a.ts" "$C/b.ts=$S/b.ts" "$C/Dockerfile=$S/Dockerfile.c1"

# (b) ONE-LINE barrel, no trailing newline: index.ts -> one.ts -> deep.ts; one.ts COPYed, deep.ts not
{ cat $S/index.base.ts; echo 'import "./one.ts";'; } > $S/index.c2.ts
printf 'export * from "./deep.ts";' > $S/one.ts
printf 'export const deep = 1;\n' > $S/deep.ts
sed 's#^COPY pool.ts ./#COPY pool.ts ./\nCOPY one.ts ./#' $S/Dockerfile.fixed > $S/Dockerfile.c2
sh mint.sh scratch/gate5d-c2-oneline scratch/gate5d-fixed "$C/index.ts=$S/index.c2.ts" "$C/one.ts=$S/one.ts" "$C/deep.ts=$S/deep.ts" "$C/Dockerfile=$S/Dockerfile.c2"

# (c) forms: import type / export * from / export { } from - files exist, none COPYed
{ cat $S/index.base.ts; echo 'import type { T } from "./t.ts";'; echo 'export * from "./star.ts";'; echo 'export { s } from "./s.ts";'; } > $S/index.c3.ts
printf 'export type T = number;\n' > $S/t.ts; printf 'export const star = 1;\n' > $S/star.ts; printf 'export const s = 1;\n' > $S/s.ts
sh mint.sh scratch/gate5d-c3-forms scratch/gate5d-fixed "$C/index.ts=$S/index.c3.ts" "$C/t.ts=$S/t.ts" "$C/star.ts=$S/star.ts" "$C/s.ts=$S/s.ts"

# (d) control: (b) with deep.ts ALSO COPYed
sed 's#^COPY one.ts ./#COPY one.ts ./\nCOPY deep.ts ./#' $S/Dockerfile.c2 > $S/Dockerfile.c4
sh mint.sh scratch/gate5d-c4-control scratch/gate5d-c2-oneline "$C/Dockerfile=$S/Dockerfile.c4"

# (e) commented-out imports of files that do not exist (line, block, block-continuation, trailing)
{ cat $S/index.base.ts; echo '// import { old } from "./gone.ts";'; echo '/* import "./gone2.ts"'; echo '   import "./gone3.ts" */'; echo 'const keep = 1; // import "./gone4.ts"'; } > $S/index.c5.ts
sh mint.sh scratch/gate5d-c5-comments scratch/gate5d-fixed "$C/index.ts=$S/index.c5.ts"

# (f) wrong-case COPY on the incident pin itself
sed 's#^COPY pool.ts ./#COPY Pool.ts ./#' $S/Dockerfile.fixed > $S/Dockerfile.c6
sh mint.sh scratch/gate5d-c6-case a07103b "$C/Dockerfile=$S/Dockerfile.c6"
git rev-parse --short HEAD; git status --short | wc -l     # a07103b, 0
```

Run each from the worktree root. `<sha7>` is whatever mint printed for that branch.

### T11a two-hop chain
```
powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin scratch/gate5d-fixed -NewPin scratch/gate5d-c1-twohop; echo $LASTEXITCODE
```
Expect exit 1, no `static: ... covers` line, no `build:` line, and the refusal
`research-curator/a.ts:2 imports 'b.ts', and the Dockerfile never COPYs it (it lists: deno.json, index.ts, claims.ts, pool.ts, a.ts, ...)` ... `FIX in OB1: add 'COPY b.ts ./'`,
then `FAIL: 1 relative import(s) ... No docker build was attempted`.
Developer's run: exactly that, exit 1.

### T11b one-line intermediate module (the attempt-1 regression)
```
powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin scratch/gate5d-fixed -NewPin scratch/gate5d-c2-oneline; echo $LASTEXITCODE
```
Expect exit 1 and the refusal `research-curator/one.ts:1 imports 'deep.ts', and the Dockerfile never COPYs it` ... `FIX in OB1: add 'COPY deep.ts ./'`, `No docker build was attempted`.
NOT acceptable: `covers all 4 file(s)` (the attempt-1 output), or a refusal that comes from
`deno check` inside an image. Developer's run: refused at `one.ts:1`, exit 1, 2057 ms.

### T11c import forms
```
powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin scratch/gate5d-fixed -NewPin scratch/gate5d-c3-forms; echo $LASTEXITCODE
```
Expect exit 1 and THREE refusals: `index.ts:643 imports 't.ts'` (`import type`),
`index.ts:644 imports 'star.ts'` (`export * from`), `index.ts:645 imports 's.ts'`
(`export { } from`), then `FAIL: 3 relative import(s)`. Developer's run: all three, exit 1.

### T11d control - the whole chain COPYed
```
$env:DOCKER_HOST='tcp://127.0.0.1:9'
powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin scratch/gate5d-fixed -NewPin scratch/gate5d-c4-control; echo $LASTEXITCODE
Remove-Item Env:DOCKER_HOST
```
Expect `static: integrations/research-curator/Dockerfile covers all 5 file(s) reachable from index.ts`
(index.ts, claims.ts, pool.ts, one.ts, deep.ts) followed by the `Docker is not reachable`
refusal, exit 1. The 5 is the assertion: 4 means deep.ts was not walked. Developer's run: 5.

### T11e commented-out imports are not imports
```
$env:DOCKER_HOST='tcp://127.0.0.1:9'
powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin scratch/gate5d-fixed -NewPin scratch/gate5d-c5-comments; echo $LASTEXITCODE
Remove-Item Env:DOCKER_HOST
```
Expect `covers all 3 file(s)` and then the docker refusal; NOT acceptable: any line naming
`gone.ts`, `gone2.ts`, `gone3.ts` or `gone4.ts`. Developer's run: covers all 3.

### T11f wrong-case COPY does not cover
```
powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin a07103b -NewPin scratch/gate5d-c6-case; echo $LASTEXITCODE
```
Expect exit 1 and the refusal `index.ts:39 imports 'pool.ts'` with the list showing
`Pool.ts`, `No docker build was attempted`. Developer's run: exactly that.

### T11 teardown
```
git -C OB1 branch -D scratch/gate5d-fixed scratch/gate5d-c1-twohop scratch/gate5d-c2-oneline scratch/gate5d-c3-forms scratch/gate5d-c4-control scratch/gate5d-c5-comments scratch/gate5d-c6-case
git -C OB1 status --short | Measure-Object     # 0
```

## Result format

For each T: PASS / FAIL, the exact command run, the verbatim output (trimmed to the lines
the Expect block names, plus any surprise), exit code, and for T6 the milliseconds. Findings
that are true but outside this item go to `documentation/notes/deploy-gate-2026-09-06.md`
under `## gate5d`, with file:line.
