#!/usr/bin/env bash
# linux-hooks.sh <expected-host> - a fresh Linux clone commits through .githooks.
#
# <expected-host> is the gate host .githooks/pre-commit must pick in this environment:
#   pwsh     PowerShell 7 is on PATH (ubuntu-latest has it): every gate runs
#   python3  no PowerShell at all: the three never-skipped gates run through their
#            Python twins, the rest print SKIPPED (the `hooks-linux` job runs this
#            mode inside a python:3.12-slim container, which has no pwsh)
#
# What it does, against a FRESH clone of the repository it lives in (a `git clone`,
# so file modes come from git's objects, never from this checkout's filesystem):
#   1. every file under .githooks/ that git will run as a hook is executable in the
#      clone, and executing one works (the verify step in .githooks/README.md)
#   2. `core.hooksPath .githooks`, then a clean doc-only commit SUCCEEDS, the hook
#      printed its gate summary naming <expected-host>, and an attestation line for the
#      committed tree was appended to <git-common-dir>/hook-attest.log
#   3. a file carrying a planted gateway key (`gw-` + 40 generated characters, made at
#      run time so no key literal is ever in the tree) is REFUSED: the commit exits
#      non-zero, the secret guard names the pattern, and HEAD does not move
#   4. a file carrying a private LAN address (assembled at run time) is REFUSED by the
#      identity gate, which names file:line and the class, and HEAD does not move
#
# A hook that lost its x bit makes git skip it silently, so step 3 would then COMMIT
# the key - which is exactly the regression this job exists to catch, and it fails
# on two separate checks (1 and 3). Exit 0 only when every check passed.
set -uo pipefail

expected="${1:-}"
case "$expected" in
  pwsh|python3) ;;
  *) echo "usage: $0 <pwsh|python3>" >&2; exit 2 ;;
esac

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
fails=0
check() {  # check <description> <0|nonzero>
  if [ "$2" -eq 0 ]; then echo "  [OK]   $1"; else echo "  [FAIL] $1"; fails=$((fails + 1)); fi
}

# READING THE SOURCE CHECKOUT. It may belong to another uid (the python3 step mounts the
# runner's checkout read-only into a container running as root), and git then refuses
# it as "dubious ownership". `-c safe.directory=*` is NOT enough: a local clone's
# upload-pack runs with GIT_CONFIG_PARAMETERS cleared. So the exception lives in a
# throwaway global config used only by the commands that read the source; the clone
# itself, where the commits happen, runs with the environment's own config.
printf '[safe]\n\tdirectory = *\n' > "$work/src.gitconfig"
srcgit() { GIT_CONFIG_GLOBAL="$work/src.gitconfig" git "$@"; }
src="$(srcgit -C "$(dirname "$0")" rev-parse --show-toplevel)" || { echo "not inside a git checkout" >&2; exit 2; }
sha="$(srcgit -C "$src" rev-parse --verify 'HEAD^{commit}')" || { echo "RESULT: FAIL (rev-parse)"; exit 1; }
srcgit clone -q --no-local --no-checkout "$src" "$work/clone" || { echo "RESULT: FAIL (clone)"; exit 1; }
cd "$work/clone"
# A CI checkout is often a detached HEAD (a pull request's merge ref); pin the clone to
# exactly the commit under test, on a local branch the commits below can move.
git checkout -q -B ci-hooks "$sha" || { echo "RESULT: FAIL (checkout $sha)"; exit 1; }
# THE OB1 SUBMODULE, as the README's `git clone --recurse-submodules` gives it. It is
# not optional for this job: with PowerShell present the corpus-exposure gate scans
# OB1's producers, and in a clone without them it finds zero insert sites and REFUSES
# every commit as vacuous (measured in this job's first local run). It is taken from
# the source checkout's own OB1 (the workflow checks out `submodules: recursive`), so
# this script makes no network call; a source without it is a failed check, not a skip.
if [ -e "$src/OB1/.git" ]; then
  git config submodule.OB1.url "$src/OB1"
  GIT_CONFIG_GLOBAL="$work/src.gitconfig" git -c protocol.file.allow=always submodule update -q --init OB1 \
    || { echo "RESULT: FAIL (OB1 submodule from $src/OB1)"; exit 1; }
  ob1_state="OB1 at $(git -C OB1 rev-parse --short HEAD) (the recorded gitlink is $(git ls-files -s OB1 | awk '{print substr($2,1,7)}'))"
