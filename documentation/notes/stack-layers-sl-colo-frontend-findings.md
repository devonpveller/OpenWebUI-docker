# Findings — sl-colo-frontend (stack-layers PLAN 2.7, Part L.1)

Written 2026-09-19 from worktree `wt-sl-colo-frontend`, branch
`work/sl-colo-frontend`, base `development` @ `3a373e2`.

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
3a373e2:.dockerignore` has no CR bytes) and the file I staged has none either.

**[not verifiable from this tree]** Whether a CRLF working-tree `.dockerignore`
would break the `!entrypoint.sh` re-include depends on the Docker daemon's
ignore-file parser, which is not in this repo. This item does **not** change
that exposure in either direction — it is identical before and after — so no
rule was added. If someone wants the guarantee, `.dockerignore text eol=lf` in
`.gitattributes` is the one-line change, and it should be made with a test that
actually builds, not from this note.

## F4 — `scripts/recovery/update-stack.bat` had a path bug at its Dockerfile step, independent of this move

**[source]** The script's cwd at its "Step 3" Dockerfile edit was
`%SCRIPT_DIR%` — set at `:93`, with the next `cd` at `:182`. `%SCRIPT_DIR%` is
`scripts/recovery/` (`:68` does `cd /d "%SCRIPT_DIR%\..\.."` to reach the repo
root). The edit command read and wrote `'..\Dockerfile.openwebui-gpu'`, i.e.
`scripts\Dockerfile.openwebui-gpu` — a path that has never existed. The rewrite
would have silently produced nothing, or `Set-Content` would have created the
stray file.

Repointing it could not preserve the old (broken) relative form, so `:174` now
uses `%SCRIPT_DIR%\..\..\frontend\Dockerfile.openwebui-gpu`, which is
cwd-independent. This is the one place where the repoint also fixed a
pre-existing defect; it is called out here so a reviewer is not surprised by it
in the diff.

## F5 — `update-stack.bat` is inert for a much larger reason this item does not fix

**[source]** Every `docker compose` call in the script runs from the repo root
(`cd /d "%SCRIPT_DIR%\..\.."` at `:68,77,182,206,236,245,262,272,295,360,385,393,536`)
and names services by key: `build --no-cache openwebui` (`:183`),
`run --rm ... openwebui` (`:207`), `up -d openwebui` (`:237`),
`up -d llama-cpp-upstream llama-cpp-embed-upstream tailscale` (`:263`),
`up -d llm-gateway` (`:537`). The root project has been a **zero-service network
anchor** since CLEANUP-PLAN Part K.5b, so all of these fail with
`no such service`. Separately, `:505` reads `"%SCRIPT_DIR%\..\docker-compose.yml"`
— `scripts\docker-compose.yml`, which does not exist — while `:68` uses
`..\..` for the same root, so the script is internally inconsistent about its own
depth.

This item repointed only the three `Dockerfile.openwebui-gpu` references plus
the one in F4. **Fixing the script is a separate piece of work**, and it should
decide first whether the script is worth keeping next to `stack.ps1` and
`emergency-recovery.ps1` (the `.bat` twin of the recovery script was archived
2026-08-21 for exactly that reason).

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
(and was 321 at `3a373e2`, before this item's one-line comment edit, which is
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
`3a373e2`); the container has not been recreated since, so it still has it.

Not a defect and not this item's to fix — it is the same fact the live-hazard
case rests on: the running frontend is rendered from an older version of this
file, which is precisely why a pure path move cannot affect it. Whoever next
recreates `openwebui` (deliberately, with `WEBUI_SECRET_KEY` set) will drop the
mount; nothing reads it (sl-colo-inference F2/F13).
