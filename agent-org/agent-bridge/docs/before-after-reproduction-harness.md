# Before/After Reproduction Harness — headless runtime self-verification

**Status:** BUILT 2026-07-13 (`_org_reproduction_verified` + `read_merge_base`, wired into the
`_finish_effort` runtime gate). Step 1 (the honesty gate) shipped 2026-07-12 — see below.

## As built (2026-07-13)

`_org_reproduction_verified(effort_id, head_sha)`:
1. resolves `base` = `read_merge_base(repo, branch)` (GitHub `/compare/{default}...{branch}` →
   `merge_base_commit.sha`) — the pre-fix fork point;
2. runs the project's `_build_segment(check_cmd)` at **base** then **head** in ONE deterministic
   `exec_check` (the full-history clone already holds `base`, an ancestor of `head`, so no extra
   clone/fetch): `git checkout -f <base>; if (build); then REPRO_BASE=0; else 1; fi; git checkout -f
   <head>; …; echo "REPRO_BASE=$… REPRO_HEAD=$…"`;
3. parses the markers and sets `_repro_red_green[effort_id] = head_sha` **only** when base FAILED on a
   genuine code failure (`_is_infra_failure` ⇒ not a real RED) **and** head PASSED.

**Fail-closed by construction** — no resolvable base, base == head, an infra failure at base, a
timeout, or an unparseable result all return `False` (never a false verify).

**Submodule-fix handling (extended same day):** `git submodule update` is proxy-denied to the
worker, so `git checkout <commit>` moves the superproject tree but leaves each vendored submodule's
working dir put. To make a **submodule-fix** reproduce, the command syncs each submodule to ITS
gitlink at base/head with a LOCAL checkout (proxy-safe): a `csub()` helper does `git rev-parse
<commit>:<path>` → `git -C <path> checkout <sha>` for every path in `.gitmodules`. Best-effort +
`|| true`: if the proxy denies those reads, or a submodule sha isn't in the local clone, that
submodule stays put ⇒ the harness **fails closed** for that fix (honest "needs your runtime check"),
never a false pass. Nested submodules keep the privileged focus's populated state. So the harness now
covers **host-level AND vendored-submodule** runtime symptoms, and is fail-closed everywhere else.

**git-proxy note (verified against `little-coder/git-proxy/git_proxy.py`):** `csub` must sync each
submodule with a `( cd "$pth" && git checkout … )` SUBSHELL, **not** `git -C <path> checkout` — the
proxy DENIES the `-C` global (`_DENIED_GLOBALS_EXACT`, "repo-escape") which would silently no-op the
sync. The commands it uses — `git config --get-regexp` (config-read allowlist), `git rev-parse`
(read-only), bare-sha `git checkout` (write allowlist) — are all whitelisted; `ls-remote` is NOT
(that's why reachability uses the GitHub API, not the worker's git).

Tests: `test_repro_harness.py` (red→green verifies; green-base smoke test rejected; fail-closed on
no-base / infra-base / base==head).
**North star:** the keystone of the "dark factory" for *runtime* bugs — the org proving an
interaction/visual symptom is fixed WITHOUT a human, honestly.

## The problem it solves (the atlas false-done, 2026-07-12)

The runtime-symptom trust ladder let the org close a runtime symptom as **"done — VERIFIED via
reproduction"** when:

- the **worker's** output contained a `REPRO:` + `AFTER: PASS` block (the worker's *word*), AND
- the org's own `check_cmd` went **green** (`_org_verified[eid]`).

But a green check is not a reproduction. The monogame-engine check is:

```
… dotnet build vendor/murder/Murder.sln && <shader-magic check> &&
  cd …/Murder.Editor/bin/Debug/net8.0 &&
  xvfb-run -a bash -c 'timeout 15 dotnet Murder.Editor.dll; timeout 30 dotnet Murder.Editor.dll …;
                       grep -qiE "Unhandled exception|Exception:" … && exit 1; test $ec -eq 124 …'
```

It **builds + launches the editor for 30 s and greps for a crash**. The atlas symptom is *"opening a
Game Profile throws `[ERR] Atlas not loaded`"* — a **click-triggered interaction**, logged as `[ERR]`
(not an "Unhandled exception"). The passive launch never opens a Game Profile, so the check is
**green whether or not the atlas bug is present.** Trusting it = the operator's core distrust ("90%
of claims are false"), reincarnated.

