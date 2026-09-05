# Wiki preview harness — findings (2026-09-05, item `previewfix`)

Written while fixing `OB1/docker/docker-compose.preview.yml` so that following
its own header from a clean state serves a page. Everything here is TRUE and
VERIFIED but OUT OF SCOPE for that item; recorded so it is not lost.

## 1. The preview's `--build` used to retag PRODUCTION images (fixed here, but note the class)

The preview services carried `image: openbrain-workbench:local`,
`openbrain-wiki-viewer:local`, `openbrain-extract:local` while the header told
you to run `up -d --build`. Following the documented instruction therefore
moved three `:local` tags — the tags the live stack runs — onto images built
from whatever tree you happened to be in. The running containers keep their
image ID, so nothing visibly breaks; the damage lands on the NEXT recreate of
the production service, arbitrarily later, with no trace back to the preview.

Fixed in this item (`:preview` tags). The class worth watching: **any compose
file in this repo that both declares a `:local` image and documents `--build`
is a production-retag waiting to happen.** A pre-commit check ("no non-prod
compose file may write a `:local` tag") would make this structural rather than
a thing each author has to remember. Not built.

## 2. `git init` in the workbench never sets a branch and never commits

`OB1/docker/workbench/src/util/vault.ts:ensureVaultRepo()` runs
`git init -q` and nothing else. On any fresh vault that leaves:

* the default branch name (`master` here, whatever `init.defaultBranch` says
  elsewhere) — the rest of the stack assumes `main`;
* an UNBORN HEAD, so `git show HEAD:<path>` fails and with it every note
  history / revert / folder-history path in `vault.ts`, until something else
  makes the first commit.

Observed directly on 2026-09-05: a freshly-`up`ed preview reported
`fatal: your current branch 'master' does not have any commits yet`, and
Quartz logged `isn't yet tracked by git, dates will be inaccurate` for all 10
seed pages.

In PRODUCTION this is masked — the vault repo predates the workbench and the
wiki-service does the committing — so this is not an outage, it is a latent
first-run defect. The preview now works around it in its own seeder rather
than changing `ensureVaultRepo()`, which is product code and out of scope
here. A real fix would be `git init -q -b main` plus an empty initial commit
in `ensureVaultRepo()`.

## 3. OB1 has no `.gitattributes`, so its shell scripts check out CRLF

The parent checkout runs `core.autocrlf=true` and OB1 carries no attributes
file, so e.g. `OB1/docker/preview/migrate-notes.sh` is on disk with CRLF
terminators. Any such script bind-mounted into a container and run by
`/bin/sh` fails with a bad-interpreter error. This item pinned
`OB1/docker/preview/.gitattributes` (`*.sh text eol=lf`) because it added a
mounted script there; **the rest of OB1 is still unpinned** — the parent repo
has a repo-wide `*.sh text eol=lf` rule, the submodule does not. Worth one
`.gitattributes` at the OB1 root, which is an OB1-wide change and not this
item's to make.

## 4. `derive-graph-index.mjs`'s hard default is a production hostname

`OPEN_BRAIN_URL` defaults to `http://openbrain-rest` (also in
`wiki-viewer/lib/wiki-db.mjs` and `lib/nav-index.mjs`). Production relies on
that default — `openbrain-wiki-viewer` sets no `OPEN_BRAIN_URL` at all — so it
cannot simply be removed. The consequence is that ANY deployment of the viewer
outside the OB1 network silently loses its graph index, its nav index and its
DB-render fallback, and (because a failed derive aborts the publish) serves a
permanent 503 rather than degrading. The preview now sets the variable
explicitly. A more robust shape would be an explicit "no index source"
configuration that publishes an empty index instead of failing; that changes
viewer behaviour and was out of scope.

## 5. The `/rest/v1` route is host-reachable in the preview

`preview-caddy` now proxies `/rest/v1/*` to PostgREST, and `preview-caddy` is
published on `127.0.0.1:8099`. So the whole preview database is readable and
writable from the host with no auth. That is deliberate for a loopback-only
throwaway stack with a hardcoded password (and makes the REST surface
pokeable), but it is a reason not to lift this compose file into anything that
binds a non-loopback address.
