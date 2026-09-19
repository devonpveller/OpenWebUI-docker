# Findings: wiki-mirror-hardening (2026-09-03)

Findings sink for harness item `wiki-mirror-hardening`. The deliverable is the
5b pre-commit gate + OB1 `5224928` (per-kind warnOnce) + the SUPERSEDED stamps;
everything below is true but outside that artifact. Each claim says how it was
checked.

## 1. Under a git hook, GIT_DIR overrides `git -C <submodule>` — silently

The gate's first live run refused a CORRECT commit, claiming the OB1 working
tree was at the parent's HEAD. Cause: hooks run with GIT_DIR exported
(absolute — in a linked worktree, the parent's worktree admin dir) and
GIT_INDEX_FILE set; both override `-C`, so `git -C OB1 rev-parse HEAD`
answered from the PARENT repo. Interactive runs of the same script were
correct all five test paths — only the hook environment reproduces it.
Fixed in the gate (submodule queries clear GIT_DIR/GIT_WORK_TREE/
GIT_INDEX_FILE; parent queries keep them, since GIT_INDEX_FILE is the very
index being committed). Verified by the refusal output (parent SHA named as
OB1's) and by the green hook run one commit later.

**CORRECTED 2026-09-03 (reviewer catch — the original sentence here was
false):** this note first claimed `grep -rn "git -C" .githooks scripts/checks`
returned nothing else. Running that exact grep returns `.githooks/commit-msg:58`,
which shells `git -C` into the staged submodule from hook context and ALREADY
solves this same GIT_DIR trap (with an sh-subshell `unset`, documented at its
lines 46–57 with the same "found on the first real run" provenance). So the
trap was prior art 40 lines from where the gate rediscovered it — the lesson
is doubled: clear the hook env when a hook script reads a submodule, and
never record a negative grep result without running it and reading every hit.

## 2. A RED probe for a test-gate must be COMMITTED, not just dirty

Planned RED proof was "break a test file, watch the gate refuse on the
failing test". It refuses earlier: a dirty tracked file trips the
dirty-tree check before any test runs. The honest RED proof needs the
broken test committed in a scratch OB1 branch (done: scratch commit
`1c78cce`, gate refused naming `deliberately-broken-gate-probe`, branch
deleted after). Testers copying the acceptance steps verbatim will hit
this; the queued test plan spells out the scratch-commit route.

## 3. `git stash` inside OB1 as cleanup is a foot-gun in probe sequences

A `stash --include-untracked` run between probe steps swallowed the probe
edit itself (then required a deliberate `stash drop`). Not a defect
anywhere; recorded because probe cleanup inside the SUBMODULE leaves no
trace in the parent and is easy to lose track of mid-sequence.

## 4. Two near-same-name OB1 branches now exist on the remote (by design)

`fix/wiki-pages-extractlinks` (09f70f4, landed) and
`fix/wiki-pages-warnonce-per-kind` (5224928, landed) — plus the PARKED
`fix/wiki-pages-extractlinks-binding` (9b47135, bundle only, superseded per
the stamps this item adds). Branch deletion on the OB1 remote remains the
operator's call (anchor out-of-scope).