## The principle

> A check only *reproduces* a symptom if it is **RED on the broken code and GREEN on the fix.**
> A check that is green on *both* does not exercise the symptom — it is a smoke test, not a
> reproduction, and cannot substantiate "verified."

The org can only claim "verified via reproduction" when **it has itself observed that RED→GREEN
transition** — never on the worker's word + a one-sided green.

## Step 1 (SHIPPED 2026-07-12) — stop the lie, fail closed

`orchestrator.py` runtime gate now requires an **org-observed RED→GREEN** signal
(`self._repro_red_green[eid] == self._org_verified[eid]`) in addition to the worker's REPRO block +
org green. Nothing sets `_repro_red_green` yet, so:

- runtime symptoms rest as honest **"NOT independently verified — please confirm on your end"**
  (the pre-trust-ladder honest state), stating exactly what *was* run (build + launch);
- when the worker already supplied a repro, the org does **not** burn worker cycles re-iterating —
  the gap is the org's (no harness), so it asks the operator to confirm;
- **no path can emit a false "verified via reproduction."**

Tests: `test_behavioral_verification.py`
(`…with_repro_but_org_did_not_run_before_after_is_not_verified` = the exact atlas case).

## Step 2 (THIS DOC) — the harness that sets `_repro_red_green`, truthfully

Run the project's check at **both** the fix head and its base, and only certify RED→GREEN.

1. **head** = `delivery.head_sha` (org already builds this green in `_org_build_check`).
2. **base** = merge-base(head, default_branch). Cheapest source: GitHub
   `/compare/{default}...{head}` → `merge_base_commit.sha` (already have a GH client + transport).
   Fallback: `git merge-base` in the sidecar.
3. **run check at base** — the same `check_cmd`, but the hermetic checkout targets `{base_sha}`
   instead of `FETCH_HEAD`. Refactor `_org_build_check` to accept `ref: str | None = None`
   (default = branch FETCH_HEAD); the host-path and plain-path checkouts both parameterize the ref.
   In the **composition** case, checking out the host base + recursive-updating submodules yields the
   OLD vendored source (pre-fix), which is exactly the pre-fix state we want to reproduce against.
4. **interpret** (fail closed):
   | base verdict        | head verdict | conclusion                                   |
   |---------------------|--------------|----------------------------------------------|
   | `fail` (non-infra)  | `pass`       | **RED→GREEN** → set `_repro_red_green[eid]=head` → VERIFIED |
   | `pass`              | `pass`       | check doesn't exercise the symptom → NOT verified *(atlas)* |
   | `infra` / `unknown` | any          | can't conclude → NOT verified                |

### Guards / gotchas
- **Only for runtime symptoms with a worker REPRO block** — don't double-run every build check.
- **Base infra-RED ≠ reproduction.** Reuse `_is_infra_failure` / `_MSBUILD_ENV_RE`: a base that's
  red because it *can't build for unrelated/infra reasons* must NOT count as RED→GREEN (it would
  false-verify). Only a genuine **code/behavior** failure at base counts.
- **Cost:** one extra full check (build + ~45 s launch) per runtime-symptom delivery. Acceptable for
  a human-trust-critical, lights-out path.
- **The reproduction must actually be in the check.** For the atlas, the *real* fix is also to add a
  headless test that opens a Game Profile (or invokes the atlas-load path) and asserts the atlas
  loads — wired into `check_cmd` — so that base goes RED. Until the worker wires such a step, base
  stays green and the org correctly refuses to certify. This closes the loop: the harness *forces*
  workers to deliver genuine reproductions, because nothing else earns "verified."
- **Emit an audit event** (`delivery_repro_red_green` with base/head verdicts) so the claim traces to
  real evidence, same as `org_build_check`.

### Where it hooks
After `_org_build_check` returns `pass` inside the delivery-finish path (where `_org_verified[eid]`
is set), for a runtime-symptom effort whose worker output has a REPRO block: run the base check and,
on a clean RED→GREEN, set `_repro_red_green[eid] = head_sha`. `_finish_effort`'s gate then flips to
the truthful "✅ Verified via reproduction — RED before, GREEN after, I ran it myself."
