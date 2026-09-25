#!/usr/bin/env bash
# run-suite.sh <agent-bridge|little-coder> - one subproject's pytest suite, the way it
# runs on a developer machine today: a fresh venv, the project's own dependencies, pytest.
#
# Used by the `suite-agent-bridge` and `suite-little-coder` jobs in
# .github/workflows/ci.yml, and runnable unchanged in any Linux shell with python3 (3.12;
# little-coder declares >=3.12) and the venv module - which is how the jobs were proven
# in a fresh ubuntu:24.04 clone before they were wired in.
#
# HOW EACH SUITE RUNS TODAY, and what this copies:
#   agent-bridge  agent-org/README.md "Tests" says `pip install -e .[test] && pytest -q`,
#                 and the operator's log (agent-org/docs/log/P8-org-self-knowledge.md)
#                 runs it from a host venv. That extra ALONE CANNOT COLLECT THE SUITE:
#                 app/github_app.py imports `jwt` (PyJWT), which is in requirements.txt
#                 but not in the [test] extra, so 80 test modules fail to import
#                 (measured at e9caf6b in ubuntu:24.04). The venv therefore also takes
#                 requirements.txt - the pins the service image installs - which is the
#                 environment the code actually runs in. Nothing is skipped or deselected.
#   little-coder  little-coder/AGENTS.md "Run the full test suite": `python -m pytest -q`
#                 from little-coder/, with the package installed (`[dev]` brings pytest).
#                 tests/conftest.py puts src/ and git-proxy/ on sys.path. git must be on
#                 PATH (test_git_proxy exercises real git).
#
# Neither suite needs a GPU, a secret, Docker or the network once its dependencies are
# installed. Exit code is pytest's (0 = every collected test passed).
set -euo pipefail

suite="${1:-}"
root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
venv="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/venv-$suite"

case "$suite" in
  agent-bridge)
    dir="$root/agent-org/agent-bridge"
    python3 -m venv "$venv"
    "$venv/bin/python" -m pip install -q --upgrade pip
    "$venv/bin/python" -m pip install -q -r "$dir/requirements.txt" -e "$dir[test]"
    ;;
  little-coder)
    dir="$root/little-coder"
    python3 -m venv "$venv"
    "$venv/bin/python" -m pip install -q --upgrade pip
    "$venv/bin/python" -m pip install -q -e "$dir[dev]"
    ;;
  *)
    echo "usage: $0 <agent-bridge|little-coder>" >&2
    exit 2
    ;;
esac

echo "== $suite: $("$venv/bin/python" --version), $("$venv/bin/python" -m pytest --version 2>&1 | head -1)"
cd "$dir"
# -p no:cacheprovider: the job must not leave a .pytest_cache in the checkout, and
# a stale cache must never decide what runs. --durations=15 only REPORTS the slowest
# tests (agent-bridge's full run is about half an hour); it selects nothing.
exec "$venv/bin/python" -m pytest -q -p no:cacheprovider --durations=15
