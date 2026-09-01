# Quadrant comparison - item `u4-stall`

**COMPARED 1/4**  - this comparison is INCOMPLETE; the quadrants that did not run are listed below with the reason each did not.

Item digest `42b3f17a5d67c8e1` - every record below was checked against it, so a result from a different item cannot appear in this table.

**Venue: `gym` (kind `gym`) - DECLARED to satisfy a "Gym:" column.** This is what `quadrant/schema.json`'s `venue_kinds.gym` says the kind is worth; it is a configuration assertion, not a measurement.

`D:\Open WebUI\ai-orchestration-gym` @ `main` (via config quadrant.venues.gym.repo) - repository identity `root:f12ba2ecd0ed02c30ce3fa32e1dbe4b8ae7bf31d`.

**CHECKED** by `quadrant/venue.py` before any cell ran:
- the venue path is a git repository ROOT (git discovers upward, so a wrong path otherwise silently adopts whatever repository encloses it)
- it is NOT the harness's own repository (git common dirs compared, so a worktree of it is recognised as it)
- the ref resolves to a commit in it
- the repository IDENTITY (root commit reachable from that ref) is recorded with every record in this set, so a record from a different repository is refused however it is labelled

**NOT CHECKED** - stated because a verdict must not imply more than it derived:
- whether this repository is a DISPOSABLE ARENA rather than a real target. No probe can decide that: a repository holding something precious answers every question above the same way. `kind: "gym"` is an ASSERTION in harness.config.json, and "satisfies a Gym: column" follows from that assertion, not from a measurement
- whether the work inside it was confined to the venue - see the per-target note below, since a `project` cell's subject is a scratch repository, not this one

Rendered from the venue PINNED with this results set, not from the configuration in force today. Every record above was admitted only if its venue matched that pin - by repository identity where both sides carry one, otherwise by every label they carry (which is weaker, and the refusal says so).

**What the venue constrains, per target.**
- `target: project` - the workspace is a FRESH `git init` scratch repository created for this run under the results directory, holding only the planted item - it is NOT in the venue repository, and its git history is one commit long. That is legitimate under PLAN section 2's preamble, which forbids "live planes or a real target": a per-run scratch repo is neither, and the venue is what the cell was DRIVEN from rather than what it worked on. It is stated because the venue heading above would otherwise be read as a claim about this row's working tree too.
- `target: self` - the workspace IS a detached worktree of the venue repository at the venue's ref. The venue above is this cell's subject, and the pin is a fact about it.

## Outcome

| quadrant | status | acceptance | wall s | rounds (dispatch/cycles/taps) | scope | containment |
|---|---|---|---|---|---|---|
| little-coder x self | NOT RUN | - | - | - | - | mechanical (container, git-proxy, egress allowlist) |
| little-coder x project | failed | 1/2 | 66.5 | 1/2/0 | 1 changed | mechanical (container, git-proxy, egress allowlist) |
| claude-code x self | NOT RUN | - | - | - | - | normative (protocol rules) - A7: FALSIFIED as enforcement |
| claude-code x project | NOT RUN | - | - | - | - | normative (protocol rules) - A7: FALSIFIED as enforcement |

## Decision view

| quadrant | acceptance | cost (wall s / USD / tokens) | taps | confidence |
|---|---|---|---|---|
| little-coder x project | 1/2 | 66.5 / null / null | 0 | n=3; repeats agree |

`null` cost means UNMEASURED, not free - a runner that does not report a figure gets no figure invented for it.

## What this comparison cannot tell you

- **little-coder x self** - NOT_RUN: no record produced - this quadrant was never attempted
- **claude-code x self** - NOT_RUN: no record produced - this quadrant was never attempted
- **claude-code x project** - NOT_RUN: no record produced - this quadrant was never attempted

Sample size: every cell above is a single run unless its confidence column says otherwise. Below n=2 the harness cannot separate a quadrant's behaviour from one run's luck, and it does not pretend to.
