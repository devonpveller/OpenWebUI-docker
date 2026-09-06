# Test plan - owuidrift

Anchor: `queue.ps1 -Show -Id owuidrift` (confirmed 2026-09-06 by
profnovice-delegated-to-claude-ext-2026-09-06). Branch `work/owuidrift`,
worktree `wt-owuidrift`, base `7614556`.

**You are testing three claims:**

1. `scripts/checks/check-owui-drift.ps1` answers, for every row of
   `owui/manifest.csv`, whether the repo file and the live `webui.db` row hold
   the same content after CR-normalisation - and it is right about which rows
   match (T1, T2, T3).
2. It cannot produce a clean bill it did not earn: any unreadable container,
   database, table or manifest is a REFUSAL with a sentence and exit 2, and any
   difference is exit 1 (T4).
3. It changes nothing and leaks nothing: no write of any kind reaches
   `webui.db`, and no plugin body, valve or `.env` value appears on any output
   path, including the failure paths (T5, T6).

## Hard rules for this run

- **`openwebui` is LIVE and serving the operator.** You may READ its database.
  You may NEVER write to it, restart it, stop it, pause it, or touch
  `tailscale` (it shares openwebui's network namespace; restarting openwebui
  alone breaks 8 tailnet serve routes). The "container stopped" case in T4 uses
  a THROWAWAY container you create yourself - `openwebui` is never stopped.
- No lease is needed: every case here is a read-only probe of the live stack
  plus a throwaway container on `--network none`. Never attach a test container
  to an `ai-stack_*` network.
- **A case that prints a secret is itself a FAIL.** T6 is the check on that.
- **A case you cannot execute is a plan inadequacy, not a scoped pass.** Use
  `queue.ps1 -PlanInadequate` naming the case; never write `PASS (scoped)`,
  `SKIPPED`, or a pass on partial evidence.
- Every case below must carry a bare `PASS` on its heading line when it passes.

## Two things the anchor gets wrong on its face, so you do not "fix" them

- The anchor says **16 rows**. `owui/manifest.csv` has **21** data rows: 13
  tool/function files + 8 skills. 16 was the count before
  `add_web_sources_to_knowledge`, `code_agent` and `code_agent_tools` were
  retired in August 2026 and the total was never recomputed;
  `owui/README.md` carried the same stale 16 and is corrected in this change.
  21 is the number to expect everywhere. Verify it: `git log --oneline -S
  code_agent -- owui/manifest.csv`, and count the file.
- The anchor names tables `tool` and `function` only. 8 of the 21 manifest rows
  are **skills**, which live in their own `skill` table with its own `content`
  column. Leaving them uncompared would have been a silent hole, so the script
  maps `type` -> table as: `tool`->`tool`, `action|filter|pipe`->`function`,
  `skill`->`skill`. A manifest `type` that maps to nothing is `UNKNOWN TYPE`
  and counts as drift, never as clean (T1(e)).

## Environment

Run from **PowerShell** in the worktree root, `D:\Open
WebUI\ai-stack\.claude\worktrees\wt-owuidrift`.

> **Git Bash trap:** MSYS rewrites a bare `/tmp/fake.db` argument into
> `C:/Users/.../Temp/fake.db` before PowerShell ever sees it, which turns T3/T4
> into a refusal for the wrong reason. If you drive these from Git Bash, prefix
> every command that carries a container-side path with `MSYS_NO_PATHCONV=1`.
> PowerShell has no such problem.

## T0 - setup: throwaway container, constructed fixture, scratch copy

Nothing here asserts; it builds what T2/T3/T4 use. `python:3-slim` is already
present locally (`docker images`); the container carries no volume, no mount and
no network.

```powershell
$SCRATCH = "$env:TEMP\owuidrift-test"
Remove-Item -Recurse -Force $SCRATCH -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $SCRATCH | Out-Null
Copy-Item -Recurse owui "$SCRATCH\owui-scratch"

docker run -d --name owuidrift-test --network none --entrypoint sleep python:3-slim 3600
docker exec owuidrift-test python3 -c "import sqlite3;c=sqlite3.connect('/tmp/fake.db');c.execute('create table tool (id text primary key, content text, updated_at integer)');c.execute('create table function (id text primary key, content text, updated_at integer)');c.execute('create table skill (id text primary key, content text, updated_at integer)');c.execute('insert into tool values (?,?,?)',('crlf_live','alpha\r\nbeta\r\ngamma\r\n',1788700000));c.execute('insert into tool values (?,?,?)',('lf_live','alpha\nbeta\ngamma\n',1788700001));c.execute('insert into function values (?,?,?)',('only_live','x=1\n',1788700002));c.execute('insert into skill values (?,?,?)',('dummy_skill','# skill\n',1788700003));c.commit()"
docker exec owuidrift-test python3 -c "import sqlite3;c=sqlite3.connect('/tmp/notables.db');c.execute('create table tool (id text primary key, content text, updated_at integer)');c.commit()"
```