else
  ob1_state="OB1 NOT AVAILABLE in the source checkout"
  echo "  [FAIL] the source checkout has no OB1 submodule to clone ($src/OB1) - check out with submodules"
  fails=$((fails + 1))
fi
git config user.email ci@example.invalid
git config user.name "ci hooks job"
git config core.hooksPath .githooks
echo "== fresh clone at $(git log -1 --format='%h %s' | cut -c1-90)"
echo "   $(uname -s), git $(git --version | awk '{print $3}'), expected gate host: $expected"
echo "   pwsh on PATH: $(command -v pwsh || echo no); python3: $(command -v python3 || echo no)"
echo "   $ob1_state"

echo ""
echo "== 1. the hooks can execute"
git ls-files -s -- .githooks
for hook in pre-commit commit-msg pre-merge-commit; do
  check ".githooks/$hook is executable in the clone" "$([ -x ".githooks/$hook" ]; echo $?)"
done
check "executing a hook works (./.githooks/commit-msg /dev/null)" \
  "$(./.githooks/commit-msg /dev/null >/dev/null 2>&1; echo $?)"

echo ""
echo "== 2. a clean commit succeeds through the hook"
ledger="$(git rev-parse --git-common-dir)/hook-attest.log"
printf '\n' >> .githooks/README.md
git add .githooks/README.md
out="$(git commit -m "ci: a clean doc-only commit" 2>&1)"; rc=$?
echo "$out" | sed 's/^/   | /'
check "the clean commit exited 0 (exit $rc)" "$rc"
check "the hook ran and named host '$expected' in its summary" \
  "$(echo "$out" | grep -q "Pre-commit gates (host: $expected)"; echo $?)"
tree="$(git rev-parse 'HEAD^{tree}')"
check "the attestation ledger has a line for the committed tree ($tree)" \
  "$(grep -q "^$tree " "$ledger" 2>/dev/null; echo $?)"

echo ""
echo "== 3. a planted gateway key is refused"
before="$(git rev-parse HEAD)"
key="gw-$(head -c 200 /dev/urandom | od -An -tx1 | tr -dc 'a-f0-9' | head -c 40)"
printf 'GATEWAY_KEY=%s\n' "$key" > ci-planted-key.txt
git add ci-planted-key.txt
out="$(git commit -m "ci: this commit must be refused" 2>&1)"; rc=$?
echo "$out" | sed 's/^/   | /'
check "the commit carrying the key exited non-zero (exit $rc)" "$([ "$rc" -ne 0 ]; echo $?)"
check "the secret guard named the gateway-key pattern" \
  "$(echo "$out" | grep -qi 'gateway key'; echo $?)"
check "HEAD did not move" "$([ "$(git rev-parse HEAD)" = "$before" ]; echo $?)"
check "the key's text is not echoed in the hook output" "$(echo "$out" | grep -qF "$key"; [ $? -ne 0 ]; echo $?)"
git rm -q --cached ci-planted-key.txt && rm -f ci-planted-key.txt

echo ""
echo "== 4. a planted personal identifier is refused (the identity gate, generic layer)"
# A private LAN address assembled at run time, so no such literal is in the tree.
before="$(git rev-parse HEAD)"
ip="192.168.$(( $$ % 200 + 20 )).$(( $$ % 150 + 50 ))"
printf 'upstream host is %s\n' "$ip" > ci-planted-identity.md
git add ci-planted-identity.md
out="$(git commit -m "ci: this commit must be refused too" 2>&1)"; rc=$?
echo "$out" | sed 's/^/   | /'
check "the commit carrying a LAN address exited non-zero (exit $rc)" "$([ "$rc" -ne 0 ]; echo $?)"
check "the identity gate named file:line and the class" \
  "$(echo "$out" | grep -q 'ci-planted-identity.md:1:[0-9]*: lan-ip'; echo $?)"
check "HEAD did not move" "$([ "$(git rev-parse HEAD)" = "$before" ]; echo $?)"

echo ""
if [ "$fails" -eq 0 ]; then echo "RESULT: PASS"; exit 0; fi
echo "RESULT: FAIL ($fails check(s))"
exit 1
