# Test plan - sl-checks-worktree (stack-layers)

**Branch:** `work/sl-checks-worktree` (from `development` at `a2d3644`)
**Developer:** `wt-sl-checks-worktree` - do not let the author of this plan execute it.
**Anchor:** `../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-checks-worktree.json`
**Findings sink:** `documentation/notes/stack-layers-sl-checks-worktree-findings.md`

## What changed (so you know what you are attacking)

| file | change |
|---|---|
| `scripts/checks/check-llm-gateway-routing.ps1` | allow-globs matched ROOT-RELATIVE instead of against the absolute path; `'*\.claude\*'` glob replaced by a `.claude` traversal prune; scan set counted, printed on both verdicts, and zero refuses; UTF-8-BOM + 7 non-ASCII comment characters normalised to ASCII/no-BOM |
| `scripts/checks/plan-store.ps1` | the plan store is also looked for beside the MAIN checkout (via `git rev-parse --git-common-dir`) when it is not beside the current checkout |
| `scripts/agent-harness/README.md` | one gotcha bullet |
| `documentation/notes/routing-check-worktree-blindspot-2026-09-19.md` | was untracked in the main checkout; now tracked, with a FIXED section |
| `documentation/notes/stack-layers-sl-checks-worktree-findings.md` | new - the sweep of all 50 scripts |
| `documentation/evidence/sl-checks-worktree/test-plan.md` | this file |

**Nothing about WHAT any check enforces was touched.** T12 is the case that tries to
falsify that sentence, and T6 is the one that tries to catch the fix having widened the
allow-list by accident.

## Environment - build these two trees first

You need the branch in TWO places: inside `.claude/worktrees/` (where the defect lives) and
at an ordinary repo-root path (the control). Do not reuse the developer's worktree for the
worktree half if you can provision your own; if you cannot, say so in your report.

```sh
# (A) a scratch worktree - the harness way, from the MAIN checkout
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Open WebUI\ai-stack\scripts\agent-harness\new-worktree.ps1" -Id sl-ckw-test -Base work/sl-checks-worktree
#     -> A = D:\Open WebUI\ai-stack\.claude\worktrees\wt-sl-ckw-test

# (B) a scratch CLONE at a repo-root path outside .claude
B=/c/Users/<you>/scratch/ckw-clone
git clone --no-local "D:/Open WebUI/ai-stack" "$B" && git -C "$B" checkout work/sl-checks-worktree
cp -r "D:/Open WebUI/ai-stack/OB1" "$B/OB1" && rm -f "$B/OB1/.git"     # so both trees hold the same files
for p in .env agent-org/docker/.env coder/.env frontend/.env inference/.env memory/.env portal/.env search/.env; do : > "$B/$p"; done

# (C) the same clone at BASE, for the red/green pair
git clone --no-local "D:/Open WebUI/ai-stack" "$B-base" && git -C "$B-base" checkout a2d3644
```

Every command below is run from the root of the tree named in the case. Nothing here
touches a container, a `:local` image or an `ai-stack_*` network.

---

## T1 - the routing check scans the same set from a worktree as from a repo-root clone, and the number is not zero

*Acceptance 1, first half. Claims C1, C2, C3, C9.*

```sh
# in A (the worktree)
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checks/check-llm-gateway-routing.ps1
# in B (the clone, with the 8 empty .env placeholders created above)
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checks/check-llm-gateway-routing.ps1
```

**Expected:** both print
`[check-llm-gateway-routing] OK - no LLM gateway bypasses found. <N> file(s) scanned under <root>.`
with the SAME `<N>`, `<N> > 0`, exit 0. The developer measured N = 1009 for both; your N may
differ if OB1 or the gitignored `.env` set differs, but the two trees must agree with each
other. **Disproves it:** different counts, or a count of 0, or no count printed at all.

**Sub-case T1b (what the 8 files are):** skip the `for p in ...` line when building B and
re-run. Expect exactly 8 fewer than A (developer measured 1001 vs 1009). A different delta
means the trees are not equivalent - reconcile before trusting T1.