Constructed fixture files - **note the crossed line endings**: `a_repo_lf.py` is
pure LF on disk and its live row is CRLF; `b_repo_crlf.py` is CRLF on disk and
its live row is LF. That crossing is the whole point of T3.

```powershell
$F = "$SCRATCH\fake"; New-Item -ItemType Directory -Force $F | Out-Null
[IO.File]::WriteAllBytes("$F\a_repo_lf.py",   [Text.Encoding]::ASCII.GetBytes("alpha`nbeta`ngamma`n"))
[IO.File]::WriteAllBytes("$F\b_repo_crlf.py", [Text.Encoding]::ASCII.GetBytes("alpha`r`nbeta`r`ngamma`r`n"))
[IO.File]::WriteAllBytes("$F\c_no_live_row.py", [Text.Encoding]::ASCII.GetBytes("whatever`n"))
@"
file,type,name,owui_id,sha256
a_repo_lf.py,tool,A,crlf_live,-
b_repo_crlf.py,tool,B,lf_live,-
c_no_live_row.py,tool,C,no_such_id,-
d_absent_file.py,filter,D,only_live,-
"@ -replace "`r`n","`n" | Set-Content -NoNewline -Encoding ascii "$F\manifest.csv"
Get-Item "$F\*.py" | ForEach-Object { "$($_.Name) size=$($_.Length)" }
```

Expect `a_repo_lf.py size=17`, `b_repo_crlf.py size=20` (17 + 3 CRs),
`c_no_live_row.py size=9`. If the sizes differ, PowerShell rewrote the line
endings and T3 proves nothing - fix that before continuing.

Teardown at the end of the run:
`docker rm -f owuidrift-test; Remove-Item -Recurse -Force $SCRATCH`.

## T1 - the live census: what the check says about all 21 rows today

```powershell
powershell -NoProfile -File scripts\checks\check-owui-drift.ps1
"EXIT=$LASTEXITCODE"
```

Expected, on the live stack as of 2026-09-06 (order is by file path):

```
21 manifest rows: 19 in sync, 2 differ, 0 missing live, 0 missing repo, 0 unknown type.
EXIT=1
```

with exactly these two `DIFFERS` rows and 19 `IN SYNC`:

| row | expect |
|-----|--------|
| `tools/deep_research.py` (`tool|deep_research`) | **IN SYNC** - the anchor's headline case; this session pasted it 2026-09-06, live `updated_at` reads `2026-09-06 12:07:10 UTC` |
| `filters/mnemory_persistent_memory.py` (`function|mnemory_persistent_memory`) | **DIFFERS**, live `updated_at` `2026-09-03 00:53:44 UTC` |
| `tools/mnemory.py` (`tool|mnemory`) | **DIFFERS**, live `updated_at` `2026-05-29 18:13:03 UTC` |
| the other 18 | **IN SYNC** |

(a) **Reproduce two rows by hand - do not take the script's word.** Pick
`tools/deep_research.py` (claimed IN SYNC) and `tools/mnemory.py` (claimed
DIFFERS). Hash both sides yourself, with different tools than the script uses:

```powershell
# repo side, CR dropped, hashed
foreach ($p in 'owui\tools\deep_research.py','owui\tools\mnemory.py') {
  $b = [IO.File]::ReadAllBytes($p) | Where-Object { $_ -ne 13 }
  $h = [Security.Cryptography.SHA256]::Create().ComputeHash([byte[]]$b)
  "{0}  {1}" -f ([BitConverter]::ToString($h).Replace('-','').ToLower()), $p
}
# live side, CR dropped, hashed INSIDE the container
docker exec openwebui python3 -c "import sqlite3,hashlib;c=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True);print([(i,hashlib.sha256(x.encode('utf-8').replace(b'\r',b'')).hexdigest()) for i,x in c.execute(\"select id,content from tool where id in ('deep_research','mnemory')\")])"
```

