# Test plan - queue item `emitters`

**Branch:** `work/emitters` **Developer:** `emitters` **OB1:** `b69cdbf -> d126926`
(OB1 branch `fix/emitter-alias-sanitise`, pushed)

**The change.** Two alias emitters in `OB1/recipes/entity-wiki/generate-wiki.mjs`
interpolated a raw entity name/label into a wikilink alias: the entity
**auto-linker** (`linkifyEntities`) and the entity **index** (the `entities.md`
builder). Quartz's wikilink alias class excludes `#`, so `C#` and `Daily #NNN`
produced links that do not render at all - they survive on the page as literal
`[[...]]` markup. Both now route the alias through the shared `linkSafeLabel`,
the way the `Grounded by` emitter already did. Where a label sanitises away to
nothing the auto-linker leaves the prose unlinked and the index falls back to a
bare `[[slug]]`; neither emits an empty-alias `[[slug|]]`.

To make them testable at all - which is why the break shipped - `linkifyEntities`
is now exported and the index emitter is extracted into a pure exported
`buildEntityIndex`. That extraction is meant to be verbatim; the only behavioural
change inside it is the `linkSafeLabel` call. Case 7 checks that.

Secondly, a cross-check invariant was appended to
`documentation/notes/wiki-research-2026-08-30-status-and-findings.md` (new
section 3), plus a two-sentence status marker closing that file's section 2.

---

## READ THIS FIRST - three traps, each of which has already produced a false result

### TRAP 1 (the big one): SERVED-PAGE EVIDENCE IS INVERTED from the obvious reading

A previous plan for this same area got this **backwards** and would have
manufactured a false green. Read it twice.

The wiki viewer has **two** renderers:

- `OB1/docker/wiki-viewer/lib/render-page.mjs` `renderMarkdown` - its own
  wikilink regex, whose alias group **PERMITS `#`**. Both the `db` path AND the
  `fresh` path in `serve.mjs` call this same function. The source comment on the
  fresh path says so outright.
- Quartz proper - reached **only** by the STATIC BUILD.

Consequences, and this is the whole trap:

- A page fetched with `x-wiki-render: db` renders `Daily #NNN` **correctly
  against completely unfixed code.**
- A page fetched with `x-wiki-render: fresh` does too. `fresh` is NOT the Quartz
  path, despite the name.
- There is no `x-wiki-render: static`. The only values the server ever emits are
  `db`, `fresh` and `not-available`.

**Therefore: valid served-page evidence is a response carrying NO `x-wiki-render`
header at all, after a real Quartz compile. A response carrying `db` or `fresh`
must be DISCARDED - it proves nothing about this change.** Case 3 gives you
exactly that, hermetically. If you improvise your own served-page check, print
the response headers and state which renderer produced it.

### TRAP 2: run vault greps in `openbrain-wiki-viewer`, NOT `openbrain-wiki`

`openbrain-wiki` is BusyBox. Its grep has no `--include`, so it matches nothing
and **silently prints `0`** - and a `0` reads exactly like a fully-healed vault.
`openbrain-wiki-viewer` has GNU grep (3.8 at time of writing; `grep --version`
confirms). Every vault command in this plan runs there, at `/wiki`.

### TRAP 3: do not re-type the probe commands - copy them from the file

Those greps are dense with backslashes, and a backslash was eaten in transit
three separate times while those notes were written (the file records this).
Copy them out of `documentation/notes/wiki-research-2026-08-30-status-and-findings.md`
as printed. One of them is deliberately wrapped across two lines and the other
two are deliberately single lines; that distinction is load-bearing. If you paste
through a tool layer that eats backslashes, write them into a `.sh` file first
and run `docker exec -i openbrain-wiki-viewer sh < file`.

---

Set these once (forward slashes work in both bash and PowerShell here):

```
WT="D:/Open WebUI/ai-stack/.claude/worktrees/wt-emitters"
OB1="$WT/OB1"
```

Substitute if you have the branch checked out elsewhere - it must be the checkout
holding `work/emitters` with OB1 at `d126926`. Confirm before anything else:

```
cd "$OB1" && git rev-parse HEAD     # expect d126926...
cd "$WT"  && git rev-parse HEAD     # expect the tip of work/emitters
```

