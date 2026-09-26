#!/usr/bin/env bash
# gates-python.sh - the never-skipped pre-commit gates that read the WORKING TREE, run
# through their Python twins with no PowerShell involved.
#
# This is what a Linux contributor's commit hits when pwsh is not installed
# (.githooks/README.md, "Which host runs the gates"). Both twins walk the checkout, not
# the index, so they judge the whole tree here - a routing bypass anywhere, or a CRLF
# shell script anywhere, fails the job, not only one that was just staged.
#
#   gateway routing  scripts/checks/check_llm_gateway_routing.py  (twin of the .ps1)
#   line endings     scripts/checks/validate_lineendings.py       (twin of the .ps1)
#
# The third never-skipped gate, the secret guard, reads only STAGED content, so on a
# clean checkout it would pass having read nothing. It is exercised for real by the
# `hooks-linux` job instead, which stages a planted key and requires the refusal.
#
# The `gates-pwsh` steps of the same job run the .ps1 originals, so a twin that drifts
# from its original shows up as one step red and the other green.
set -uo pipefail
root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$root"
rc=0
echo "== gateway routing (Python twin)"
python3 scripts/checks/check_llm_gateway_routing.py || rc=1
echo ""
echo "== line endings (Python twin)"
python3 scripts/checks/validate_lineendings.py || rc=1
exit $rc