Expect `deep_research` to be `631db708e570d57d138ccf2ef4b7509e0a8fc1f6cf4692f69c34572c06b200f2`
on BOTH sides; `mnemory` to be `9d57b7f6efcc4a29337dce8febb0b9a4cbdb7eb4c4b7ef3ce0c2e107edde75be`
(repo) against `240c39138b196a3929ddeaa3bb49be7d54251e549c1447b38b84c42b268ded5f`
(live). Any mismatch with the script's own report FAILS T1.

(b) **The DIFFERS rows are real drift, not a bug in the comparison.** Confirm
the sizes differ too (numbers only, never content):

```powershell
docker exec openwebui python3 -c "import sqlite3;c=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True);print([(i,len(x.replace(chr(13),'')),len(x.replace(chr(13),'').encode('utf-8')),x.replace(chr(13),'').count(chr(10))+1) for i,x in c.execute(\"select id,content from tool where id='mnemory'\")])"
python -c "b=open(r'owui/tools/mnemory.py','rb').read().replace(b'\r',b''); s=b.decode('utf-8'); print(('mnemory-repo',len(s),len(b),s.count(chr(10))+1))"
```

Expect live `('mnemory', 23315, 23789, 615)` against repo
`('mnemory-repo', 23313, 23787, 615)` - i.e. 2 characters apart, same line
count. A comparison that called two contents of different length equal would be
the bug worth finding; they are not equal, and the script says so.

