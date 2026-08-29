# MERGE-PROTOCOL — how parallel agents land work without wrecking each other

**Status:** LIVE (2026-08-28). The tooling this references is built and verified:
`scripts/agent-harness/{new-worktree,sync-worktree-env,remove-worktree,lease}.ps1`.

**Audience:** any Claude session — VS Code extension or Mattermost bridge — that is
about to change this repo. Read this before your first `git add`, not after your
first conflict.

---

## 0. The one-paragraph version

**Agree what the work is for before you build it.** Write an anchor - goal, artifact,
audience, acceptance criteria, what is out of scope - propose it, and wait for the operator
to confirm it. Then work in your own worktree. When it is ready, write the test plan and
**queue it** - you do
not test or merge your own work. A tester who did not write it executes that plan and
records evidence. A reviewer who did not write it reviews the diff, rebases onto the work
line, and merges. If the rebase changes what was tested, the pass is stale and the item
goes back to test rather than through. Conflicts are adapted by whoever lands later; if
adapting would defeat another agent's goal, talk to them before resolving, and if you
cannot agree, ask the operator.

**Why the anchor comes first:** the first real run of this pipeline shipped two READMEs
that passed every check and were still not what was asked for - one was 46% defect log. The
tests verified that each claim was TRUE; the review verified that each claim was SUPPORTED;
nothing asked whether the artifact was the thing requested. A pipeline that validates
correctness and never validates intent will reliably ship a correct answer to the wrong
question. The anchor is what the tester tests against and the reviewer judges against, and
it outlives the prompt - which lives in one agent's context and is gone by the time anyone
else needs to know what "done" meant.

**Why there is no lock in that paragraph:** a worktree already isolates files, and git
already refuses two worktrees on one branch. The coordination that actually matters is
*separation of duties*, which is a pipeline, not a mutex. Leases exist only for the shared
runtime a worktree cannot isolate - see §1.

## 1. Before you touch anything

### 1.0 The anchor - agree the goal before building toward it

Copy `scripts/agent-harness/anchor.template.json`, fill it in **in the operator's terms**,
and propose it:

```powershell
.\scripts\agent-harness\queue.ps1 -Propose -Id <short-id> -Anchor <your anchor.json> -Developer wt-<short-id>
```

Then stop. `-Submit` refuses (exit 5) until the operator runs `-ConfirmAnchor`. If you had
to guess at what they meant, the anchor is where you say so - it is the cheapest moment in
the whole pipeline to be corrected.

Six fields, each earning its place:

| field | what it pins down |
|---|---|
| `goal` | one sentence, the outcome in their words - stops goal drift across test cycles |
| `artifact` | path and kind - "a README" versus "a report" is a decision, not a style |
| `audience` | who reads or runs it - the field that would have prevented the coder README |
| `acceptance` | objectively checkable criteria - **this is what the tester tests** |
| `out_of_scope` | the tempting adjacent work this deliberately does not do |
| `findings_sink` | where anything true but out of scope gets written down instead |

The tool refuses vague anchors: a missing field, an empty list, or an acceptance criterion
too short to check. Say what would count as FAILING each criterion, not only what passing
looks like.

### 1.1 Your worktree

```powershell
.\scripts\agent-harness\new-worktree.ps1 -Id <short-id> -OwnerKind extension -OwnerRef <session>
# then EITHER:  EnterWorktree path: <the path it prints>   (moves your session there)
# OR simply:    git -C <the path it prints> ...          (works from anywhere)
```

`EnterWorktree` is a convenience, not the mechanism - it is unavailable to some session
kinds (a subagent with a pinned cwd was refused outright). If it does not work, use
`git -C <worktree>` and absolute paths; what must hold is that your commits land in the
worktree and the main checkout is never mutated, and neither depends on your cwd.

The script prints your **work line** - the branch you branched from and will land on. It
defaults to whatever the main checkout currently has loaded, so agents inherit the tooling
and docs on that branch; override with `-Base` or `AI_STACK_WORK_LINE`.

This is not optional politeness — a bare `git worktree add` is broken here in three
verified ways (no `.env`, empty `OB1/`, wrong base branch), and a shared checkout is
how one session's `git add` swept another's staged gitlink on 2026-08-23.

Rules while you work:

- **Stage by path, never `git add -A`.** Other agents' files are dirty in *their*
  trees, but your worktree can still see stray files; sweeping is how accidents
  happen.
