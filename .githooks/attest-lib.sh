# attest-lib.sh - the ONE definition of "this content and this message were validated".
#
# NOT A HOOK. git only runs files named after a hook, so this sits in .githooks/ purely so
# that the attester (.githooks/commit-msg) and the verifier (.githooks/reference-transaction)
# cannot drift apart. Two copies of a normalisation rule drift, and the copy that drifts is
# the one nobody looks at - here a drift would either deny honest commits or wave a bypass
# through, so it gets one definition and both sides source it.
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
#   git commit --amend --no-verify -m "<any new message>"
#       -> same shape. The tree is unchanged and already attested, so the message could be
#          rewritten to say anything at all.
#
# That is not a cosmetic hole. commit-msg is the hook enforcing the gitlink-SHA rule
# CLAUDE.md makes hard, and PLAN.md SS C.7 makes the commit message the operator's audit
# surface - the message channel was the one wide open.
#
# THE FIX IS ORDERING, not more checks: attestation moved to commit-msg, the LAST hook that
# can veto, and it records (tree, message). A hook chain that aborts anywhere now leaves
# NOTHING attested, and a message rewrite is new content as far as the ledger is concerned.
# `git commit --no-verify` skips pre-commit and commit-msg together, so there is no way to
# reach the attester without passing everything in front of it.
#
# NORMALISATION. The attester sees the message FILE (comments, scissors block, trailing
# blank lines still in it); the verifier sees the message git actually STORED. The same
# reduction is applied to both, and it is idempotent, so a stored message reduces to itself
# and a raw file reduces to what git would have stored. Measured green (2026-08-30) on:
# `-m`, `-F` with comment lines and trailing blanks, `--amend --no-edit`, a PARTIAL commit
# (`git commit -- path`, where git hands the hooks a temporary index), a clean merge
# (commit-msg IS invoked, with .git/MERGE_MSG) and a conflicted merge resolved by
# `git commit --no-edit`. None of those produce a guard line.

_attest_norm() {
    # Cut the `commit -v` / --cleanup=scissors block, then apply git's own cleanup.
    sed -e '/^#.*>8/,$d' | git stripspace --strip-comments
}

# digest of a message FILE (commit-msg's $1)
_attest_digest_file() { _attest_norm < "$1" 2>/dev/null | git hash-object -t blob --stdin 2>/dev/null; }

# digest of the message already stored in a COMMIT (reference-transaction's new value)
_attest_digest_commit() { git log -1 --format=%B "$1" 2>/dev/null | _attest_norm 2>/dev/null | git hash-object -t blob --stdin 2>/dev/null; }
