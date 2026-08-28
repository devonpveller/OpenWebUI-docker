# common.ps1 - the single include for this toolkit.
#
# Kept as a one-line composition root so every script keeps ONE dot-source and the internal
# split can change without touching callers:
#
#   git-io.ps1    facts   - how to talk to git, and repository topology (no policy)
#   resolve.ps1   policy  - where shared state lives, which branch is the work line
#
# The dependency points one way (policy -> facts), so the git adapter can be exercised
# without the policy, and the policy can be redirected with environment variables without
# touching git at all. Split out of a single grab-bag file on 2026-08-28 when it had grown
# three unrelated responsibilities.

. (Join-Path $PSScriptRoot "resolve.ps1")