- **Never `cd` into the main checkout to mutate it.** It is the operator's.
- **Testing runs under plane leases, not cloned environments.** Before any test that
  *mutates* a plane or *needs it stable to trust the result*, hold that plane's
  lease; a read-only probe (curl a health endpoint) needs none:

  ```powershell
  .\scripts\agent-harness\lease.ps1 -Acquire -Name open-brain -Owner <your-wt-id> -Thread <mm-thread>
  #   ... test ...
  .\scripts\agent-harness\lease.ps1 -Release -Name open-brain -Owner <your-wt-id>
  ```

  Lease names = the compose planes (`lease-names.conf`): `inference`, `memory`,
  `search`, `coder`, `frontend`, `open-brain`, `agent-org`, `portal`. Rules:
  1. A multi-plane test requests all names in **one call**
     (`-Name "frontend,open-brain"`) — sorted, all-or-nothing, so two agents
     cannot deadlock.
  2. **When unsure which planes you touch, widen.** Your announce post (step 5)
     names the leases you held; under-declaring is visible in the post-mortem.
  3. Exit 3 = held: wait, poll ≤1/min. Long build? `-Refresh` keeps it alive.
  4. **Editing files that belong to a plane needs no plane lease** - only touching the
     RUNNING plane does. Read-only inspection of a running plane (`curl` a health endpoint,
     `docker ps`/`port`/`inspect`, a read-only `docker exec`) is not "touching" it either -
     nothing changes, so nothing needs serialising. Anything that mutates, restarts,
     rebuilds or retags does. A docs, config or source change you do not deploy is the
     read-only case: queue it for test and review, and take no lease at all.
  5. Leave the plane the way you found it *before releasing* — a lease serializes
     interference, it does not clean up after you. Test data stays test-prefixed
     (`testing-*`), test images tag `:wt-<id>` and never touch `:local` (retagging
     IS the deploy, which stays gated).
- **Never mutate prod containers, `:local` tags, or prod volumes** — that is a
  *deploy*, a separate gated step. Where a container-level sandbox is cheap, use
  the proven sidecar shape (`docker run --entrypoint ... ` on a private/no
  network) — but never attach a test container to the `ai-stack_*` anchor
  networks (it would hijack service aliases and block emergency recovery), and
  never reuse a prod `container_name` (the watchdog inspects by name).
- If your run spans hours, `.\scripts\agent-harness\sync-worktree-env.ps1 -Id <id>` picks
  up any `.env` change the operator made meanwhile.

## 2. Landing it - the pipeline

