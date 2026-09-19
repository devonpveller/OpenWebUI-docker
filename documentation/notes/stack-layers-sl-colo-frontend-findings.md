# Findings — sl-colo-frontend (stack-layers PLAN 2.7, Part L.1)

Written 2026-09-19 from worktree `wt-sl-colo-frontend`, branch
`work/sl-colo-frontend`. Base `development` @ `3a373e2` for attempt 1; **rebased
onto `2f5c451`** (sl-driver-parity) for attempt 2, and every base-relative
measurement below re-taken against `2f5c451`.

**Attempt 1 FAILED on T14**: F4 and F5 stated reasons that were measurably
false, and F4 reported a fix that was not delivered. Both are rewritten below
and carry a correction banner; the real mechanism is the new **F13**. F14 is
also new. Nothing else in this file changed.

Everything below is **out of scope for the item** (which moves the frontend
plane's build inputs into `frontend/` and repoints the pointers) and is written
here rather than into the deliverable, per MERGE-PROTOCOL §2.

**Provenance labels** used on every entry:

- **[source]** — read from the file in this worktree at the stated path/line.
- **[measured]** — a command I ran here, with its output; the command is given.
- **[not verifiable from this tree]** — depends on something outside the repo
  (a daemon's parser, a running image's internals). Check before acting.

---

## F1 — The repo root had exactly two build consumers, and both were this plane

**[measured]** `MSYS_NO_PATHCONV=1 git grep -n "context:" -- '*.yml' '*.yaml'`
over the whole tree (OB1 and `scripts/archive/` excluded) returns 19 build
contexts. Every one is a
subdirectory or a sibling (`./config/watcher`, `../little-coder`, `../backup`,
`./gateway`, `../../mnemory`, …) **except** `frontend/docker-compose.yml:159`
and `:280`, which were `context: ..` — the repo root.

That is why the root `.dockerignore` moved with the files rather than being
kept: after this item **no build context is rooted at the repo root**, so a root
`.dockerignore` would guard nothing. `git ls-files | grep dockerignore` now
returns exactly one path, `frontend/.dockerignore`.

## F2 — `.gitattributes` needed no change, and that is luck, not design

**[source + measured]** `.gitattributes:4-5` carries `*.sh text eol=lf` and
`entrypoint.sh text eol=lf`. Neither pattern contains a slash, so git matches
them **at any directory depth** — the move could not strand them. Proven, not
assumed:

    git check-attr text eol -- frontend/entrypoint.sh
    frontend/entrypoint.sh: text: set
    frontend/entrypoint.sh: eol: lf

Had line 5 been written path-anchored (`/entrypoint.sh`), the rule would have
stopped applying at the new path **silently**, and the next fresh checkout would
have produced a CRLF `.sh` that `dockerfile.tailscale` copies into the image —
the failure class `documentation/runbooks/PREVENTION-GUIDE.md` exists for. Line 5
is also now redundant with line 4; leaving it alone was the line-neutral choice.

## F3 — `.dockerignore` has no line-ending rule, before or after the move

**[measured]** `git check-attr text eol -- frontend/.dockerignore` reports
`unspecified` for both, exactly as the root file did. Its working-tree copy
therefore follows `core.autocrlf`; the blob is LF (`git show
2f5c451:.dockerignore` has no CR bytes) and the file I staged has none either.

**[not verifiable from this tree]** Whether a CRLF working-tree `.dockerignore`
would break the `!entrypoint.sh` re-include depends on the Docker daemon's
ignore-file parser, which is not in this repo. This item does **not** change
that exposure in either direction — it is identical before and after — so no
rule was added. If someone wants the guarantee, `.dockerignore text eol=lf` in
`.gitattributes` is the one-line change, and it should be made with a test that
actually builds, not from this note.

## F4 — `scripts/recovery/update-stack.bat` had a path bug at its Dockerfile step, independent of this move

**Corrected 2026-09-19 after attempt 1. The first version of this entry gave a
false mechanism and claimed a fix that was not delivered — see F13, which is the
real mechanism, and read this entry as a pointer to it.**

**[measured]** The script's "Step 3" edit command (`:174`) reads and writes a
path relative to the current directory, and that directory is not what the
script thinks. The reason is **not** that `%SCRIPT_DIR%` is `scripts/recovery/`:
`SCRIPT_DIR` is **never assigned anywhere in the file** (F13). The cwd at `:174`
is whatever the last successful `cd` left, which is the **drive root** `D:\` —
`:68` and `:77` do `cd /d "%SCRIPT_DIR%\..\.."`, which with an empty variable
resolves to `\..\..` and lands on `D:\`, and `:93`'s `cd /d "%SCRIPT_DIR%"`
errors out (`The filename, directory name, or volume label syntax is
incorrect.`, errorlevel 1) leaving the cwd where it was. Measured in `cmd` with
the script's own `@echo off` + `setlocal enabledelayedexpansion` preamble.

So `'..\Dockerfile.openwebui-gpu'` resolved to `D:\Dockerfile.openwebui-gpu`, a
path that has never existed — the rewrite read 0 lines and `Set-Content` would
have created a stray file at the drive root.

**What this item did about it: nothing, deliberately.** `:174` is a **name-only
repoint** — `'..\Dockerfile.openwebui-gpu'` → `'..\frontend\Dockerfile.openwebui-gpu'`,
the same shape the author wrote with `frontend\` inserted. It was broken before
and it is broken after, in exactly the same way. **No fix is claimed.** The
first attempt rewrote the line to `%SCRIPT_DIR%\..\..\frontend\...` and this
note called it "cwd-independent"; it expands to `\..\..\frontend\...` and reads
0 lines, so that was a fix asserted and not delivered. Reverted.

## F5 — `update-stack.bat` is inert for a much larger reason this item does not fix

**Corrected 2026-09-19 after attempt 1. The headline is unchanged and was right;
the mechanism given the first time was wrong. It said the `docker compose` calls
"run from the repo root … so all of these fail with `no such service`". They do
not run from the repo root, and that is not the error they get.**

**[measured]** The script's thirteen `cd /d "%SCRIPT_DIR%\..\.."` lines
(`:68,77,182,206,236,245,262,272,295,360,385,393,536`) all land on the **drive
root** `D:\`, because `SCRIPT_DIR` is never assigned (F13). Every `docker
compose` call that follows one — `build --no-cache openwebui` (`:183`),
`run --rm … openwebui` (`:207`), `up -d openwebui` (`:237`),
`up -d llama-cpp-upstream llama-cpp-embed-upstream tailscale` (`:263`),
`up -d llm-gateway` (`:537`) — therefore fails at `D:\` with
**`no configuration file provided: not found`**, before compose ever looks for a
service. Measured: `cd D:\ && docker compose ps` prints exactly that.

The headline still holds, and holds twice over. The root project **is** a
zero-service network anchor — `docker-compose.yml` is 54 lines with a
`networks:` key at `:34` and **no `services:` key at all** (verified:
`grep -c "^services:"` returns 0) — so even a script that reached the repo root
correctly would fail those calls with `no such service`. `update-stack.bat` is
inert for **both** reasons, and F13's is the one that fires first.

The first version of this entry also said the script "is internally inconsistent
about its own depth" (`:505` uses `%SCRIPT_DIR%\..\` where `:68` uses
`%SCRIPT_DIR%\..\..\`). That is true as a reading of the text and useless as a
diagnosis: both spellings expand from an empty variable, so neither has a depth.
Dropped in favour of F13.

This item repointed only the six `Dockerfile.openwebui-gpu` references in the
file, by name, changing no path shape (F4). **Fixing the script is a separate
piece of work**, and it should decide first whether the script is worth keeping
next to `stack.ps1` and `emergency-recovery.ps1` (the `.bat` twin of the
recovery script was archived 2026-08-21 for exactly that reason).

## F6 — `scripts/recovery/rebuild_tailscale.py` needed no edit, and is inert for the same reason

**[source]** Read end to end. `find_project_root()` (`:29`) returns
`/host_project` when it exists, otherwise walks parents until it finds a
directory containing `docker-compose.yml` — the repo root. `main()` (`:88`)
then runs `docker compose down tailscale`, `build --no-cache tailscale` and
`up -d tailscale` **in that directory**. The root project has no `tailscale`
service, so all three fail. The script never names a Dockerfile, which is why
the move required no change to it; its brokenness predates this item and is not
caused by it.

## F7 — `scripts/checks/dev-helper.ps1` has the same root-project assumption

**[source]** `Invoke-Rebuild` (`:97`) does `Set-Location $PROJECT_DIR`
(the repo root, computed at `:13`) and then `docker compose build --no-cache
tailscale`; `Test-DockerCompose` (`:58`) runs a bare `docker compose config`
in the same place. Both address the zero-service anchor. Out of scope here — what
this item changed in that file is only the entrypoint path it checks
(`:80`) and the two operator-facing strings naming it (`:78`, `:92`).

## F8 — `PREVENTION-GUIDE.md` points at a script path that does not exist

**[source]** `documentation/runbooks/PREVENTION-GUIDE.md` tells the reader to run
`.\scripts\dev-helper.ps1` seven times (`:28,31,34,77,80,92,132`). The script is at
`scripts/checks/dev-helper.ps1`. Pre-existing; the lines this item touched in
that file were only the ones naming `entrypoint.sh` / `dockerfile.tailscale`.

## F9 — `workspace-stacks.md:23` states a line count that no longer holds

**[source + measured]** The history paragraph says `entrypoint.sh` was
"rewritten to a 318-line route table". `wc -l frontend/entrypoint.sh` is **321**
(and was 321 at `2f5c451`, before this item's one-line comment edit, which is
line-neutral). The sentence describes a past commit, so it was left as written
rather than silently updated to a number that would then describe neither the
commit nor today.

## F10 — This item shifts every line below `workspace-stacks.md:114` by 8

**[measured]** The §1a insertion is 8 new lines at `:115-122` (plus one modified
line at `:225`, the `llm-gateway-ui` row). A sweep for
`workspace-stacks.md:<NN>` citations tree-wide finds three, all in
`documentation/evidence/` (`sl-colo-inference/test-plan.md:604`,
`stack-layers/sl-colo-gateways-test-plan.md:323` and `:337`) — the exempt class,
and each is a record of a run against a past tree rather than a live pointer.
No live citation into that file by line exists. Recorded so the next reader of
those plans knows why the numbers are off by 8.

## F11 — The live containers hold no bind to anything this item moved

**[measured, 2026-09-19, not re-run since]**

    docker inspect openwebui --format '{{.Config.Image}} | {{range .Mounts}}...'
    openwebui:local | volume frontend_openwebui-data -> /app/backend/data;
      bind .../config -> /app/config; .../data/tailscale, .../status-pipe,
      .../system-prompts -> /host_project/...
    tailscale:local | bind .../data/tailscale -> /var/lib/tailscale;
      /dev/net/tun -> /dev/net/tun

Neither container binds `entrypoint.sh`, either Dockerfile or `.dockerignore`:
`entrypoint.sh` is **baked into `tailscale:local`** by
`frontend/dockerfile.tailscale:7` (`COPY`), not mounted. So landing this branch
cannot disturb a running container, and no recreate is needed — see the test
plan's live-hazard case.

## F12 — The running `openwebui` still carries a mount its compose file no longer declares

**[measured, 2026-09-19]** The inspect above shows
`bind .../ai-stack/config -> /app/config` on the live container. That mount was
**removed** from `frontend/docker-compose.yml` by sl-colo-inference (merged at
`3a373e2`, carried into `2f5c451`); the container has not been recreated since,
so it still has it.

Not a defect and not this item's to fix — it is the same fact the live-hazard
case rests on: the running frontend is rendered from an older version of this
file, which is precisely why a pure path move cannot affect it. Whoever next
recreates `openwebui` (deliberately, with `WEBUI_SECRET_KEY` set) will drop the
mount; nothing reads it (sl-colo-inference F2/F13).

## F13 — `%SCRIPT_DIR%` in `scripts/recovery/update-stack.bat` is a variable that is never set

Added 2026-09-19 after attempt 1, where the tester measured what F4 and F5 had
only reasoned about. This is the mechanism those two entries now point at.

**[measured]** In `scripts/recovery/update-stack.bat`:

    grep -c "SCRIPT_DIR" scripts/recovery/update-stack.bat        -> 27
    grep -niE 'set[[:space:]]+"?SCRIPT_DIR|SCRIPT_DIR='  <file>   -> no match (exit 1)
    grep -n "dp0" <file>                                          -> no match (exit 1)

**27 uses, zero definitions, no `%~dp0` anywhere** — identical counts on the
base `2f5c451` and on this branch, so this is neither caused nor touched by this
item. (The tester measured 28 on attempt 1; the extra one was in the `:174`
rewrite this attempt reverted.)

Executed in `cmd`, with the script's own preamble (`@echo off` +
`setlocal enabledelayedexpansion`), because batch expands an undefined variable
to nothing rather than erroring:

    echo [RAW]%SCRIPT_DIR%[END]              -> [RAW][END]                (empty)
    cd /d "%SCRIPT_DIR%\..\.."               -> cwd = D:\   err=0         (the DRIVE root)
    cd /d "%SCRIPT_DIR%"                     -> "The filename, directory name, or
                                                volume label syntax is incorrect."
                                                err=1, cwd UNCHANGED

So the whole `%SCRIPT_DIR%` family is dead: every `cd /d "%SCRIPT_DIR%\..\.."`
(thirteen of them: `:68,77,182,206,236,245,262,272,295,360,385,393,536`) lands
on the drive root, every `cd /d "%SCRIPT_DIR%"` (`:93,185,209,248,252,264,275,297,371,377,389,397,542`)
errors and leaves the cwd wherever it was, and the one remaining path built from
it — `:505`, `findstr … "%SCRIPT_DIR%\..\docker-compose.yml"` — resolves against
nothing. (`:174` used it too, in this branch's first attempt; that rewrite is
reverted, so the only `%SCRIPT_DIR%` uses on this branch are the ones already on
the base.) That makes the script inert **independently** of the zero-service
root project in F5, and it fires first.

**Recorded, not fixed — deliberately out of scope for a colocation item.**
[measured] The minimal repair, if the script is kept, is one line
(`set "SCRIPT_DIR=%~dp0"`) inserted immediately after `setlocal
enabledelayedexpansion` at `:6`, i.e. as a new `:7`. I verified in `cmd` that
this shape works as the existing call sites are written: `%~dp0` ends in a
backslash and `cd /d "%SCRIPT_DIR%\..\.."` tolerates the doubled separator,
going up two levels from `scripts/recovery/` to the repo root, and
`cd /d "%SCRIPT_DIR%"` then succeeds. It would **not** fix `:505`
(`"%SCRIPT_DIR%\..\docker-compose.yml"` would become
`scripts\docker-compose.yml`, which does not exist), and it would turn a dozen
`docker compose` calls from "fail at the drive root" into "fail at the
zero-service anchor" (F5) — so it is the first line of a repair, not the repair.
Whoever decides that script's fate owns this.

## F14 — Two of this item's acceptance clauses were vacuous

**[measured]** The anchor's artifact/acceptance text names
"`documentation/runbooks/UPDATE-MANAGEMENT.md` build commands" and the
"README.md repo map" as things to repoint. An unbounded
`git grep -n -e Dockerfile.openwebui-gpu -e dockerfile.tailscale -e entrypoint.sh`
returns **no hit in either file**, on the base or on this branch:
UPDATE-MANAGEMENT.md carries no rebuild command naming a Dockerfile (its
`openwebui` references are the netns restart-order rule and the pinned-image
policy), and README.md's repo map lists directories, not root files.

Neither clause could be satisfied or violated, which is why both files are
absent from this branch's diff — recorded here so a reviewer does not read that
absence as an omission. Worth trimming from the next stack-layers anchor:
a criterion that cannot fail is not a criterion.
