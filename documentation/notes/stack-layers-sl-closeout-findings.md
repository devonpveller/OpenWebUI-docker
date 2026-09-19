# stack-layers / sl-closeout — findings sink (2026-09-19)

Written while correcting findings 1-17 of
`documentation/notes/cleanup-branch-closeout-audit-2026-09-19.md`. Everything
here is TRUE and OUT OF SCOPE for `sl-closeout`, or is a correction to the audit
note itself. Provenance is labelled per MERGE-PROTOCOL §2: **[source]** = read
from the file at the cited line, **[live]** = observed by running something,
**[unverifiable here]** = cannot be settled from this tree.

## 1. The audit's own count of the pre-commit checks was wrong (finding 7)

The note says the hook "runs 8" and cites `.githooks/pre-commit:9-17`, which is
the *header comment*, not the body — and the header itself lists nine entries
and omits check 5d. The body invokes **ten** blocking checks, in this order:
`check-staged-secrets.ps1` (:24), `validate-lineendings.ps1` (:31),
`check-doc-placement.ps1` (:48), `check-llm-gateway-routing.ps1` (:57),
`check-corpus-exposure-producers.ps1` (:85), `check-project-configs.ps1` (:94),
`check-env-file-scope.ps1` (:103), `check-ob1-recipe-tests.ps1` (:114),
`check-ob1-deno-recipes.ps1` (:133), `check-ob1-integration-images.ps1` (:157).
A non-blocking attestation step (`# --- 6. ATTESTATION`, :163) runs last and can
never fail the commit. **README.md and scripts/README.md were corrected to ten,
not to the note's eight.** The header comment at `.githooks/pre-commit:7-16` is
still one check short (no 5d) — not corrected here, it is not one of the 17.
[source: .githooks/pre-commit]

## 2. `scripts/checks/plan-store.ps1` resolves both the store AND the index from CWD

**Corrected 2026-09-19 after the tester refuted the first version of this entry.**
It is not "needs `-Store` from a worktree" — the determinant is the CURRENT
DIRECTORY, and `-Store` fixes only half of what that breaks.

`plan-store.ps1:56` sets `$Root = git rev-parse --show-toplevel`, which answers
for the CWD, not for `$PSScriptRoot`. Two things hang off `$Root`:

- the plan store, derived as `<$Root>/../documentation-plans-ai-stack` (:59),
  with a hard `throw` if that has no `.git` (:60). From a worktree `$Root` is
  `.../.claude/worktrees/wt-<id>`, so it looks under `.claude/worktrees/` and
  throws. `-Store <abs path>` gets past this.
- `$IndexPath = Join-Path $Root 'documentation/implementation-guide/README.md'`
  (:62), the index the seam check reads. `-Store` does NOT redirect this.
  Invoked by absolute path with the CWD in the operator's main checkout, it
  audits the MAIN checkout's index — so a branch that adds a status row is
  scored against an index that does not have it yet, and the run exits 1 with
  `plan store feature '<name>' has NO status row in the index` (:89).

So the working combination is **CWD inside the worktree AND `-Store` pointing at
the real store**. Every agent the harness runs works in a worktree, and CLAUDE.md
tells every planning session to run this script at start and end, so the default
is wrong for the population told to use it. Resolving `$Root` from
`git rev-parse --git-common-dir` (or from `$PSScriptRoot`) would close both
halves; that is a change to a check script and out of scope for a docs item.
[source: scripts/checks/plan-store.ps1:56-62, :89] [live: exit 0 from
`wt-sl-closeout` with CWD in the worktree,
2026-09-19; the exit-1-from-the-wrong-CWD half was observed by the tester
(wt-tester-closeout) on the same day, not re-run here]

## 3. Three more `.env.example` variables that nothing reads

Same class as the four cron variables this item removed, but not named in the
audit, so they were left alone:

    .env.example:370  BACKUP_INTERVAL=86400
    .env.example:371  RETAIN_COUNT=2
    .env.example:372  MIN_AGE_SECS=82800

