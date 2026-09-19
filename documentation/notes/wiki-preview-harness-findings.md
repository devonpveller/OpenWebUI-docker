# Wiki preview harness — findings (2026-09-05, item `previewfix`)

Written while fixing `OB1/docker/docker-compose.preview.yml` so that following
its own header from a clean state serves a page. Everything here was OUT OF
SCOPE for that item; recorded so it is not lost.

Corrected 2026-09-05 during review: item #2 credited the preview's vault
seeder with curing a Quartz warning it does not cure, and item #3's example
file was already covered by the `.gitattributes` this item added. Nothing has
been deleted — both wrong claims are stated and superseded in place, under a
**CORRECTION** heading, so a reader who remembers the old wording can see what
happened to it. Every other claim below was re-checked at the same time; the
file:line citations were added then.

## 1. The preview's `--build` used to retag PRODUCTION images (fixed here, but note the class)

The preview services carried `image: openbrain-workbench:local`,
`openbrain-wiki-viewer:local`, `openbrain-extract:local` while the header told
you to run `up -d --build` (the three tags at lines 66, 74 and 111 of
`git -C OB1 show b69cdbf:docker/docker-compose.preview.yml`, the documented
`--build` at line 6 of the same). Following the documented instruction
therefore moved three `:local` tags — the tags the live stack runs — onto
images built
from whatever tree you happened to be in. The running containers keep their
image ID, so nothing visibly breaks; the damage lands on the NEXT recreate of
the production service, arbitrarily later, with no trace back to the preview.

