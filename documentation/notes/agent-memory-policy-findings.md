# Findings — agent-memory policy layer (Phase 1.2 slice 1), 2026-08-29

Checked against `work/ampolicy` at `be56fa2`, OB1 at `2f7d094`.

---

## F1 — the local secret guard and GitHub's disagree, and GitHub is stricter

The first push of this work was **rejected by GitHub push protection** (`GH013`), on a
Slack-token-shaped literal in the test fixtures for the secret detector. The repo's own
`pre-commit` secret guard had scanned the same file and passed it.

So there are two scanners with different coverage, and the stricter one is the remote:

- local `check-staged-secrets.ps1` — blocks env files and provider tokens;
- GitHub push protection — a much wider partner-pattern set, including Slack.

That gap matters because it moves the failure to the latest, most expensive moment: after
the work is committed, at push time, when the fix means rewriting history. This session hit
exactly that (an `--amend` was needed).

**Two things worth doing, neither in scope here:**

1. Extend the local guard toward GitHub's pattern set — at minimum Slack, GitHub PATs, JWTs
   and PEM blocks — so the block happens at commit time.
2. Record the rule that produced the fix, because it is not obvious: **a test fixture for a
   secret detector must never be a literal.** Assemble it at runtime
   (`join("xox", "b-…")`). The regex sees the same input; the repository holds nothing
   scannable.

The offered alternative — the "allow the secret" URL in the rejection — was deliberately
NOT used. Clicking it would have normalised bypassing a push guard in the same session
whose U5 work exists because an agent bypassed a commit guard.

---

## F2 — the invariant is only as good as the constant it reads

`defaultWritebackIsRecallable()` composes `WRITEBACK_DEFAULTS.review_status` against
`DEFAULT_RECALL_STATUSES`, and its paired negative test forces the value back to `'pending'`
to prove the check can fail.

What it does **not** prove is that `WRITEBACK_DEFAULTS` is what the *writeback tool actually
inserts*. Nothing calls this module yet. The moment the tool exists (next slice), it can
still ignore these constants and write `review_status` itself, and every test here stays
green while the plane breaks in exactly the documented way.

**So the next slice owes a test at the seam:** the writeback path must be shown to use
`WRITEBACK_DEFAULTS` rather than its own literals — ideally by having the tool take them as
its only source and asserting the inserted row's `review_status` equals
`WRITEBACK_DEFAULTS.review_status`. Written down here because it is the obvious place for
the invariant to leak.

---

## F3 — `MAX_CONTENT_CHARS` is characters, and the embedding limit is tokens

The detector caps content at 20,000 characters. The embedding lane (bge-m3) has a **512
token** limit, and this stack has already been bitten twice by that boundary — the OB1
embedding-size rejection, and the daily-digest re-ingestion bug where an oversized chunk
500'd the embedder.

So a memory can pass this gate and still be unembeddable. That is not wrong — they are
different limits for different reasons, and truncation/halving belongs with the writeback
implementation that owns the embedding call — but the two must not be confused for each
other. The next slice needs its own guard on the embedding path; passing
`detectUnsafeContent` is not evidence that a memory can be embedded.

---

## F4 — this module is unimported, and that is the point (but it is also a risk)

Nothing imports `agent-memory-policy.ts`. The plan asks for the invariant test to be
written *before* the feature, and that is what this is.

The risk is the obvious one: an unimported module can drift out of agreement with the code
that eventually does the work, and dead code that looks authoritative is worse than no code.
The mitigation is that the next slice consumes it rather than reimplementing it (see F2). If
the tools land without importing this, **delete this module** rather than leaving two
policies in the tree.