(line numbers on this branch after the 2026-09-19 rebase onto 9f64b84.) These
are the **container-side** names. Every sidecar sets them itself in its own `environment:` block from a prefixed
host variable — e.g. `- BACKUP_INTERVAL=${MNEMORY_BACKUP_INTERVAL:-86400}`
(`memory/docker-compose.yml:111`), `- RETAIN_COUNT=${OPENWEBUI_BACKUP_RETAIN_COUNT:-2}`
(`frontend/docker-compose.yml:210`) — and `backup/generic-tar-backup.sh:25,27`
reads them from inside the container. No compose file interpolates the bare
names from the root `.env`, so setting them there changes nothing. They belong
in the `sl-env-split` sweep.
[source: grep for `${BACKUP_INTERVAL`, `${RETAIN_COUNT`, `${MIN_AGE_SECS` across
every compose file returns only `$$`-escaped, container-side uses]

## 4. The audit overstated how empty the plane directories are (L.1)

The note says the plane dirs "contain only a compose file — [verified]".
`memory/`, `search/` and `coder/` each also hold a `README.md`, and `portal/`
holds `local-test.override.yml`. The substance of L.1 is unaffected — no plane
directory contains its service SOURCE — but the next reader should not expect
four bare directories.

**Overtaken while this item was in review (2026-09-19):** `sl-colo-portal`
(adfd9f2) moved the portal's eight config trees into `portal/config/`, and
`sl-inference-split` (9f64b84) added `inference/compose/` with four included
service-group files. So the listing keeps moving, which is L.1 happening rather
than an objection to it — do not cite a plane-directory listing as a stable fact.
[source: `ls` of each plane directory, 2026-09-19, re-run after the rebase onto
9f64b84]

## 5. `agent-org/README.md`: 865 test functions, counted with the AST — and why grep said 866