---

## Case 1 - RED-FIRST: the new tests must FAIL against the pre-fix emitters

This is the case that decides whether the tests test anything. Do not skip it and
do not take the developer's transcript for it.

Reconstruct the pre-fix emitters **without** reverting the test file or the
export/extract refactor, so you are running the NEW tests against the OLD
behaviour. In `$OB1`, edit `recipes/entity-wiki/generate-wiki.mjs` and undo
exactly the two `linkSafeLabel` calls:

1. In `linkifyEntities`, make the replacer use the raw `name` as the alias again
   (the original was a single `return slug ? ... : m;` interpolating `${name}`).
2. In `buildEntityIndex`, replace the `const label = linkSafeLabel(n.label);` +
   conditional `idx.push(...)` pair with the original single `idx.push` that
   interpolates the raw `${n.label}`.

Then:

```
cd "$OB1" && node --test recipes/entity-wiki/generate-wiki.test.mjs
```

**PASS if:** exactly **4** cases fail, and they are the four `#` cases:

- `auto-linker: a '#' in an entity name still emits a link Quartz can parse`
- `auto-linker: a name that sanitises to nothing stays plain text, not an empty alias`
- `entity index: a '#' in an entity label still emits a link Quartz can parse`
- `entity index: a label that sanitises to nothing falls back to the slug, never an empty alias`

and the two "unchanged behaviour" guards - `a '#'-free name is linked exactly as
before`, `a '#'-free label is listed exactly as before` - **PASS**. Both halves
matter: the first shows the tests catch the defect, the second shows the fix is
not simply "strip everything".

The developer's RED run reported `# tests 14 / # pass 10 / # fail 4`, with the
auto-linker failure message
`broken wikilink left as literal text: We ship [[technology/c-sharp|C#]] services.`

**FAIL if:** fewer than 4 fail (a test that cannot go red proves nothing), or if
either `#`-free guard fails (then the extraction was not verbatim).

Restore afterwards: `git checkout -- recipes/entity-wiki/generate-wiki.mjs`, then
confirm `git status --short` in `$OB1` is clean and HEAD is still `d126926`.

**Confirm the assertions are behavioural, not textual.** Open
`recipes/entity-wiki/generate-wiki.test.mjs` and check the new block asserts on
the STRING RETURNED BY THE EMITTER, driven through `WIKILINK_RE`. If any new
assertion greps the source of `generate-wiki.mjs` for the word `linkSafeLabel`,
that assertion does not count - report it.

Check the parser is genuinely Quartz's and not a paraphrase:

```
docker exec openbrain-wiki-viewer sh -c "sed -n '120p' /quartz/quartz/plugins/transformers/ofm.ts"
docker exec openbrain-wiki-viewer sh -c "grep -m1 version /quartz/package.json"
```

Compare the regex byte-for-byte with the `WIKILINK_RE` literal in the test file
(ignoring the trailing comma). The version should be `4.5.1`.

## Case 2 - GREEN: the whole OB1 recipe suite, and no case-count shrink

```
cd "$OB1" && node --test $(find recipes -name '*.test.mjs')
```

**PASS if:** `# tests 54 / # pass 54 / # fail 0` across 8 test files. The pinned
predecessor `b69cdbf` had 48; 54 is +6. A shrink is a refusal, not a warning.

## Case 3 - SERVED-PAGE evidence: a real Quartz compile, no `x-wiki-render` header

Hermetic - it touches no prod container, no vault and no lease. It builds a tiny
fixture from the emitters' REAL output and compiles it with the real Quartz in
the deployed viewer image. Re-run it yourself; do not accept the transcript.

