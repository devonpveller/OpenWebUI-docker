# check-staged-secrets.ps1 - pre-commit secret guard
#
# WHY THIS EXISTS (2026-08-20):
#   .env.bak-pre-mtp and .env.bak-pre-qwen38 were committed and only caught at
#   push time by GitHub push protection, which matched TWO GitHub PATs. Each file
#   actually held ~25 live credentials (Authelia JWT/session/storage keys,
#   Cloudflare tunnel token, Mullvad WireGuard private key, Tailscale auth key,
#   Mattermost/Telegram bot tokens, LiteLLM master key, DB passwords,
#   WEBUI_SECRET_KEY). GitHub only pattern-matches ITS OWN token format, so the
#   block was luck - the other ~25 would have been published.
#   .gitignore covered ".env" and ".env.killswitch-*.bak" but not ".env.bak-*".
#
#   This runs at COMMIT time so nothing secret ever reaches a commit, rather than
#   relying on a remote-side scanner that only knows a few vendors' formats.
#
# SCOPE: only STAGED content is scanned (git diff --cached), so it stays fast -
# no walking the working tree or vendored/data dirs.
#
# PER-PLANE ENV FILES (sl-env-split, 2026-09-19): rule 1 below matches on the
# file's LEAF, not its path, so `frontend/.env`, `portal/.env` and every other
# `<plane>/.env` are blocked by the existing `$leaf -eq '.env'` arm with no
# change - and `<plane>/.env.example` is allowed by the same allowlist that
# allows the root one. Verified by staging a scratch `portal/.env`. Do NOT
# anchor these tests to a path: that is exactly how the six new files would
# have become committable in silence.
#
# EXIT: 0 = clean, 1 = blocked.

$ErrorActionPreference = 'Stop'

# --- staged, still-present files (Added/Copied/Modified/Renamed/Type-changed)
# TYPE CHANGE (T) added 2026-09-25 (ac-hooks-portable attempt 1, tester): a tracked
# file turned into a SYMLINK is status T, outside ACMR, so `ln -sf <key> SECURITY.md`
# and a tracked `frontend/.env` turned into a symlink both passed as "nothing
# staged". A symlink's staged blob IS its target text, and that text is what the
# commit publishes - so `git show :<path>` below scans exactly it, and the name rules
# test the link's own name. (A link pointing at a key FILE publishes only the path;
# the key file itself is scanned if it is staged too.)
# RENAMED (R) was missing until 2026-09-25 (ac-hooks-portable F1, anchor amended):
# rename detection is on by default, so `git mv x frontend/.env` - or a rename
# plus an edit that adds a key - was status R, outside ACM, and this guard said
# "nothing staged - skip" and exited 0 on every host. `--name-only` prints a
# rename's DESTINATION path, which is the name the rules must test and the blob
# `git show :<path>` must read. Copies (C) were already inside ACM.
# check_staged_secrets.py carries the same filter; keep them in step.
# Exclude submodule gitlinks (mode 160000): they are commit pointers, not
# blobs, so `git show :<path>` errors on them. `--diff-filter` can't express
# "not a gitlink", so filter by mode from the staged index listing.
#
# PATH NAMES UNDER pwsh OFF WINDOWS (ac-hooks-portable, 2026-09-25). git C-quotes
# a non-ASCII name (`"caf\303\251.env"`), and `git show ":<quoted>"` then fails.
# Under Windows PowerShell 5.1 that stderr, with EAP=Stop, THROWS - the script
# exits 1 and the commit is refused (by accident, but closed). Under pwsh 7 a
# native command's stderr no longer throws, so the same file fell to the
# `$LASTEXITCODE -ne 0` skip below and its content was never read, and its
# quoted leaf (`...env"`) matched no filename rule either: with a non-ASCII
# `.env`, or a non-ASCII file holding a gw- key, staged, this script exited 0
# (measured at 3c3ff75, pwsh 7.4 in a Linux container). Off
# Windows the names are therefore read NUL-separated (-z), which git never
# quotes. On Windows the two git calls below are exactly the ones they were.
#
# EVERY git CALL HERE FAILS CLOSED (ac-hooks-portable2, review blocker 1). Under WSL
# the hook used to pick Windows PowerShell, whose Windows git refused the Linux
# checkout ("dubious ownership"); both queries below then returned nothing, and
# this guard printed "nothing staged - skip" and exited 0 - it had not asked its
# question at all. A query that FAILED is not an answer. The same holds for any
# git that cannot read this repository (GIT_DIR wrong, safe.directory, a broken
# index). The exit code of each call is checked, and a failure refuses the commit.
function Stop-GitFailed([string]$What, [int]$Rc) {
    Write-Host "  [secrets] FAIL - 'git $What' exited $Rc, so this guard cannot tell what is staged." -ForegroundColor Red
    Write-Host "  A failed query is not 'nothing staged'. Fix git's access to this repository" -ForegroundColor Red
    Write-Host "  (safe.directory, GIT_DIR, the index), then commit again." -ForegroundColor Red
    exit 1
}
$zPaths = ($PSVersionTable.PSEdition -eq 'Core') -and ($IsWindows -ne $true)
if ($zPaths) {
    $lsOut = & git ls-files --stage -z
    if ($LASTEXITCODE -ne 0) { Stop-GitFailed 'ls-files --stage -z' $LASTEXITCODE }
    $diffOut = & git diff --cached --name-only -z --diff-filter=ACMRT
    if ($LASTEXITCODE -ne 0) { Stop-GitFailed 'diff --cached --name-only -z' $LASTEXITCODE }
    $gitlinks = @((@($lsOut) -join "`n").Split([char]0) |
        Where-Object { $_ -match '^160000 ' } |
        ForEach-Object { ($_ -split '\t', 2)[1] })
    $staged = @((@($diffOut) -join "`n").Split([char]0)) |
        Where-Object { $_ -and $_.Trim() -ne '' -and $gitlinks -notcontains $_ }
} else {
$lsOut = & git ls-files --stage
if ($LASTEXITCODE -ne 0) { Stop-GitFailed 'ls-files --stage' $LASTEXITCODE }
$diffOut = & git diff --cached --name-only --diff-filter=ACMRT
if ($LASTEXITCODE -ne 0) { Stop-GitFailed 'diff --cached --name-only' $LASTEXITCODE }
$gitlinks = @(@($lsOut) |
    Where-Object { $_ -match '^160000 ' } |
    ForEach-Object { ($_ -split '\t', 2)[1] })
$staged = @($diffOut) |
    Where-Object { $_ -and $_.Trim() -ne '' -and $gitlinks -notcontains $_ }
}

