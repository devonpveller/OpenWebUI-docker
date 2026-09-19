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

## 2. `scripts/checks/plan-store.ps1` cannot run from a git worktree

It derives the plan store as `<repo root>/../documentation-plans-ai-stack`
(`plan-store.ps1:59`). Inside a harness worktree the repo root is
`.../.claude/worktrees/wt-<id>`, so it looks for the store under
`.claude/worktrees/` and throws at :60. It exits 0 when given
`-Store "D:\Open WebUI\documentation-plans-ai-stack"` explicitly. Every agent
run by the harness works in a worktree, and CLAUDE.md tells every planning
session to run this script at start and end — so the default path is wrong for
the population that is told to use it. A one-line fix (`git rev-parse
--git-common-dir` to find the main checkout) would close it; that is a change to
a check script and is out of scope for a docs item.
[source: scripts/checks/plan-store.ps1:57-60] [live: both invocations run
2026-09-19 from `wt-sl-closeout`]

## 3. Three more `.env.example` variables that nothing reads

Same class as the four cron variables this item removed, but not named in the
audit, so they were left alone:

    .env.example:337  BACKUP_INTERVAL=86400
    .env.example:338  RETAIN_COUNT=2
    .env.example:339  MIN_AGE_SECS=82800

(line numbers after this item's edits.) These are the **container-side** names.
Every sidecar sets them itself in its own `environment:` block from a prefixed
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
four bare directories. `frontend/` and `inference/` are the two that really do
hold one file each, which is also why they are the two planes with no README.
[source: `ls` of each plane directory, 2026-09-19]

## 5. `agent-org/README.md`: 866 is a count of `def test_`, not of collected tests

The README's two "55 tests" claims were corrected to 866, which is the number of
`def test_` definitions under `agent-org/agent-bridge/tests/` (the directory
`pyproject.toml:25` sets as `testpaths`). Two further `def test_` live outside
that directory and are not collected. What `pytest -q` actually reports may be
HIGHER, because parametrized tests collect once per case; the suite was not run
here (it needs `pip install -e .[test]`).
[source: agent-org/agent-bridge/pyproject.toml:23-25; grep count]
[unverifiable here: the collected count]

## 6. OB1's "~29 containers" is 24 services (left uncorrected, deliberately)

`README.md:26` and `CLAUDE.md:22` both say "~29 containers" for the Open Brain
project; `OB1/docker/docker-compose.yml` declares 24 services. The audit filed
this under "Cosmetic" rather than in findings 1-17, so this item did not touch
it — `sl-readmes` owns the plane/README prose.
[source: count of top-level `  <name>:` keys under `services:` in
OB1/docker/docker-compose.yml]

## 7. Two standalone-OWUI blockers this item could not fix

Both are named in the audit's last section and both are `stack-layers` wave-1
work, not doc fixes:

- `inference/docker-compose.yml` binds `C:\Users\yamao\.lmstudio\models`
  literally; CLEANUP-PLAN D.4 missed it because that sweep grepped only for
  `D:/`. It becomes `LM_MODELS_DIR` in `sl-inference-split`.
- `config/` mixes portal config with inference config in one directory, which is
  what `sl-colo-portal` and `sl-colo-inference` separate.

[source: inference/docker-compose.yml:34,394; `ls config/`]

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