## T2 - a planted bypass in a worktree's staged compose file goes RED on the branch

*Acceptance 1, second half. Claims C1, C4.*

```sh
# in A
cat > inference/compose/zz-planted-bypass.yml <<'EOF'
services:
  planted-bypass:
    image: busybox
    environment:
      OPENAI_API_BASE: http://llama-cpp-upstream:8080
EOF
git add inference/compose/zz-planted-bypass.yml
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checks/check-llm-gateway-routing.ps1 ; echo "exit=$?"
```

**Expected:** `FAIL - 1 gateway bypass(es) found in <N+1> file(s) scanned ...`, the line
`inference\compose\zz-planted-bypass.yml:5`, exit 1.
**Disproves it:** exit 0, or a report that does not name the planted file.

## T3 - the same plant at base `a2d3644` is GREEN from the same worktree (the defect, reproduced)

*Acceptance 1, third part. Claims C1, C5.*

With the plant from T2 still in place in A:

```sh
# base copy of the script only - do NOT check out base in A
powershell -NoProfile -ExecutionPolicy Bypass -File "$B-base/scripts/checks/check-llm-gateway-routing.ps1" -Root "<windows path of A>"
```

**Expected:** `OK - no LLM gateway bypasses found.` (no count - the base script prints
none), exit 0, while the bypass is sitting in the tree. That IS the defect.
**Disproves it:** base going red - then the blind spot was not what this item claims and
the fix is unmotivated.

## T4 - the defect was the PATH, not the pattern

*Claims C5, C6.*

```sh
cp <A>/inference/compose/zz-planted-bypass.yml "$B-base/inference/compose/"
cd "$B-base" && powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checks/check-llm-gateway-routing.ps1 ; echo "exit=$?"
```

**Expected:** the BASE script, run at a repo-root path, FAILS on the same plant, exit 1.
**Disproves it:** base green here too - then the pattern never matched the plant and T3
proved nothing. Clean up both plants afterwards (`git rm --cached`, `rm`).

## T5 - a scan of nothing now refuses

*Claims C7, C8.*

```sh
mkdir -p /tmp/ckw-empty
# branch script
powershell -NoProfile -ExecutionPolicy Bypass -File <A>/scripts/checks/check-llm-gateway-routing.ps1 -Root "C:\tmp\ckw-empty" ; echo "exit=$?"
# base script
powershell -NoProfile -ExecutionPolicy Bypass -File "$B-base/scripts/checks/check-llm-gateway-routing.ps1" -Root "C:\tmp\ckw-empty" ; echo "exit=$?"
```

**Expected:** branch `FAIL - scanned 0 files under ...`, exit 1; base `OK`, exit 0.
**Disproves it:** branch exiting 0 on an empty root.

## T6 - the allow-list still allows, and still only what it allowed

*Acceptance "only where it looks". Claims C10, C11.*

```sh
# in A - (a) plant inside an ALLOWLISTED path
printf '\nzz_probe:\n  api_base: http://llama-cpp-upstream:8080\n' >> inference/config/litellm.config.yaml
powershell ... check-llm-gateway-routing.ps1          # expect OK, exit 0
git checkout -- inference/config/litellm.config.yaml
# (b) plant in a NON-allowlisted sibling (a model_list fragment)
printf 'zz_probe:\n  api_base: http://llama-cpp-upstream:8080\n' > inference/config/litellm/model_list/zz-plant.yaml
powershell ... check-llm-gateway-routing.ps1          # expect FAIL naming that file, exit 1
rm inference/config/litellm/model_list/zz-plant.yaml
```

**Expected:** (a) green, (b) red naming
`inference\config\litellm\model_list\zz-plant.yaml`. Repeat both in B - identical verdicts.
**Disproves it:** (a) going red (the root-relative rewrite broke the globs - and note the
script allow-lists ITSELF, so a broken glob would also make every run red on this file), or
(b) staying green (the fix widened the allow-list, which would be a change to what is
enforced).

## T7 - every other check in the pre-commit chain selects the same set from both trees