> **Units trap:** `len()` on a TEXT column (and SQLite's `length()`) counts
> CHARACTERS; the file on disk is BYTES. These files carry non-ASCII, so the
> two differ by ~60 for `deep_research` even when the content is identical.
> Compare like with like or you will "find" drift that is not there.

(c) **CR-normalisation is load-bearing on the live data, not decoration.** The
working tree holds `.py` files as CRLF (`.gitattributes` + `core.autocrlf=true`)
while the live `tool`/`function` rows are LF, and `.md` skills are LF on disk
while the live `skill` rows are CRLF. So a naive byte comparison would report
all 21 rows as drifted. Show it:

```powershell
(Get-Item owui\tools\deep_research.py).Length     # 17215 bytes on disk, CRLF
docker exec openwebui python3 -c "import sqlite3;c=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True);print([(i,len(x.encode('utf-8')),x.count(chr(13))) for i,x in c.execute(\"select id,content from tool where id='deep_research'\")])"
docker exec openwebui python3 -c "import sqlite3;c=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True);print([(i,x.count(chr(13)),x.count(chr(10))) for i,x in c.execute(\"select id,content from skill where id='docx'\")])"
```

Expect `17215` bytes on disk against `('deep_research', 16882, 0)` live - the
same 16882 bytes once the file's 333 CRs are dropped, and zero CRs live. The
skill row goes the other way: `('docx', 590, 590)` - CRLF live, while
`owui/skills/docx.md` on disk has 0 CRs (`.gitattributes`: `*.md text eol=lf`).
Both directions occur in the real data; T3 pins them with a constructed pair.

(d) The run must not crash on any row, and every one of the 21 rows must carry
exactly one of the five statuses.

(e) `UNKNOWN TYPE` is reachable and counts as drift. In a scratch manifest,
change one row's `type` to `widget` and re-run against it:

```powershell
(Get-Content "$SCRATCH\owui-scratch\manifest.csv") -replace '^tools/mnemory\.py,tool,','tools/mnemory.py,widget,' | Set-Content "$SCRATCH\owui-scratch\manifest.csv"
powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -Manifest "$SCRATCH\owui-scratch\manifest.csv"
"EXIT=$LASTEXITCODE"
Copy-Item owui\manifest.csv "$SCRATCH\owui-scratch\manifest.csv" -Force
```

Expect a `UNKNOWN TYPE  tools/mnemory.py  type=widget` line, `1 unknown type` in
the tally and `EXIT=1`. A row the check could not compare must never be silently
dropped or counted as in sync.

## T2 - a deliberately drifted file bites, and only that row

Perturb the SCRATCH copy. **Do not edit the live database and do not edit the
repo file** - the repo side is the safe side to perturb, and the scratch copy is
safer still.

```powershell
$p = "$SCRATCH\owui-scratch\tools\deep_research.py"
$b = [IO.File]::ReadAllBytes($p); $b[5134] = [byte][char]'%'   # was '#'
[IO.File]::WriteAllBytes($p, $b)
powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -Manifest "$SCRATCH\owui-scratch\manifest.csv"
"EXIT=$LASTEXITCODE"
```

Expected: `tools/deep_research.py` now reads `DIFFERS` with **two different**
64-hex hashes (repo `886c6cec037bb7c302cb04f68e58266b745fbeb9882d6e8379a00bd2d6a8475f`,
live `631db708...`), the tally reads `18 in sync, 3 differ`, and `EXIT=1`. The
other 20 rows are unchanged from T1 - a one-byte edit must move exactly one row.

Restore and confirm it returns:

```powershell
Copy-Item owui\tools\deep_research.py "$SCRATCH\owui-scratch\tools\deep_research.py" -Force
powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -Manifest "$SCRATCH\owui-scratch\manifest.csv"
"EXIT=$LASTEXITCODE"
```

Expected: `IN SYNC       tools/deep_research.py`, tally back to
`19 in sync, 2 differ`, `EXIT=1` (the two pre-existing drifts remain - that is
correct, not a failure of this case).

## T3 - CRLF vs LF compares EQUAL, proven with a constructed pair, both directions

The live data alone would let you argue this by reasoning; the T0 fixture
settles it by construction. `a_repo_lf.py` (LF on disk, 17 bytes) is matched
against a live row stored with CRLF (20 chars); `b_repo_crlf.py` (CRLF on disk,
20 bytes) against a live row stored with LF (17 chars). Both must read IN SYNC.

```powershell
powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -Container owuidrift-test -DbPath /tmp/fake.db -Manifest "$SCRATCH\fake\manifest.csv"
"EXIT=$LASTEXITCODE"
```

Expected, exactly:

```
IN SYNC       a_repo_lf.py                                   tool|crlf_live
IN SYNC       b_repo_crlf.py                                 tool|lf_live
MISSING LIVE  c_no_live_row.py                               tool|no_such_id
MISSING REPO  d_absent_file.py                               function|only_live

4 manifest rows: 2 in sync, 0 differ, 1 missing live, 1 missing repo, 0 unknown type.
EXIT=1
```

That single run also proves `MISSING LIVE` (a manifest id with no live row) and
`MISSING REPO` (a manifest file that is not on disk).

(a) The all-match path really does exit 0 - a green must be reachable, or
`EXIT=1` proves nothing:

```powershell
Get-Content "$SCRATCH\fake\manifest.csv" -TotalCount 3 | Set-Content "$SCRATCH\fake\manifest-clean.csv"
powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -Container owuidrift-test -DbPath /tmp/fake.db -Manifest "$SCRATCH\fake\manifest-clean.csv"
"EXIT=$LASTEXITCODE"
```

Expected: `2 manifest rows: 2 in sync, ...`, the `IN SYNC:` summary sentence,
`EXIT=0`.

(b) Falsification: had the script hashed raw bytes, T3's two rows would read
DIFFERS. Confirm the raw hashes really are unequal, so the equality above is
normalisation and not an accident:

```powershell
docker exec owuidrift-test python3 -c "import sqlite3,hashlib;c=sqlite3.connect('file:/tmp/fake.db?mode=ro',uri=True);print([(i,hashlib.sha256(x.encode()).hexdigest()[:16]) for i,x in c.execute('select id,content from tool')])"
Get-FileHash "$SCRATCH\fake\a_repo_lf.py" -Algorithm SHA256 | Select-Object -Expand Hash
```

The live `crlf_live` raw digest must NOT equal the raw digest of
`a_repo_lf.py`; the check still called them IN SYNC.

## T4 - it REFUSES rather than reporting a clean bill

**Run each of these in a CHILD process** (`powershell -NoProfile -File ...
2>&1`). The refusal is emitted with `Write-Host`, which is invisible to
in-process capture (`$out = & .\check.ps1`) - that trap silently emptied the
evidence of two other items this week.

**`openwebui` is never stopped.** (a) uses a container name that does not
exist; (b) uses the THROWAWAY container from T0.

```powershell
# (a) container absent
$o = & powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -Container owuidrift-nosuch 2>&1
"EXIT=$LASTEXITCODE"; $o

# (b) container present but STOPPED - the throwaway, never openwebui
docker stop owuidrift-test
docker ps --filter name=openwebui --format "{{.Names}} {{.Status}}"   # must still be Up (healthy)
$o = & powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -Container owuidrift-test -DbPath /tmp/fake.db -Manifest "$SCRATCH\fake\manifest.csv" 2>&1
"EXIT=$LASTEXITCODE"; $o
docker start owuidrift-test

# (c) database present but a table missing (notables.db has `tool` only)
$o = & powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -Container owuidrift-test -DbPath /tmp/notables.db -Manifest "$SCRATCH\fake\manifest.csv" 2>&1
"EXIT=$LASTEXITCODE"; $o

# (d) manifest missing
$o = & powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -Manifest "$SCRATCH\nope.csv" 2>&1
"EXIT=$LASTEXITCODE"; $o
```

Expected in all four: `EXIT=2`, a line beginning `REFUSED: ` that is a sentence
naming the cause, then `Nothing was compared, so nothing is known about drift.`
Verbatim first lines:

```
REFUSED: container 'owuidrift-nosuch' was not found by docker inspect, so the live database cannot be read.
REFUSED: container 'owuidrift-test' exists but is not running (State.Running=false), so the live database cannot be read.
REFUSED: reading '/tmp/notables.db' in container 'owuidrift-test' failed (exit 1) - the file, a table or python3 is missing, or the database is unreadable.
REFUSED: the manifest '...\nope.csv' does not exist, so there is no list of files to compare.
```

A warn-and-pass, an exit 0, an exit 1, or any output containing `IN SYNC:` or a
row tally FAILS this case: nothing was compared, so nothing may be reported.

(e) The `-CountOnly` mode used by `stack.ps1` must refuse the same way rather
than printing a reassuring `0`:

```powershell
$n = & powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -Container owuidrift-nosuch -CountOnly 2>$null
"stdout=[$n] EXIT=$LASTEXITCODE"
```

Expected `stdout=[REFUSED] EXIT=2`. `stdout=[0]` would be the exact failure this
whole item exists to prevent.

## T5 - it writes nothing

(a) Static: the script contains no write verb, and the only SQL it builds is a
SELECT. Read the statement, do not just grep for it - it is on one line of
`scripts/checks/check-owui-drift.ps1` (the `$py` here-string, ~line 148):

```powershell
Select-String -Path scripts\checks\check-owui-drift.ps1 -Pattern 'sqlite3.connect|select id,content'
Select-String -Path scripts\checks\check-owui-drift.ps1 -Pattern '\b(UPDATE|INSERT|DELETE|DROP|ALTER|ATTACH|commit\(\)|mode=rw|docker cp)\b'
```

Expect the connect string to be
`sqlite3.connect('file:<db>?mode=ro',uri=True)` - SQLite's own read-only URI
mode, which refuses a write at the engine level - and the second search to
return only prose matches (`updated_at`, comment text), no SQL verb. Confirm by
eye that no other SQL is assembled anywhere in the file.

(b) Live: take the state, run the check several times, take it again.

```powershell
docker exec openwebui sh -c "ls -l --time-style=full-iso /app/backend/data/webui.db"
docker exec openwebui python3 -c "import sqlite3;c=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True);print([(t,c.execute('select count(*) from '+t).fetchone()[0]) for t in ('tool','function','skill')])"
1..5 | ForEach-Object { powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 | Out-Null }
docker exec openwebui sh -c "ls -l --time-style=full-iso /app/backend/data/webui.db"
docker exec openwebui python3 -c "import sqlite3;c=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True);print([(t,c.execute('select count(*) from '+t).fetchone()[0]) for t in ('tool','function','skill')])"
```

Expected: identical size, identical mtime to the nanosecond, and
`[('tool', 5), ('function', 9), ('skill', 8)]` both times.

> Assert on `webui.db` itself. `webui.db-wal` and `webui.db-shm` move on their
> own every few seconds because OWUI is serving the operator; that is the app,
> not this check. If you want to be sure, run the two `ls` commands with the
> check NOT running in between and watch the same -wal mtime move.

## T6 - no secret, no valve, no plugin body, on any path

Collect the full output of EVERY case above - the successful ones and the
refusals - into one file, then search it. Never print the search patterns.

```powershell
$ALL = "$SCRATCH\all-outputs.txt"
& powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 *>&1 | Out-File $ALL -Encoding utf8
& powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -Container owuidrift-test -DbPath /tmp/fake.db -Manifest "$SCRATCH\fake\manifest.csv" *>&1 | Out-File $ALL -Append -Encoding utf8
& powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -Container owuidrift-nosuch *>&1 | Out-File $ALL -Append -Encoding utf8
& powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -Container owuidrift-test -DbPath /tmp/notables.db *>&1 | Out-File $ALL -Append -Encoding utf8
& powershell -NoProfile -File scripts\checks\check-owui-drift.ps1 -CountOnly *>&1 | Out-File $ALL -Append -Encoding utf8
```

(a) `.env` values. Compare by value, report only a count and the KEY names of
any hit:

```powershell
$out = Get-Content $ALL -Raw
$hits = Get-Content .env | Where-Object { $_ -match '^\s*[A-Za-z_][A-Za-z0-9_]*=' -and $_ -notmatch '^\s*#' } | ForEach-Object {
  $k,$v = $_ -split '=',2; $v = $v.Trim().Trim('"').Trim("'")
  if ($v.Length -ge 8 -and $out.Contains($v)) { $k.Trim() }
}
"env values present in output: $($hits.Count) -> $($hits -join ',')"
```

Expected: exactly one hit, `OPEN_WEBUI_HOST`, whose value is the 9-character
string `openwebui` - the container name the check is documented to print.
Confirm that is what it is (`(Get-Content .env | Select-String
'^OPEN_WEBUI_HOST=').Line.Split('=')[1].Length` is 9) and that no other key
appears. Any second hit FAILS.

(b) Live valve strings. Stream them as grep patterns so they never reach the
screen or the disk (Git Bash):

```bash
docker exec openwebui python3 -c "
import sqlite3,json
c=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True)
toks=set()
for t in ('tool','function'):
    for (v,) in c.execute('select valves from '+t):
        try: d=json.loads(v)
        except Exception: continue
        if isinstance(d,dict):
            for x in d.values():
                if isinstance(x,str) and len(x)>=8: toks.add(x)
print(chr(10).join(sorted(toks)))
" | grep -c -F -f - "$ALL"
```

There are 7 such strings live. Expected output `0` (grep exit 1). Any non-zero
count FAILS - and do NOT print the matching line to investigate.

(c) No plugin body. Every line of the output is a status row, a hash line, a
header, a tally or a REFUSED sentence:

```powershell
Get-Content $ALL | Where-Object { $_ -notmatch '^(IN SYNC|DIFFERS|MISSING LIVE|MISSING REPO|UNKNOWN TYPE|REFUSED|Nothing was compared|== owui|   manifest:|\s+(repo|live) [0-9a-f]{64}|\d+ manifest rows|DRIFT:|IN SYNC:|\d+|\s*$)' }
```

Expected: no output. Any line that is not one of those shapes must be explained
before this case passes.

## T7 - manifest.csv carries sha256, not bytes

```powershell
Get-Content owui\manifest.csv -TotalCount 1
(Get-Content owui\manifest.csv).Count
(Get-Content owui\manifest.csv | Select-Object -Skip 1 | Where-Object { $_ -match ',[0-9a-f]{64}$' }).Count
git diff 7614556 -- owui/manifest.csv
```

Expected: header exactly `file,type,name,owui_id,sha256` (no `bytes`); 22 lines
(header + 21 rows); 21 rows ending in a 64-hex digest; the diff touches only the
last column and the header (check the diff by eye - the `file,type,name,owui_id`
values and the ROW ORDER must be unchanged from `7614556`).

Recompute at least three digests by hand, from three different folders, and
confirm they match both the manifest column AND what the live rows hash to:

```powershell
foreach ($p in 'owui\actions\copy_sources.py','owui\skills\docx.md','owui\pipes\little_coder.py') {
  $b = [IO.File]::ReadAllBytes($p) | Where-Object { $_ -ne 13 }
  $h = [Security.Cryptography.SHA256]::Create().ComputeHash([byte[]]$b)
  "{0}  {1}" -f ([BitConverter]::ToString($h).Replace('-','').ToLower()), $p
}
```

Expected:

```
61515e8bb69d4ba1a83ed05164bb54d90f05d4cc16e02c62041881ba4e241415  owui\actions\copy_sources.py
cfbabd72b1aec7dfaad988fb6e5e16b27dc744b9a00cae15db9045e95f53903e  owui\skills\docx.md
90897748215ad5819dfb02b737efd6d82b7823d6a10588eb0ec4206fb2e00516  owui\pipes\little_coder.py
```

Note that the `sha256` column is documentation: the check hashes the FILE at run
time and never reads this column, so a stale column cannot make the check lie -
but nothing verifies the column either (recorded in the findings note).

## T8 - stack.ps1 health gains exactly one line

```powershell
powershell -NoProfile -File scripts\stack\stack.ps1 health
"EXIT=$LASTEXITCODE"
```

The baseline is the file at the base commit, not a re-run (the change is
committed by the time you test it, so `git stash` would not remove the probe):

```powershell
(git show 7614556:scripts/stack/stack.ps1 | Select-String '^\s*Probe "').Count   # 13
(Select-String -Path scripts\stack\stack.ps1 -Pattern '^\s*Probe "').Count       # 14
git diff 7614556 -- scripts/stack/stack.ps1
```

Expected: 13 probes before, 14 after; the diff adds one `Probe` line, one
`$owuiDrift` line that feeds it, and a comment - and touches nothing else in the
file. The health run prints exactly one more `[OK]`/`[FAIL]` line than the 13 at
the base:
`  [FAIL] frontend: owui/ snapshots drifted from live webui.db: 2`, positioned
between the tailnet-routes probe and the memory cloud-door probe. The count in
that line must equal the `differ + missing + unknown` total from T1. The probe
reports the COUNT and no file names.

`[FAIL]` is correct today: 2 rows really are drifted. The failing probe raises
the health exit code by one; confirm the final line moves from
`ALL HEALTH PROBES PASSED` to `1 probe(s) FAILED` and that no other probe
changed state.

(a) A refusal must read as FAIL, not as 0 drifted. Point the probe at a dead
container name by temporarily editing the `-Container` argument into the
`$owuiDrift` line, or trust T4(e) - which already proves `-CountOnly` prints
`REFUSED`, and `"REFUSED" -eq '0'` is false. State which you did.

## T9 - the documentation claims are true

(a) `owui/README.md`'s "Deployment sync status" section names the check, carries
the 2026-09-06 regeneration date, and no longer asserts a dated
byte-identical claim. The **paste/redeploy instructions and the netns warning
must be intact**: confirm the "Redeploy mechanism" section and the
`Netns ordering (not optional)` blockquote survive verbatim:

```powershell
git diff 7614556 -- owui/README.md
```

Expect no deletions below the `## Redeploy mechanism` heading.

(b) The script header's "WHAT A GREEN DOES NOT PROVE" list is honest. Check the
valve claim in particular - `valves` is a separate column and the check's SQL
selects `id,content,updated_at` only (T5a). Check the "manifest-driven" claim:
the live `function` table holds 9 rows while the manifest names 8 functions, so
one live function (`add_web_sources_to_knowledge`, retired 2026-08-20) is
invisible to the check exactly as the header says:

```powershell
docker exec openwebui python3 -c "import sqlite3;c=sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True);print(sorted(i for (i,) in c.execute('select id from function')))"
```

(c) `owui/README.md` no longer says 16 where the manifest has 21.

## T10 - scope

- `git diff --stat 7614556` touches only:
  `scripts/checks/check-owui-drift.ps1` (new), `owui/manifest.csv`,
  `owui/README.md`, `scripts/stack/stack.ps1`,
  `documentation/evidence/owuidrift/test-plan.md`,
  `documentation/notes/deploy-gate-2026-09-06.md`.
- **No `owui/*.py` or `owui/*.md` plugin snapshot changed** (anchor
  out-of-scope): `git diff --stat 7614556 -- owui/tools owui/pipes owui/filters
  owui/actions owui/skills` must be empty.
- Nothing was pasted into OWUI and nothing wrote to `webui.db` (T5).
- No scheduler, watcher or alerting was added: `git diff 7614556` contains no
  new scheduled task, cron entry or watchdog target.

## Teardown

```powershell
docker rm -f owuidrift-test
Remove-Item -Recurse -Force $SCRATCH
docker ps --filter name=openwebui --format "{{.Names}} {{.Status}}"   # still Up (healthy)
```