```
SC=/c/temp/qfix ; rm -rf "$SC" ; mkdir -p "$SC/content/technology" "$SC/content/source" "$SC/out"

cd "$OB1"
SC=$SC node --input-type=module -e "
import { linkifyEntities, buildEntityIndex } from './recipes/entity-wiki/generate-wiki.mjs';
import fs from 'node:fs';
const d = process.env.SC + '/content';
const fm = '---' + String.fromCharCode(10) + 'title: t' + String.fromCharCode(10) + '---' + String.fromCharCode(10,10);
fs.writeFileSync(d+'/autolinker.md', fm + linkifyEntities('The service is written in C# and ships daily.', [{name:'C#',slug:'technology/c-sharp'}]));
fs.writeFileSync(d+'/entities.md', buildEntityIndex([{slug:'source/daily-481',label:'Daily #481',type:'source'},{slug:'technology/c-sharp',label:'C#',type:'technology'}], []));
fs.writeFileSync(d+'/unfixed.md', fm + 'The service is written in [[technology/c-sharp|C#]] and ships daily.');
fs.writeFileSync(d+'/technology/c-sharp.md', fm + 'x');
fs.writeFileSync(d+'/source/daily-481.md', fm + 'x');
"

docker run --rm -v "$SC/content:/fixture:ro" -v "$SC/out:/outroot" --entrypoint sh openbrain-wiki-viewer:local -c "cd /quartz && npx quartz build -d /fixture -o /outroot/site --concurrency 1"

docker run --rm -d --name emitters-static -p 18099:8080 -v "$SC/out/site:/site:ro" --entrypoint sh openbrain-wiki-viewer:local -c "cd /site && npx --yes http-server -p 8080 --silent"
sleep 4
curl -sS -D - -o /dev/null http://127.0.0.1:18099/autolinker.html
curl -sS http://127.0.0.1:18099/autolinker.html | grep -oE 'written in <a[^>]*>[^<]*</a>'
curl -sS http://127.0.0.1:18099/entities.html   | grep -oE '<li><a href="[^"]*(source|technology)[^"]*"[^>]*>[^<]*</a></li>'
curl -sS http://127.0.0.1:18099/unfixed.html    | grep -oE '<p>The service.*</p>'
docker rm -f emitters-static
```

Note the output dir is mounted one level ABOVE the target (`/outroot`, building
into `/outroot/site`). Quartz `rmdir`s its output dir on start and fails with
`EBUSY` if that dir is itself the bind mount.

**PASS requires ALL of:**

1. The compile banner says `Quartz v4.5.1` and it reports `Emitted N files`.
2. The header dump for `autolinker.html` contains **NO `x-wiki-render` header**.
   If you see `db` or `fresh`, you are hitting the wrong server - see TRAP 1.
3. `autolinker.html` renders a real anchor, e.g.
   `written in <a href="./technology/c-sharp" class="internal alias" data-slug="technology/c-sharp">C</a>`.
4. `entities.html` renders both entries as anchors, including `>Daily 481</a>`
   and `>C</a>`.
5. The CONTROL, `unfixed.html`, carries the PRE-FIX emitter output and in the
   SAME compile renders the literal text `[[technology/c-sharp|C#]]`.

Item 5 is what makes 3 and 4 mean anything: it proves the compile WOULD have
shown the break had it been there, so a green on 3/4 is not just a permissive
renderer.

**FAIL if** the control also renders as an anchor (then it is not the deployed
Quartz doing the rendering), or if any response carries `x-wiki-render`.

## Case 4 - the gate chain 5b/5c actually ran on this bump

```
cd "$WT" && git show --stat HEAD
```

The parent commit bumps `OB1` `b69cdbf -> d126926` and touches the notes file.
Both OB1 gates fire only when an OB1 gitlink is STAGED, so to see them do real
work you must stage the bump:

```
cd "$WT"
git reset --soft HEAD~1
powershell -NoProfile -File "D:/Open WebUI/ai-stack/scripts/checks/check-ob1-recipe-tests.ps1" -Root "$WT"
powershell -NoProfile -File "D:/Open WebUI/ai-stack/scripts/checks/check-ob1-deno-recipes.ps1" -Root "$WT"
git commit -C ORIG_HEAD          # restore the commit exactly
git rev-parse HEAD               # must equal the SHA you started from
```

**PASS if:** 5b reports `shrink floor OK - test FILES 8 -> 8, test CASES 48 -> 54
(b69cdbf -> d126926)` and `# tests 54 | # pass 54 | # fail 0`, with **no** 3c
self-consistency warning - the lexical count and node's runtime count agree at
54. An absent warning is the thing to confirm here, not merely an absent refusal.
5c reports `deno check clean over 36 non-test *.ts in OB1/recipes/daily-digest`.