Fixed in this item (`:preview` tags). The class worth watching: **any compose
file in this repo that both declares a `:local` image and documents `--build`
is a production-retag waiting to happen.** A pre-commit check ("no non-prod
compose file may write a `:local` tag") would make this structural rather than
a thing each author has to remember. Not built.

## 2. `git init` in the workbench never sets a branch and never commits

`OB1/docker/workbench/src/util/vault.ts:ensureVaultRepo()` (vault.ts:25-39)
runs `git init -q` and then only sets `user.email`/`user.name`. It never names
a branch and it never commits. On any fresh vault that leaves:

* the default branch name (`master`: the workbench image ships git 2.47.3 with
  `init.defaultBranch` unset, confirmed by running `git init` in it) — the
  rest of the stack assumes `main` (`wiki-service/wiki-service.mjs:138`,
  `const GIT_BRANCH = ENV.WIKI_GIT_BRANCH || "main"`);
* an UNBORN HEAD, so `git show HEAD:<path>` (vault.ts:71 and vault.ts:98)
  fails and with it every note history / revert / folder-history path in
  `vault.ts`, until something else makes the first commit.

Observed directly on 2026-09-05: a freshly-`up`ed preview reported
`fatal: your current branch 'master' does not have any commits yet`.

In PRODUCTION this is masked — the wiki-service creates and commits the vault
repo (`wiki-service.mjs:218-221`, `git commit -q -m "wiki: initial repo"` on a
fresh repo) before the workbench ever touches it — so this is not an outage,
it is a latent first-run defect. The preview now works around it in its own
seeder rather than changing `ensureVaultRepo()`, which is product code and out
of scope here. A real fix would be `git init -q -b main` plus an empty
initial commit in `ensureVaultRepo()`.

**CORRECTION (2026-09-05, at review).** This item originally read that the
same freshly-`up`ed preview made "Quartz log `isn't yet tracked by git, dates
will be inaccurate` for all 10 seed pages", and counted that warning among the
things the preview's seeder fixes. The observation is true; the CAUSAL claim
is false, and the seeder does not cure the warning. The reviewer re-checked it
with the fixed stack up and the vault a genuine repo on `main` (their run: the
seed commit, `git show HEAD:index.md` working) and all 10 pages warned anyway.

Mechanism, reproduced inside `openbrain-wiki-viewer:preview` against a vault
that was a repo with everything committed:

* `OB1/docker/wiki-viewer/entrypoint.sh:7-8` does `ln -sfn /wiki
  /quartz/content`, so Quartz reaches the vault only through a symlink and
  every page's path lies under `/quartz`;
* Quartz's `CreatedModifiedDate` transformer
  (`/quartz/quartz/plugins/transformers/lastmod.ts` in the image, v4.5.1)
  does `Repository.discover(ctx.argv.directory)` — which follows the symlink
  and yields workdir `/wiki/` — then looks each file up as
  `path.relative(workdir, file.data.filePath)`, where `filePath` is the
  cwd-relative `content/content/<page>.md`. That resolves to
  `../quartz/content/content/<page>.md`, outside the repository, so
  `getFileLatestModifiedDateAsync` throws and the `catch` prints the warning.
  Measured in the image: workdir `/wiki/`, computed path
  `../quartz/content/content/foo.md`, `Error: Failed to get commit`. It is
  also why the logged paths read `content/content/...`.

So the warning fires for every page whatever the vault's git state, and page
dates fall back to frontmatter/mtime (`quartz.config.ts` priority
`["frontmatter", "git", "filesystem"]`). Fixing it is viewer-side — mount the
vault at `/quartz/content` instead of symlinking it, or patch `lastmod.ts` —
and is a separate piece of work. The `.git` exclusions in that entrypoint's
`ignorePatterns` sed are NOT part of this: they only keep Quartz from
rendering the vault's `.git` as content.

The two artifact comments that repeated the wrong claim (the `preview-seed`
block in `docker-compose.preview.yml` and the `seed-vault.sh` header) were
corrected in OB1 `df5d83e`, which also states there, where a reader meets it,
that the warning persists and why.

## 3. OB1 has no `.gitattributes`, so its shell scripts check out CRLF

The parent checkout runs `core.autocrlf=true` and OB1 carries no repo-wide
attributes file, so its tracked `*.sh` files are restored with CRLF. Any such
script bind-mounted into a container (or `COPY`ed into an image) and run by
`/bin/sh` fails with a bad-interpreter error.

The example: `OB1/docker/wiki-viewer/entrypoint.sh`. It is CRLF on disk in
both this worktree and the operator's checkout, `git -C OB1 check-attr eol --
docker/wiki-viewer/entrypoint.sh` says `unspecified`, and the viewer Dockerfile
carries a `RUN sed -i` at Dockerfile:288 that strips a literal CR from
`/entrypoint.sh` in the image, with a comment recording the crash-loop it
caused twice (2026-08-25 and again 2026-08-26) — a per-file workaround in
the image build, standing in for the missing rule. Six tracked `.sh` files
outside `docker/preview/` are in that state as of OB1 `df5d83e`:
that entrypoint, the three `docker/backup/*.sh`,
`integrations/openbrain-idea-refinery/test-e2e.sh`, and
`integrations/openclaw-agent-memory/plugin/smoke/native-openclaw-smoke.sh`.

The parent's own gate does not see them either:
`scripts/checks/validate-lineendings.ps1` (pre-commit step 2) walks
`git ls-files '*.sh'`, which lists ZERO OB1 paths — a submodule is one gitlink
in the parent index, not its files.

This item pinned `OB1/docker/preview/.gitattributes` (`*.sh text eol=lf`)
because it added a mounted script there; **the rest of OB1 is still unpinned**
— the parent repo has a repo-wide `*.sh text eol=lf` rule, the submodule does
not. Worth one `.gitattributes` at the OB1 root, which is an OB1-wide change
and not this item's to make.

**CORRECTION (2026-09-05, at review).** This item originally used
`OB1/docker/preview/migrate-notes.sh` as its example of a still-unpinned
script. That file is INSIDE `docker/preview/`, so the `.gitattributes` this
item added covers it: `git -C OB1 check-attr text eol --
docker/preview/migrate-notes.sh` returns `text: set` / `eol: lf`, and a fresh
clone of OB1 at this commit therefore writes it LF whatever `core.autocrlf`
says. Two states worth keeping apart: the attributes rule governs what a
checkout WRITES, so a working copy that already held the file before the rule
landed can keep CRLF on disk until that blob is rewritten — true of someone's
disk, false of the shipped tree. (Here it is LF in both checkouts anyway.) The
conclusion above is unaffected: it was the example that was wrong, not the
finding.

## 4. `derive-graph-index.mjs`'s hard default is a production hostname

`OPEN_BRAIN_URL` defaults to `http://openbrain-rest`
(`wiki-viewer/derive-graph-index.mjs:35`, and the same default in
`wiki-viewer/lib/wiki-db.mjs:13` and `lib/nav-index.mjs:36`). Production relies
on that default — `openbrain-wiki-viewer` sets no `OPEN_BRAIN_URL` at all in
`OB1/docker/docker-compose.yml` — so it cannot simply be removed. The
consequence is that ANY deployment of the viewer outside the OB1 network
silently loses its graph index, its nav index and its
DB-render fallback, and (because a failed derive aborts the publish —
`wiki-viewer/entrypoint.sh:153-157` for the cold start, `:293-297` for every
later snapshot) serves a permanent 503 rather than degrading. The preview now
sets the variable explicitly. A more robust shape would be an explicit "no
index source" configuration that publishes an empty index instead of failing;
that changes viewer behaviour and was out of scope.

## 5. The `/rest/v1` route is host-reachable in the preview

`preview-caddy` now proxies `/rest/v1/*` to PostgREST
(`OB1/docker/preview/Caddyfile:23-28`, `handle_path /rest/v1/*` — which also
strips inbound `Authorization`/`apikey`), and `preview-caddy` is published on
`127.0.0.1:8099` (`docker-compose.preview.yml:235`). So the whole preview
database is readable and writable from the host with no auth. That is
deliberate for a loopback-only throwaway stack with a hardcoded password (and
makes the REST surface pokeable), but it is a reason not to lift this compose
file into anything that binds a non-loopback address.
