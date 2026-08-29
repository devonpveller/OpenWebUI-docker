# Findings — initdb chain integrity, 2026-08-29

Checked against `work/initchain` at `a9febd8`, OB1 at `8e7a481`.

---

## F1 — the offline harness was validating a chain that no longer existed

`scripts/checks/test-quartz4-offline.ps1` carried a **hardcoded** list of init files ending
at `88-init-import-jobs.sql`. Compose mounted twenty. So seven migrations — every one added
since import-jobs, including source-editing, thread-embedding, claims, research-jobs, ideas
and wiki-pages — were never exercised by the harness that exists to prove fresh-apply works.

It reported `PASS init chain ran without errors` the whole time. It was telling the truth
about a chain nobody runs.

Fixed here by deriving the chain from compose. A test that keeps its own copy of reality
eventually tests something reality no longer resembles.

---

## F2 — the verify query was decoration

The harness ran a verify query and `Write-Host`'d it. Nothing compared the numbers to
anything. A verify step whose output nobody asserts on is a status line, not a check — it
cannot fail, so it cannot catch anything.

Four assertions were added (agent-memory tables/trigger/functions, wiki-links index). The
moment they existed they failed, which is how the two bugs below were found.

---

## F3 — `Start-Sleep 18` was racing initdb, and my first fix raced it differently

The harness slept a fixed 18 seconds before grepping the log. With the chain grown to 21
files that is a coin flip: too short and it greps a half-finished log and calls it clean.

My first replacement polled for `database system is ready to accept connections` — which is
**worse**, because postgres logs that line **twice**: once for the temporary server it runs
the initdb scripts against, and again when the real server starts. Polling catches the
first, i.e. *before the migrations have run*, and every verify count comes back 0.

The correct marker is the entrypoint's own `PostgreSQL init process complete`. Recorded
because the mistake is easy to repeat and looks right in review.

---

## F4 — initdb ordering was locale-dependent, and the whole scheme was fragile

The big one. The entrypoint iterates `for f in /docker-entrypoint-initdb.d/*` under **bash**
with `LANG=en_US.utf8`, where collation **ignores punctuation** at the primary level.
Verified inside the actual image:

```
98-init-ideas.sql
99a-init-agent-memory.sql
99b-init-wiki-pages-links.sql
99-init-wiki-pages.sql        <-- LAST
```

So `99a` sorts *before* `99-`, not after. The `99a` prefix chosen when the agent-memory
mount was added (earlier the same day) was therefore wrong on its stated reasoning — that
reasoning was ASCII byte order, which is what `sh` uses, not what bash-with-a-UTF-8-locale
uses. It only appeared to work because agent-memory depends solely on `thoughts`, created by
the first file in the chain.

Adding the wiki-links index exposed it: that one needs `wiki_pages`, so initdb aborted with
`relation "wiki_pages" does not exist`.

Mixed-width numbers fail the same way (`100-` sorts before `20-`). The only form correct
under both the C and UTF-8 collations is **fixed-width, digits-only** prefixes, which is
what the chain now uses (010…110). Only mount targets changed; on-disk filenames are
untouched.

**Worth knowing generally:** any glob-ordered directory in this stack has the same hazard.
If another component relies on filename ordering, it deserves the same check.

---

## F5 — pre-existing harness failures, unrelated to this work

Baselined on the line before merging, so the record is honest about what was already red:

- `deno check/test`
- `caddy validate`

Both fail on `refactor/ai-stack-cleanup` without any of this item's changes. Not
investigated here — they belong to whoever owns the workbench and portal surfaces — but
they mean **this harness has not been exiting 0 for some time**, so nobody is running it as
a gate. A check that always fails is a check that gets ignored, which is how F1 and F2
survived.

A third failure, `compose config`, appears only inside a worktree: `new-worktree.ps1` copies
`.env`, `.env.test` and `OB1/docker/.env`, but OB1 compose also references
`OB1/recipes/email-history-import/.env`, which is gitignored and not copied. The render
succeeds on the main checkout. Worth adding to the worktree env-copy list.
