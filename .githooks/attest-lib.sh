# attest-lib.sh - the ONE definition of "this content and this message were validated".
#
# NOT A HOOK. git only runs files named after a hook, so this sits in .githooks/ purely so
# that the attester (.githooks/commit-msg) and the verifiers (.githooks/reference-transaction
# and scripts/checks/check-hook-attestation.ps1) cannot drift apart.
#
# ATTEST-FORMAT: v2   <- the ledger line is "<tree> <message-digest> <iso> <branch>", and the
#                        message digest is over the EXACT BYTES git stores. Both verifiers
#                        look for this token in the hook that ran for a commit before they
#                        demand a v2 pair; changing the format means changing the token.
#
# WHY THE MESSAGE IS PART OF THE ATTESTATION (2026-08-30, from a refutation that reproduced
# the hole by execution). Attestation used to record the TREE only, and it was written by
# .githooks/pre-commit - the FIRST hook in the chain. commit-msg runs AFTER pre-commit, so:
#
#   git commit -m "OB1 -> deadbee1, pushed before this bump"
#       -> pre-commit passes and ATTESTS the tree; commit-msg refuses the bogus SHA;
#          HEAD does not move - but the ledger entry SURVIVES the abort.
#   git commit --no-verify -m "<the identical rejected message>"
#       -> no hooks run; the tree is in the ledger; reference-transaction waves it through.
#          LANDED, and with no guard-log line: unstopped AND unaudited.
#
# THE FIX IS ORDERING, not more checks: attestation moved to commit-msg, the LAST hook that
# can veto, and it records (tree, message). A hook chain that aborts anywhere now leaves
# NOTHING attested.
#
# ---------------------------------------------------------------------------------------
# WHY THE ATTESTER REWRITES THE MESSAGE INSTEAD OF REDUCING IT (2026-08-30, round 3).
#
# The first version of this file digested a REDUCTION of the message - the same `sed` +
# `git stripspace --strip-comments` applied to the attester's file and to the verifier's
# stored message - and claimed the reduction was idempotent, so "a message rewrite is new
# content as far as the ledger is concerned". THAT CLAIM WAS FALSE, and a verifier landed a
# lie through the gap in one command:
#
#   git commit --amend --no-verify -m '# OB1 -> deadbee1, pushed before this bump
#   <the already-attested subject>'
#       -> exit 0. HEAD MOVED. No guard-log line. `git log --oneline` shows the '#' line as
#          the SUBJECT, because `-m` selects cleanup=whitespace and git stores '#' lines
#          VERBATIM - while the verifier's reduction threw that line away before hashing.
#
# Any text a reduction DISCARDS but git STORES is an unguarded rewrite channel. Comment
# lines were one spelling of it; a `# ---- >8 ----` scissors line with prose under it was
# another. Patching those two spellings would have left the next one.
#
# SO THE REDUCTION IS GONE FROM THE VERIFY SIDE. The attester CANONICALISES the message file
# in place, and the verifier hashes the STORED BYTES with no transformation at all:
#
#   attester:  canon(file) -> written back to the file -> digested -> ledger
#   verifier:  raw bytes of the commit's message       -> digested -> must be in the ledger
#
# There is nothing left to discard, so there is no channel to hide text in. This is a
# property of the code's SHAPE, not a list of shapes that were thought of: a byte that
# reaches the commit is a byte that was hashed, because it is the same file.
#
# THE INVARIANT THIS RESTS ON is that canon() output is a FIXED POINT of every cleanup mode
# git can apply after commit-msg returns - otherwise git would store something other than
# what was attested and honest commits would be denied. That is not asserted here; it is
# enumerated and executed by scripts/agent-harness/verify-commit-path-guard.ps1 step 12.5,
# across `-m`, `-m` twice, `-F`, the editor path, all five --cleanup modes, a non-ASCII
# message, a PARTIAL commit (temporary index), a clean merge (MERGE_MSG) and a conflicted
# merge resolved by `git commit --no-edit`. Step 12 covers the other direction with the
# shapes the old reduction discarded: a leading '#' line, a '#' line mid-body, a scissors
# block, extra blank runs and CRLF - each of which must now be REFUSED.
# Measured green on git 2.49.0.windows.1, 2026-08-30.
#
# VISIBLE, NOT SILENT. When canonicalisation changes the message the attester says so on
# stderr. Deleting a line of someone's commit message without telling them is the failure
# mode documented in the "explain file removals" rule; git's own default cleanup does the
# same removal, but silence about it is still wrong.

# The canonical form of a message: cut the `commit -v` / --cleanup=scissors block, then
# apply git's own cleanup. Idempotent (after --strip-comments there are no comment lines
# left for the sed to find), which is what lets the attester write it back to the file.
_attest_canon() {
    sed -e '/^#.*>8/,$d' | git stripspace --strip-comments
}

# Rewrite the message FILE to its canonical form, in place. Returns 0 always; prints a
# notice on stderr only when it actually changed something. `cat >` rather than `mv` so the
# path git handed us keeps its identity.
_attest_canonicalise_file() {
    _ac_file="$1"
    _ac_tmp="$1.attest.$$"
    _attest_canon < "$_ac_file" > "$_ac_tmp" 2>/dev/null || { rm -f "$_ac_tmp"; return 0; }
    _ac_before=$(git hash-object -t blob --stdin < "$_ac_file" 2>/dev/null)
    _ac_after=$(git hash-object -t blob --stdin < "$_ac_tmp" 2>/dev/null)
    if [ -n "$_ac_after" ] && [ "$_ac_before" != "$_ac_after" ]; then
        cat "$_ac_tmp" > "$_ac_file"
        echo "commit-msg: the message was canonicalised before attesting - comment lines and" >&2
        echo "  any scissors block are removed, trailing whitespace trimmed. What is attested" >&2
        echo "  is exactly what git will store, so nothing can ride along unchecked." >&2
    fi
    rm -f "$_ac_tmp"
    return 0
}

# digest of a message FILE, as bytes. No reduction: call _attest_canonicalise_file first.
_attest_digest_file() { git hash-object -t blob --stdin < "$1" 2>/dev/null; }

# The message a COMMIT actually stores, byte for byte.
#
# `git cat-file commit` and cut everything through the first EMPTY line. Not
# `git log -1 --format=%B`, which appends a newline of its own and therefore hashes to a
# different blob - measured on four commits drawn from a 14-shape corpus, all four
# differed.
#
# The header/body split is safe even for a signed commit: git writes multi-line headers
# (gpgsig) with every continuation line prefixed by a space, so the blank line inside an
# ASCII-armored signature is stored as " " and does not match /^$/. Exercised by the drill
# against a hand-built commit object carrying a gpgsig header.
_attest_stored_message() { git cat-file commit "$1" 2>/dev/null | sed -e '1,/^$/d'; }
_attest_digest_commit() { _attest_stored_message "$1" | git hash-object -t blob --stdin 2>/dev/null; }