*Acceptance 2 ("the tester repeats the selection test for every script listed as safe").
Claims C12-C18. Run each pair in A and in B and diff the output.*

| check | command | plant that must go RED in BOTH | expected |
|---|---|---|---|
| secrets | `./scripts/checks/check-staged-secrets.ps1` | `printf 'X=1\n' > zz-plant.env; git add zz-plant.env` | `COMMIT BLOCKED ... ENV FILE STAGED: zz-plant.env`, exit 1, identical text |
| line endings | `./scripts/checks/validate-lineendings.ps1` | compare `git ls-files '*.sh' \| wc -l` in both (developer measured 24/24) | `SUCCESS: All tracked shell scripts have Unix line endings`; same tracked count |
| doc placement | `./scripts/checks/check-doc-placement.ps1 -All` | - | identical: `Tracked feature directories here ...: 3`, `No untracked paths under implementation-guide.` |
| gateway routing | T1-T6 above | | |
| corpus exposure | `./scripts/checks/check-corpus-exposure-producers.ps1` | - | identical, including the count in `all <n> RECOGNISED corpus insert site(s) ... (<m> file(s) scanned)` (developer measured 13 sites / 773 files in both) |
| project configs | `./scripts/checks/check-project-configs.ps1` | stage a `.ps1` with an unterminated string | identical `[configs] PS1 PARSE ERROR: <path> - The string is missing the terminator: ".` |
| env_file scope | `./scripts/checks/check-env-file-scope.ps1` and `-All` | stage a compose file with `env_file:` `- ../../.env` | identical `env_file grant of a shared .env in a staged compose file (1): ...:5: env_file -> ../../.env -- is the repo root env file` |
| OB1 5b/5c/5d | `check-ob1-recipe-tests.ps1`, `check-ob1-deno-recipes.ps1`, `check-ob1-integration-images.ps1` | no OB1 gitlink staged | identical skip line in both; these select from the index and the pinned tree, not from disk position |

**Disproves the sweep:** any check whose output differs between A and B in a way that is
not explained by the gitignored `.env` set - and in particular any check that reports FEWER
files, or misses its plant, from A. That is exactly the failure this item exists to remove,
and it fails the item.

## T8 - plan-store.ps1 finds the store from a worktree

*Claims C19, C20, C21.*

```sh
# in A
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checks/plan-store.ps1 ; echo "exit=$?"
# at base, same tree, to see what it used to do
powershell -NoProfile -ExecutionPolicy Bypass -File "$B-base/scripts/checks/plan-store.ps1"   # run with A as cwd
# in B (a clone with no plan store anywhere beside it)
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/checks/plan-store.ps1 ; echo "exit=$?"
```

**Expected:** in A, `== plan store: D:\Open WebUI\documentation-plans-ai-stack` and a
verdict line, exit 0 or 1 depending on the store's real state (a dirty store legitimately
exits 1 - read the output, not just the code). The base script in A throws
`plan store not found at ...\.claude\worktrees\documentation-plans-ai-stack`, exit 1. In B,
the unchanged refusal naming B's own parent, exit 1.
**Disproves it:** A still refusing, or B silently picking up the operator's store (the
checkout's own parent must be tried first).

## T9 - dev-helper.ps1 is documented, not fixed, and the documentation is true

*Acceptance 2's "safe-with-reason" row that is NOT safe from the main checkout. Claims C22, C23.*

```sh
powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach ($p in @('<A>','D:\Open WebUI\ai-stack','<B>')) { $n = @(Get-ChildItem -Path $p -Filter '*.sh' -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '\.git' }).Count; Write-Host ($n.ToString() + '  ' + $p) }"
```

This is `dev-helper.ps1`'s own selector, copied from its source. **Expected:** A and B agree
(developer measured 33 and 33 - your numbers will differ with the worktree's contents but
must MATCH each other); the main checkout is much larger (developer measured 168) because
it contains the sibling worktrees. **Disproves the note:** A scanning fewer than B - then
`dev-helper` belongs in the fixed column, not the documented one.

## T10 - encoding, parse, lint, attestation