**Corrected 2026-09-19: the first fix shipped 866, which is wrong, and it was
wrong for the reason this whole item exists.** The number came from the audit
note's `[agent]` line and was reproduced with `grep -c "def test_"` instead of
being counted from the code. Grep counts TEXT, so it counts prose: the 866th hit
is a sentence inside `tests/test_p18_observation.py:392` ("...branch has a stable
44 `def test_`, and the honest 44 next round read as a regression..."), which is
documentation, not a definition.

The count in the README is now **865**, obtained by parsing every file under
`agent-org/agent-bridge/tests/` and counting `ast.FunctionDef` /
`ast.AsyncFunctionDef` nodes whose name starts with `test_`:

    98 files · 109 sync + 756 async = 865 · all module-level · 865 unique names

The README states the method ("AST count, 2026-09-19") rather than a bare number,
so the next reader can reproduce it and knows what it is not. `tests/` is the
right scope because `pyproject.toml:23-25` sets `testpaths = ["tests"]`.

Two further corrections to what this entry said before:

- It claimed "two further `def test_` live outside that directory". They are not
  definitions either — both are COMMENT lines in
  `agent-org/agent-bridge/app/orchestrator.py:1403,1432`, found by the same grep
  and misread the same way.
- What `pytest -q` reports is still expected to be HIGHER than 865, because a
  parametrized function collects once per case. The suite was NOT run (it needs
  `pip install -e .[test]`), so the README does not claim to state pytest's
  number.

**The lesson, which is the reusable part:** a test count taken with `grep` is a
count of a string, and any test suite that writes about testing will contain that
string in prose. Use the AST. The same applies to counting services, probes or
volumes from text — T2's pre-commit recount survived only because the thing being
counted (`powershell.exe -NoProfile`) does not appear in the file's prose.
[source: AST parse of agent-org/agent-bridge/tests/*.py, 2026-09-19;
agent-org/agent-bridge/pyproject.toml:23-25;
agent-org/agent-bridge/tests/test_p18_observation.py:392;
agent-org/agent-bridge/app/orchestrator.py:1403,1432]
[unverifiable here: the collected count pytest would report]

## 6. OB1's "~29 containers" is 24 services (left uncorrected, deliberately)

`README.md:26` and `CLAUDE.md:22` both say "~29 containers" for the Open Brain
project; `OB1/docker/docker-compose.yml` declares 24 services. The audit filed
this under "Cosmetic" rather than in findings 1-17, so this item did not touch
it — `sl-readmes` owns the plane/README prose.
[source: count of top-level `  <name>:` keys under `services:` in
OB1/docker/docker-compose.yml]

## 7. Two standalone-OWUI blockers this item could not fix

Both were named in the audit's last section as `stack-layers` wave-1 work, and
one of the two landed while this item was in review:

- The literal `C:\Users\yamao\.lmstudio\models` bind that CLEANUP-PLAN D.4
  missed (its sweep grepped only for `D:/`) — `sl-inference-split` owns the
  `LM_MODELS_DIR` change. Re-check it against `inference/compose/upstreams.yml`
  and `inference/compose/backups.yml` now that the plane is split, not against
  the old single file.
- `config/` no longer mixes the two planes: `sl-colo-portal` (adfd9f2) moved the
  portal's trees into `portal/config/`, so what remains at the repo root is the
  inference set (`litellm.config.yaml`, `litellm.ui.config.yaml`,
  `llama-swap.config.yaml`, `chat-template.jinja`, `litellm/`).
  `sl-colo-inference` is the item that moves those.

[source: `ls config/` and `ls inference/compose/`, 2026-09-19, after the rebase
onto 9f64b84]

## 8. The acceptance check constrains the wording of the removal comment

The anchor's executable check is
`! grep -q smolcrawl-data docker-compose.yml`, which fails on ANY occurrence of
the string — including a comment explaining why the declaration was removed.
The explain-your-removals rule and the check therefore pull against each other,
and the comment now in the anchor file describes the volume ("the orphan
crawl-index volume left behind when smolcrawl-pipelines was retired") without
naming it. Worth knowing for the next anchor that pairs a `grep -q` check with a
removal.
[source: anchors/sl-closeout.json acceptance[2]; docker-compose.yml:26-32]

## 9. `SECURITY.md` still routes a live deferred item through the closed plan

Swept after the reviewer rejected attempt 1 for exactly this shape: marking
`CLEANUP-PLAN.md` CLOSED without fixing the documents that point AT it as live.
Two pointers were inside the artifact list and are fixed on this branch
(`CLAUDE.md`'s Pointers entry, `README.md`'s repo-map row), and one more in the
same class was fixed because it named a decision that has moved
(`scripts/README.md:15`, `stack-services.json` "wire-or-demote = CLEANUP-PLAN
D-12" — D-12's generator half is now `sl-driver-parity`).

One remains and is NOT fixed here, because `SECURITY.md` is not in this item's
artifact list:

    SECURITY.md:24  "...scrub = CLEANUP-PLAN D-1, deferred"

D-1 (the git-history credential scrub) really is still deferred, so the sentence
is true — but it sends the reader to a file whose new header says it is no
longer a worklist, and the "v3 CLOSED" ledger covers D-1 only implicitly, inside
the Part A row about the declined rotation. Either `SECURITY.md` should name the
posture itself as the live record (it already is: §0 and §24-33), or the ledger
should name D-1 explicitly. A one-line edit for whoever owns `SECURITY.md` next;
`stack-layers` DECISIONS D2 is the decision it hangs off.

`SECURITY.md:7` ("2026-08-20 posture changes (CLEANUP-PLAN v3 execution day)")
and `CLAUDE.md:27` ("Retired 2026-08-20 (CLEANUP-PLAN v3)") are historical
attributions, not live pointers, and are correct as they stand.

**The generalisable part:** closing a plan is two edits, not one — the plan's own
status, and every pointer that describes it. The first without the second
produces a document that calls itself closed and a repo that still routes
newcomers to it, which is worse than leaving it open. `git grep -n -i "living"`
over the root docs is the cheap version of the sweep; `git grep -n CLEANUP-PLAN`
is the thorough one.
[source: git grep -n CLEANUP-PLAN over README.md, CLAUDE.md, SECURITY.md,
scripts/README.md, agent-org/README.md and documentation/implementation-guide/README.md,
2026-09-19]

## 10. `UPDATE-MANAGEMENT.md` still names Watchtower as the stack's auto-updater

The nineteenth survivor of the same shape, found by the reviewer of attempt 2.

    documentation/runbooks/UPDATE-MANAGEMENT.md:7-9
    "Everything is **manual and verified** - the only auto-updater is
     Watchtower, scoped to the `openwebui` image and pending retirement
     (CLEANUP-PLAN v3, decision D-2)."

Watchtower was RETIRED on 2026-08-20, not pending retirement. There is no
`watchtower` service, image or `container_name` in any compose file in this
workspace; what survives is the opt-out LABEL
`com.centurylinklabs.watchtower.enable=false` on many services (harmless, and
documentation of the decision), one OB1 comment explaining a deliberate absence
of that label, and an archived override under `scripts/archive/`. `CLAUDE.md:27`
records the retirement correctly, so the runbook contradicts the file every
agent reads first.

Not fixed here: `documentation/runbooks/UPDATE-MANAGEMENT.md` is outside this
item's artifact list. The fix is one sentence - say the stack has no
auto-updater, that Watchtower was retired 2026-08-20, and that the surviving
labels are inert.
[source: grep for `watchtower` across every `*.yml` in the workspace,
2026-09-19; documentation/runbooks/UPDATE-MANAGEMENT.md:7-9; CLAUDE.md:27]

## 11. `config/litellm.ui.config.yaml` still calls the main gateway PERMISSIVE

Audit finding 1's class, in a file the finding did not name and the artifact
list does not cover:

    config/litellm.ui.config.yaml:10
    "Setting a master_key on the PERMISSIVE main gateway would enforce auth on
     every ... no-key path"

The main gateway has not been permissive since the J.1 flip on 2026-08-21;
`config/litellm.config.yaml` sets `master_key` under `general_settings`. The
sidecar's REASON for existing is still sound (LiteLLM 1.88.1 hard-requires a
master key for the admin UI), so only the parenthetical about the main gateway
is stale - but it is the same false statement this item corrected in two other
files, and someone reading it will draw the same wrong conclusion.

Not fixed here: outside the artifact list. It belongs with `sl-colo-inference`,
which moves that file into `inference/config/` anyway.
[source: config/litellm.ui.config.yaml:8-17; config/litellm.config.yaml
general_settings, 2026-09-19 after the rebase onto 9f64b84]

## 12. The rebase dropped this branch's portal header hunk on purpose

Recorded because a reader diffing this branch will find audit finding 12
corrected by a commit that is not ours.

`development` and this branch fixed the same false header
(`portal/docker-compose.yml`, "Networks/volumes are defined in the root file")
in different words, and the rebase onto 9f64b84 conflicted there. This branch's
hunk was DROPPED and `development`'s kept, on the reviewer's recommendation,
because `sl-colo-portal` (adfd9f2) rewrote the surrounding paragraph anyway to
describe the new `portal/config/` tree - keeping our wording would have
reintroduced a header that no longer matched the file's mounts. The finding is
closed either way; only the authorship moved.

`git diff development -- portal/docker-compose.yml` is therefore EMPTY on this
branch, and `portal/docker-compose.yml` stays in the anchor's artifact list
while not appearing in the diff.

Two citations moved with that rebase and were corrected here rather than in the
audit note, whose body is the dated record of what the audit said:
`portal/docker-compose.yml`'s header is now at :13-14 (the audit cites :10), its
`networks:` at :592 and `volumes:` at :611 (the test plan cited a 585-615
window). The `.env.example` comments that cited
`inference/docker-compose.yml:383,413,420-421` and "service llm-gateway-backup"
now cite `inference/compose/backups.yml:41,75,82-84` and `:13,31`, because
`sl-inference-split` moved both sidecars into the included file.
[source: the rebase conflict itself; portal/docker-compose.yml and
inference/compose/backups.yml as of 2026-09-19]