**FAIL if** either gate prints "not applicable" while the gitlink IS staged -
that is a gate that did not gate.

## Case 5 - OB1 reachability and NO pin regression

```
cd "$OB1"
git ls-remote origin fix/emitter-alias-sanitise
git merge-base --is-ancestor b69cdbf d126926 ; echo "b69cdbf ancestor: $?"
git merge-base --is-ancestor a18e2ca d126926 ; echo "a18e2ca ancestor: $?"
```

**PASS if:** ls-remote returns `d126926c42f2ccfd28e734e54fc08ae1f6d7a57b`, and
both ancestor checks print `0`. The pinned commit must be reachable on the OB1
remote BEFORE the parent gitlink lands (two items nearly lost their OB1 work this
week to exactly that omission), and it must CONTAIN the current pin so merging
does not silently revert the wikilink fix, the podcast lineage, the curator
ResilientPool or the wiki-viewer work that `b69cdbf` carries.

## Case 6 - the notes invariant, exercised the way the file instructs

Open `documentation/notes/wiki-research-2026-08-30-status-and-findings.md`. The
new section 3 claims the still-unhealed probe and the merged `^Daily #` command
measure the same population and must agree. Test the claim; do not read it.

1. Copy BOTH commands out of the file **as printed** (TRAP 3).
2. Run BOTH in `openbrain-wiki-viewer` at `/wiki`, in one session (TRAP 2).
   Print `grep --version` first.

**PASS if:** the two numbers are EQUAL. The developer's paired run on 2026-09-05
agreed under GNU grep 3.8. Your value will differ from the developer's - it
decays continuously as the merged fix heals pages on regeneration - and that is
expected, and is precisely why no figure is written into the section.

**FAIL if:** they disagree. Per the invariant itself, treat that as a BROKEN
PROBE and report which one, not as vault movement.

Also confirm the new section attaches **no figures** to the probes (the anchor
required this: any number written beside them is wrong within the hour), and that
its instructions match traps 2 and 3 above.

## Case 7 - scope: nothing else moved, and the extraction is verbatim

```
cd "$WT"  && git show --stat HEAD
cd "$OB1" && git show --stat HEAD
```

**PASS if:** the parent commit touches exactly `OB1` (gitlink) plus the one notes
file, and the OB1 commit touches exactly `generate-wiki.mjs` and its test file.

**FAIL if** any of these, all explicitly out of scope, were touched:
`linkSafeLabel`'s character class or its lossy-`#` behaviour (the `C# -> C`
degrade is a recorded operator decision, deliberately not reopened); page
regeneration or backfill; the 149 `bracket in alias` breaks from other emitters.

Then diff `buildEntityIndex` against the block it replaced inside
`writeGraphManifest`:

```
cd "$OB1" && git show HEAD -- recipes/entity-wiki/generate-wiki.mjs
```

The frontmatter lines, the `# Knowledge Wiki` heading, the
`Compiled from OpenBrain ...` sentence, the `See [[notebooks|Notebooks]]` line,
the per-type grouping and the `localeCompare` sort must all be unchanged. One
intentional difference: `links.length` became `(links || []).length` so the pure
function tolerates a caller passing nothing; the production caller always passes
`links`. Anything else is a silent behaviour change to every `entities.md`
compile and should be reported.

---

## What would DISPROVE the developer's claim

Aim at these, in this order:

1. **The tests cannot go red.** If Case 1 shows fewer than 4 failures, the tests
   are decorative and the fix is unproven no matter how green Case 2 is.
2. **The served-page green came from the permissive renderer.** If any Case 3
   response carries `x-wiki-render`, or if the CONTROL page also renders an
   anchor, the evidence is void. This is the failure this plan exists to prevent.
3. **The extraction was not verbatim.** `buildEntityIndex` is a lift-and-shift; a
   quiet change to the frontmatter, the sort or the grouping would alter every
   `entities.md` compile while all tests stayed green.
4. **The pin regresses.** If `d126926` does not contain `b69cdbf`, merging this
   reverts four other landed items.
5. **The probes disagree.** Then section 3's invariant asserts something false,
   and the secondary payload is wrong even if the primary is right.