if (-not $staged -or $staged.Count -eq 0) {
    Write-Host "  [secrets] nothing staged - skip"
    exit 0
}

$violations = New-Object System.Collections.Generic.List[string]

# --- 1. filename rules: env files must never be committed ------------------
# Allowlist = templates and non-secret config that are intentionally tracked.
$allowNames = @(
    '.env.example',
    '.healthcheck.env'
)
foreach ($f in $staged) {
    $leaf = Split-Path $f -Leaf
    if ($allowNames -contains $leaf) { continue }
    if ($leaf -like '*.env.example') { continue }

    # Any dotenv-shaped name: .env, .env.anything, anything.env, *.env.bak etc.
    if ($leaf -eq '.env' -or $leaf -like '.env.*' -or $leaf -like '.env-*' -or $leaf -like '*.env') {
        $violations.Add("ENV FILE STAGED: $f  (env files hold live credentials - never commit)")
    }
}

# --- 2. content rules: high-confidence provider token formats --------------
# Deliberately NOT a generic "KEY=<long string>" rule: this repo's docs discuss
# credentials by name constantly, and false positives would train people to
# bypass the hook. These patterns are specific enough to be near-zero-FP.
$patterns = @(
    @{ Name = 'GitHub PAT (classic)';    Re = 'ghp_[A-Za-z0-9]{36}' },
    @{ Name = 'GitHub PAT (fine-grain)'; Re = 'github_pat_[A-Za-z0-9_]{50,}' },
    @{ Name = 'GitHub OAuth/refresh';    Re = 'gh[osru]_[A-Za-z0-9]{36}' },
    @{ Name = 'OpenAI key';              Re = 'sk-[A-Za-z0-9]{32,}' },
    @{ Name = 'OpenAI project key';      Re = 'sk-proj-[A-Za-z0-9_\-]{20,}' },
    @{ Name = 'Anthropic key';           Re = 'sk-ant-[A-Za-z0-9_\-]{20,}' },
    @{ Name = 'Google API key';          Re = 'AIza[0-9A-Za-z_\-]{35}' },
    @{ Name = 'Slack token';             Re = 'xox[baprs]-[A-Za-z0-9\-]{10,}' },
    @{ Name = 'AWS access key id';       Re = 'AKIA[0-9A-Z]{16}' },
    @{ Name = 'Telegram bot token';      Re = '[0-9]{8,10}:AA[A-Za-z0-9_\-]{33}' },
    @{ Name = 'Private key block';       Re = '-----BEGIN [A-Z ]*PRIVATE KEY-----' },
    # This repo's own gateway-key format (mnemory/openbrain privacy gateways).
    # Added 2026-08-20: a live gw- key sat committed in .vscode/mcp.json and
    # openbrain-gateway/smoke_test.py for months - the one local token class
    # this guard could not see.
    @{ Name = 'ai-stack gateway key';    Re = 'gw-[A-Za-z0-9_\-]{30,}' }
)

foreach ($f in $staged) {
    # Read the STAGED blob, not the working file - they can differ.
    $content = & git show ":$f" 2>$null
    # A staged path whose blob cannot be read was NOT scanned: refuse, never skip it.
    if ($LASTEXITCODE -ne 0) { Stop-GitFailed "show :$f" $LASTEXITCODE }
    if (-not $content) { continue }
    $text = ($content -join "`n")

    # Skip obvious binaries.
    if ($text -match "\0") { continue }

    foreach ($p in $patterns) {
        $m = [regex]::Match($text, $p.Re)
        if ($m.Success) {
            $line = 1
            $idx = $m.Index
            if ($idx -gt 0) { $line = ([regex]::Matches($text.Substring(0, $idx), "`n")).Count + 1 }
            # Never print the secret itself.
            $violations.Add("$($p.Name) in ${f}:${line}")
        }
    }
}

# --- report ----------------------------------------------------------------
if ($violations.Count -gt 0) {
    Write-Host ""
    Write-Host "=========================================================" -ForegroundColor Red
    Write-Host " COMMIT BLOCKED - secret material detected in staged files" -ForegroundColor Red
    Write-Host "=========================================================" -ForegroundColor Red
    foreach ($v in $violations) { Write-Host "  - $v" -ForegroundColor Red }
    Write-Host ""
    Write-Host " Fix:" -ForegroundColor Yellow
    Write-Host "   git restore --staged <file>     # unstage it"
    Write-Host "   ...then add it to .gitignore so it cannot come back."
    Write-Host ""
    Write-Host " If a credential really was staged, treat it as COMPROMISED and"
    Write-Host " rotate it - do not just unstage and move on."
    Write-Host ""
    Write-Host " Genuine false positive (e.g. a doc example)? Bypass ONCE with:"
    Write-Host "   git commit --no-verify"
    Write-Host ""
    exit 1
}

Write-Host "  [secrets] staged files clean ($($staged.Count) scanned)"
exit 0