`scripts/agent-harness/queue.ps1` carries the state a pull request would carry; the diff is just
`git diff <work line>...work/<your-id>`. It is local on purpose: a GitHub PR would need
branches pushed (against this repo's push policy), the `gh` CLI (absent), and network.

**Step 1 - validate in your own worktree.** `ruff check .`, plus a compose `config` render
for any plane **whose compose file or env you changed** (editing a plane's *other* files is
not touching it - see §1 rule 4; the two senses of "touch" collided here until a developer
called it out). A queue submission is not the place to discover your branch is red.

**What "evidence" means depends on the change.** For behaviour, it is RED to GREEN: a repro
that failed before your change and passes after. **For documentation and other non-behavioural
work there is no repro to fail** - do not invent theatre. What such a change owes instead is
*verification against the source of truth*: every claim checked against the file it describes,
and links proven to resolve.

**Where disagreements go.** Checking claims against the tree turns up real drift between
documents and reality. That is valuable and it does **not** belong in the deliverable - the
anchor names a `findings_sink` for exactly this, and anything true but out of scope is
written there. This protocol used to say to call such disagreements out "explicitly", with
no destination; agents reasonably read that as "in the artifact", and half of one README
became a defect table. Neither losing the finding nor pasting it into the deliverable is
right; the sink is the third option.

**The sink is held to the artifact's standard.** A false claim in a findings file is not a
smaller mistake than a false claim in the deliverable - it is a larger one, because the
sink is what the NEXT item reads and acts on. A real case: a findings entry said a recovery
script "never brings the anchor up at all", written from a function's comment without
opening the call sites; two of the three recovery paths do exactly that, each with a
comment saying so. Acting on that entry would have meant adding something that already
existed twice. **Any claim about what a script does is verified against that code path, not
against its comments** - the same lens the test plan applies to the artifact.

**Label each entry with how you know it.** A findings file mixes three kinds of claim and
they are not equally strong: *read from source* (name the file and line), *observed live*
(say when, and that it has not been re-run since), and *not verifiable from this tree*
(a third-party binary's behaviour, an image's internals - say so and tell the reader to
check before acting). A developer arrived at this after a tester failed it twice, and it is
worth more than the rule it satisfies: an entry that states a conclusion more confidently
than its own provenance supports is the failure, and labelling makes that visible instead
of leaving it to care.

Two ground rules the same item paid for three times over:

- **Read to the end of the function.** Three consecutive attempts described one script
  wrongly, each from stopping early - at a sibling's behaviour, at a function's comment,
  then at the top of a `try` block. A claim about what code does is verified by reading the
  whole body it lives in.
- **Check the stated REASON, not only the claim.** The third failure had a true headline
  ("a minimal run never touches the anchor") attached to a false mechanism ("it only issues
  `restart` against existing containers" - it issues six `up -d`). A right answer for a
  wrong reason misleads the next reader exactly as much as a wrong answer.

**And a sink entry is not an obligation.** If a finding cannot be stated at the strength you
actually verified, narrow it to what you did verify or drop it. An entry that keeps needing
correction is usually reaching past what the item could responsibly check - silence there
costs nothing, and a confident wrong entry costs whoever acts on it.
Say which of the two you are offering; a documentation item claiming a RED to GREEN pass is a
worse signal than one that says plainly what it checked.

**Step 2 - write the test plan, then submit. This is the developer's last action.**

```powershell
.\scripts\agent-harness\queue.ps1 -Submit -Id <id> -Branch work/<id> -Developer <your-wt-id> -TestPlan <path>
```

The plan is written *before* the work is queued, because it is what someone else will
execute. "I tested it myself" is not a plan, and the tool refuses a submission without one.
List the **cases** - for each, what to run, what counts as passing, and what would count as
failing. A plan that cannot fail is not a plan.

**Cases that keep earning their place** (each was the case a real plan was missing, found
by a tester after every case in that plan had passed):

- **For every command the artifact tells a reader to RUN, check the description of its
  effects against the script that implements it.** A plan can verify exhaustively that a
  document matches a compose file and still never ask what the document's headline command
  actually does. That gap shipped a README whose first executable line would fail on a cold
  host, with a sentence beside it promising it would not.
- **Diff what was REMOVED, not only what is present.** "Nothing true was silently lost" is
  only checkable against the previous version plus wherever the findings went.
- **If the work shows an ACCEPTANCE CRITERION is itself wrong, say so in the plan.** Do not
  quietly write a case that tests the criterion's intent instead of its wording - that
  routes around a disagreement the operator is entitled to decide. A real case: a criterion
  required a README to state that a readiness endpoint "walks vpn+searxng+redis"; reading
  the code end to end showed it does not, so meeting the criterion literally would have
  meant regressing to a false statement. The plan tested the intent and did not declare the
  conflict, which left it to be discovered mid-review. Declare it, and let the gate decide
  between amending the anchor and accepting intent over wording.
- **Read the blob, not the working tree** (`git show <branch>:<path>`). `core.autocrlf`
  makes every text file CRLF locally, and the operator's checkout may hold uncommitted
  edits that are not on your branch - score the branch against the branch.

Expect cycles. A case failing is the plan doing its job: the tester reports what it revealed,
you fix the finding in the same worktree, and `-Resubmit` starts the next attempt on the same
item so the history stays in one place. Tests are not a turnstile in front of review - an
involved change that finds nothing on the first pass is a reason to doubt the plan.

**Step 3 - a TESTER (not you) claims it and executes the plan.**

```powershell
.\scripts\agent-harness\queue.ps1 -Claim -Id <id> -Role tester -By <their-id>
#   ... run the plan. Touching a plane's RUNNING services? Hold its lease first ...
.\scripts\agent-harness\queue.ps1 -Pass -Id <id> -By <their-id> -Evidence <what ran, what it produced> -PlanAdequate
.\scripts\agent-harness\queue.ps1 -Fail -Id <id> -By <their-id> -Reason <what broke>
```

`-Evidence` is required on **both** verdicts, and may be a path to a file - long evidence
does not fit a PS5.1 argument, and it is copied beside the item. A failure is exactly when
the next person needs your evidence most.

One of `-PlanAdequate` / `-PlanInadequate` is **required** on both verdicts. It is a
judgement, not a formality: the plan was written by the developer, so
a tester who only reports pass/fail is grading an exam without reading the syllabus. If the
plan missed cases, run the ones you think are missing, say so, and withhold the flag - a
plan that missed the finding is itself a finding. A failure returns the item to the
developer, who fixes it **in the same worktree** and `-Resubmit`s.

**Executing the plan is the floor, not the ceiling.** Both testers in the first real run
passed every case as written and then failed the item on something the plan never asked
about. If the change makes claims the plan does not check, check them.

**A plan must never ask a tester to run a command whose SAFETY depends on an unverified
flag.** A real near-miss: a case said to run `docker compose build --dry-run`, without the
plan having established that `build` honours `--dry-run`. Had it not, the tester would have
performed a real `--no-cache` rebuild and overwritten a `:local` tag - a gated deploy, and
the exact thing that case's own item was forbidden from doing. The tester checked `--help`
first and it was supported; that was its caution, not the plan's. If a case is safe only
because of a flag, the plan proves the flag works before the case uses it.

**Two things a plan must not do**, both found in real plans on the first run: send you to
`cat` a `docker compose config` render while forbidding you to print `.env` values (the
render interpolates secrets in plaintext - grep sections instead), and check line endings on
a working-tree file where `core.autocrlf=true` makes every text file CRLF regardless of
`.gitattributes` (read the blob with `git show <branch>:<path>`). If a case would make you
break the plan's own rules, that is a finding about the plan.

**Step 4 - a REVIEWER (not the developer) claims it, judges it against the anchor, rebases,
and re-checks staleness.**

Claiming prints the confirmed anchor. **Read it before you read the diff** — it is the
context for the judgement, not the judgement itself. Your first question is not "is this
correct" (the tester already asked that) but *"does this belong in this codebase?"* — right
module, house patterns followed, tree still coherent afterwards. Green tests plus code that
does not belong is a rejection, and the tool will not let you merge without saying which:

```powershell
.\scripts\agent-harness\queue.ps1 -Merged -Id <id> -By <their-id> -Sha <sha> -FitsCodebase
.\scripts\agent-harness\queue.ps1 -Reject  -Id <id> -By <their-id> -Misfits -Reason "<what misfits>"
```

`-Misfits` on a merge is refused outright: if it does not belong, it does not land.

**Review is not where intent is decided (2026-08-29, U2).** This verdict used to be
`-FitsAnchor`, which asked the reviewer to re-judge whether the work was the right *thing*.
That is the operator's call and they have already made it twice — at the anchor gate before
any work, and at the pre-review release gate. Re-opening it at merge time puts the decision
furthest from the person who owns it, at the moment it costs most to act on. If you believe
the anchor asked for the wrong thing, say so and send it back to the release gate; do not
encode an intent objection as a codebase verdict. The old spelling is gone, not aliased —
it asked a different question and answering it here was the mistake.

```powershell
.\scripts\agent-harness\queue.ps1 -Claim -Id <id> -Role reviewer -By <their-id>
git -C <the developer's worktree> rebase <work line>
```

The tests passed at a specific commit. **If your rebase changes what would land, that pass
no longer describes it** - return the item rather than merging it:

```powershell
.\scripts\agent-harness\queue.ps1 -Requeue -Id <id> -By <their-id> -Reason "rebase changed the tested content"
```

That is not a rejection; nothing is wrong with the work. Use `-Reject` only when the work
itself should not land.

**Step 5 - the REVIEWER merges, in a dedicated merge worktree.**

```powershell
# Write the evidence to a FILE first: multi-line -m is impractical in PS5.1, and unlike
# `git commit`, `git merge -F -` does NOT read stdin ("could not read file '-'").
git -C <main-checkout> worktree add .claude/worktrees/merge-line <work line>
git -C <main-checkout>/.claude/worktrees/merge-line merge --no-ff work/<id> -F <msg-file>
git -C <main-checkout> worktree remove .claude/worktrees/merge-line
.\scripts\agent-harness\queue.ps1 -Merged -Id <id> -By <their-id> -Sha <merge sha>
```

**If the work line is checked out in the main checkout, you cannot merge - hand off
instead.** Git refuses a second checkout of one branch, and force-moving the ref would
leave that working tree's index lying about its contents. With the work line defaulting to
the operator's active branch this is the normal case, so it is a supported outcome, not a
failure: leave the branch rebased and green, release the claim, and report the exact command
for them to run. `new-worktree.ps1` warns about this at provisioning time so it is never a
surprise at landing time. Never merge inside the operator's checkout - it is their working
copy and a bridge turn could be running in it.

`--no-ff` keeps the branch visible in history, and the merge message carries the evidence -
the operator's branch policy made mechanical, and what makes a later bisect readable. If the
merge bumps the `OB1` gitlink, verify the SHA is reachable on the OB1 remote **first**; an
unreachable gitlink breaks every fresh `--recurse-submodules` clone.

**A note on commit and merge messages.** PS5.1 mangles multi-line strings and embedded
quotes for native commands - a developer agent had `git commit -m` split its message
into pathspecs. Write the message to a file and use `-F <file>`, for `git commit` as
well as `git merge`.

**Step 6 - the developer retires the worktree.**

```powershell
.\scripts\agent-harness\remove-worktree.ps1 -Id <id>
```

It refuses while you still hold uncommitted or unmerged work, and tells you what. That
refusal is the difference between "cleaned up" and "deleted the only copy".

## 3. Conflicts: who talks to whom

**Tier 1 — mechanical** (adjacent edits, both-added files, import ordering). The
merging agent resolves them during the rebase. **The later merger adapts.** This rule
exists so merge order cannot change correctness, and so nobody has an incentive to
race for the lease.

**Tier 2 — semantic overlap** (you both changed the same subsystem with competing
intents; adapting mechanically would defeat the other agent's goal). Do not resolve
it silently:

1. Post into the **other session's Mattermost thread** — the worktree registry
   (`scripts/agent-harness/state/worktrees.json`) carries each worktree's owner thread.
   Describe the conflict, your proposed resolution, and what it costs them.
2. The bridge's follow/auto-wake machinery wakes that session with your post as its
   next prompt. It answers in-thread. This is existing, working machinery — no new
   code, and it leaves an auditable trail the operator can read later.
3. Implement what you agree on, then finish the merge.

**Use Mattermost, not `SendMessage`.** A `ListAgents` peer may be a headless
subprocess mid-turn, and injecting into one derails it — that happened on 2026-08-23
and produced confidently-wrong answers. The thread is the bus for both entry points.

**Tier 3 — no convergence, or the resolution is destructive** (one of you wants to
delete what the other depends on). Stop. Ask the operator — `AskUserQuestion` in the
extension, a plain question in-thread from the bridge. Never resolve a disagreement
by force-push; `development` history is append-only.

## 4. What stays human

| Action | Who |
|---|---|
| Merge into `development` with evidence | **Agents** (this protocol) |
| Promotion of `development` → `main` | Operator, deliberately |
| Deploying to prod containers / retagging `:local` | Gated step; operator-approved |
| `git push` | Only when the operator asks |
| Resolving a Tier-3 disagreement | Operator |

## 5. Failure modes and what to do

| Symptom | What it means | Do |
|---|---|---|
| `-Acquire` exits 3, lease HELD | Another agent is mid-merge | Wait; poll ≤1/min |
| `-Acquire` exits 3, lease EXPIRED | Previous owner probably died | `-Takeover`, **and** post a note in their thread |
| Rebase conflict you cannot judge | Semantic overlap | Tier 2 — talk to them |
| `remove-worktree` refuses | Your only copy of something | Land it or `-Force` deliberately |
| Worktree exists but registry row is gone | Manual `git worktree add`, or a crash | `remove-worktree.ps1 -PruneRegistry` |
| `.env` in your worktree looks stale | Operator changed it after you branched | `sync-worktree-env.ps1 -Id <id>` |

## 6. Deliberate limits

- **Only the landing is serialized, and git does it.** Development runs fully parallel in
  worktrees; the merge step is one fast operation that git refuses to run twice on one
  branch. There is no merge lock because there is no merge race.
- **The shared line is never left mid-merge.** The rebase happens on the work branch. A
  dead agent leaves a stale claim (TTL'd, takeable) and a half-rebased branch in *its own*
  worktree - never a broken shared line.
- **The pipeline costs a round trip.** Three roles means an item waits for a tester and
  then a reviewer. That is the price of not merging your own work on a live-service
  codebase, and it is paid deliberately - `queue.ps1 -List` is where you see what is
  waiting on whom.
- **This protocol does not cover deploy verification.** Anything that must be proven
  through the real caddy/tailnet chain happens after the merge, serially, by nature.