*Acceptance 3. Claims C24, C25.*

```sh
# no BOM, ASCII only, in both touched .ps1
powershell -NoProfile -Command "foreach ($f in 'scripts/checks/check-llm-gateway-routing.ps1','scripts/checks/plan-store.ps1') { $b=[IO.File]::ReadAllBytes($f); '{0}: bom={1} nonascii={2}' -f $f, ($b[0] -eq 0xEF), (@($b | Where-Object { $_ -gt 127 }).Count) }"
# both tokenize under PS 5.1
powershell -NoProfile -Command "foreach ($f in 'scripts/checks/check-llm-gateway-routing.ps1','scripts/checks/plan-store.ps1') { $e=$null; [void][System.Management.Automation.PSParser]::Tokenize((Get-Content $f -Raw), [ref]$e); '{0}: {1} parse error(s)' -f $f, $e.Count }"
ruff check .
git -C <A> log -1 --format=%H && git rev-parse --git-common-dir  # then grep that tree hash in <common>/hook-attest.log
```

**Expected:** `bom=False nonascii=0` for both; 0 parse errors; ruff clean (`All checks
passed!`); every commit on the branch has its tree hash in the attestation ledger.
**Disproves it:** a BOM, any byte > 127, a parse error, or a commit whose `git write-tree`
hash is absent from the ledger (that means the hooks did not run for it).

## T11 - the pre-commit chain passes from inside the worktree

*Acceptance 3. Claim C26.*

```sh
# in A, with the branch checked out and a trivial staged change
git config core.hooksPath   # must be the main checkout's .githooks
echo "" >> documentation/notes/stack-layers-sl-checks-worktree-findings.md && git add -A && git commit -m "test: hook chain from a worktree"
git reset --hard HEAD~1
```

**Expected:** every numbered check prints its green line - including
`[check-llm-gateway-routing] OK ... <N> file(s) scanned`, with N > 0 - and
`Pre-commit validations passed!`.
**Disproves it:** the routing line printing no count (a stale script), or any check failing.

## T12 - nothing about WHAT is enforced changed

*Out-of-scope guard. Claim C27.*

```sh
git diff a2d3644 -- scripts/checks/check-llm-gateway-routing.ps1 | grep -E '^[-+]' | grep -vE '^[-+]\s*#' | grep -vE '^(\+\+\+|---)'
```

**Expected:** among the non-comment changes there is NO edit to `$badPattern`, to
`$queueUpstreamAllow`, to `$exts`, or to any entry of `$allowPathLike` other than the
removal of `'*\.claude\*'`; `$pruneDirNames` gains `.claude` and nothing else.
**Disproves it:** any change to the detection regexes or the extension list - that would be
a change to what the guard enforces, which the anchor puts out of scope.

---

## Claims index

Every sentence this item introduces, as a claim you can try to break. "Introduced" includes
the comments inside the two scripts - a comment that is wrong is a lie the next reader
inherits.

### In `check-llm-gateway-routing.ps1`

* **C1** - "the absolute path of every file inside a worktree contains `\.claude\`, so the
  `*\.claude\*` entry allowed the ENTIRE tree: the guard read none of them and exited 0."
  (T1, T2, T3)
* **C2** - "Test-Allowed now matches the path RELATIVE to `$Root`, never the absolute path."
  (T1, T6)
