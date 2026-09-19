# Quadrant comparison - item `u4-baseline`

**COMPARED 4/4**

Item digest `c585bee6fee3043c` - every record below was checked against it, so a result from a different item cannot appear in this table.

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
| little-coder x self | completed | 2/2 | 65.5 | 1/2/0 | 1 changed | mechanical (container, git-proxy, egress allowlist) |
| little-coder x project | completed | 2/2 | 65.8 | 1/2/0 | 1 changed | mechanical (container, git-proxy, egress allowlist) |
| claude-code x self | completed | 2/2 | 35.4 | 1/2/0 | 1 changed | normative (protocol rules) - A7: FALSIFIED as enforcement |
| claude-code x project | completed | 2/2 | 35.2 | 1/2/0 | 1 changed | normative (protocol rules) - A7: FALSIFIED as enforcement |

## Decision view

| quadrant | acceptance | cost (wall s / USD / tokens) | taps | confidence |
|---|---|---|---|---|
| little-coder x self | 2/2 | 65.5 / null / null | 0 | n=1 - not a basis for a decision |
| little-coder x project | 2/2 | 65.8 / null / null | 0 | n=1 - not a basis for a decision |
| claude-code x self | 2/2 | 35.4 / 0.4739 / 2351 | 0 | n=1 - not a basis for a decision |
| claude-code x project | 2/2 | 35.2 / 0.4635 / 2342 | 0 | n=1 - not a basis for a decision |

`null` cost means UNMEASURED, not free - a runner that does not report a figure gets no figure invented for it.

## What this comparison cannot tell you

Every configured quadrant produced an admitted outcome.

Sample size: every cell above is a single run unless its confidence column says otherwise. Below n=2 the harness cannot separate a quadrant's behaviour from one run's luck, and it does not pretend to.
