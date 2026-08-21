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
# EXIT: 0 = clean, 1 = blocked.

$ErrorActionPreference = 'Stop'

# --- staged, still-present files (Added/Copied/Modified) --------------------
$staged = @(& git diff --cached --name-only --diff-filter=ACM) |
    Where-Object { $_ -and $_.Trim() -ne '' }

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
    if ($LASTEXITCODE -ne 0 -or -not $content) { continue }
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