* **C3** - "The globs themselves are unchanged and keep working, because the relative path
  is prefixed with `\` and `-like` lets `*` match the empty string." (T6, T12)
* **C4** - "What no longer matches is a directory name that happens to appear ABOVE the scan
  root." (T1 - and to break it, put a clone under a directory literally named `data` or
  `documentation` and check it is still scanned.)
* **C5** - "The defect is the path, not the pattern." (T3 + T4)
* **C6** - "`.claude` pruned at traversal is what makes it THIS root's own: the walk starts
  at `$Root`, so from the main checkout it is `<repo>\.claude\` (which also stops it walking
  other sessions' worktrees) and from a worktree it is the worktree's own `.claude\`."
  (T1; and from the main checkout, confirm a bypass planted in another worktree is NOT
  reported as this tree's.)
* **C7** - "A guard that examined nothing is not green." (T5)
* **C8** - "Both verdicts print the count." (T1, T2)
* **C9** - "N is the same from a worktree and from a repo-root clone of the same content."
  (T1, T1b)
* **C10** - "The gateway's own config is still allowed." (T6a)
* **C11** - "A model_list fragment is deliberately NOT allowed." (T6b)

### In the findings note (`stack-layers-sl-checks-worktree-findings.md`)

* **C12** - "50 executable files are tracked under `scripts/checks/` and `.githooks/`; the
  other 6 tracked files are a README, a data file, 2 fixtures and 2 .sql."
  (`git ls-files scripts/checks .githooks | wc -l` = 56.)
* **C13** - "2 fixed, 1 documented-not-fixed, 47 safe with a reason." (count the table rows)
* **C14** - each row's "how it selects files" cell names the mechanism at the cited line.
  (Open the file at that line. A citation that does not say what the row claims fails the
  row - re-derive rather than trusting the number.)
* **C15** - each row's "worktree vs clone" cell marked MEASURED was actually measured, and
  every row marked READ says so. (T7 re-measures them independently.)
* **C16** - "check-staged-secrets, check-project-configs, check-doc-placement and
  check-env-file-scope select from git (staged paths / toplevel), which answers the same in
  any checkout." (T7)
* **C17** - "check-corpus-exposure-producers is already safe and says at its allow-list why
  it carries no `.claude` glob." (T7 + read the comment)
* **C18** - "The OB1 gates (5b/5c/5d) select from the index and the pinned tree, so the
  checkout's position cannot change their set." (T7, plus reading the three scripts)
* **C19** - "plan-store.ps1 resolved the store to `<repo>\.claude\worktrees\` from a
  worktree and threw." (T8, base leg)
* **C20** - "It now also looks beside the main checkout via `--git-common-dir`, which is
  absolute from a worktree and a bare `.git` from the main checkout." (T8;
  `git rev-parse --git-common-dir` in both trees)
* **C21** - "The checkout's own parent is tried FIRST, so an ordinary clone is unaffected."
  (T8, B leg)
* **C22** - "dev-helper.ps1 does not under-scan from a worktree; it over-reaches from the
  main checkout (33 / 33 / 168)." (T9)
* **C23** - "It was left alone deliberately, because `fix-lineendings` WRITES and because
  `-notmatch '\.git'` also excludes `.github/`." (read `dev-helper.ps1`; the `.github`
  half is checkable with `git ls-files '.github/**/*.sh'`)
* **C27** - "Nothing about what any check enforces was changed - only where they look."
  (T12)

### In `scripts/agent-harness/README.md`

* **C24** - the new bullet's factual half: the check "pruned `.claude` by absolute-path
  substring ... fixed 2026-09-20: it matches root-relative, prints `N file(s) scanned` on
  every verdict, and refuses zero." (T1, T5)

### In the routing note (`routing-check-worktree-blindspot-2026-09-19.md`)

* **C25** - the FIXED section's table of seven runs reproduces. (T1-T5)
* **C26** - "the pre-commit chain passes from the worktree itself." (T11)
* **C29** - "the glob sat at `check-llm-gateway-routing.ps1:77` at `a2d3644`" - the note
  originally said `:73`, and the correction is itself a claim:
  `git show a2d3644:scripts/checks/check-llm-gateway-routing.ps1 | grep -n claude`.
* **C28** - "validate-lineendings' export vacuity is unchanged and still open." (run it in a
  `git archive` export with no `.git`: still `No tracked shell scripts to check`, exit 0)

## What a fair failure looks like

* Any check that reports fewer files, or misses a planted violation, from the worktree than
  from the clone, and is not documented in the findings note as deliberate. (Acceptance 2)
* The routing check printing no count, or a count of 0, anywhere it is green.
* A claim above that cannot be reproduced by the command attached to it.
* A file:line citation in the note that does not point at what the row says it does.
