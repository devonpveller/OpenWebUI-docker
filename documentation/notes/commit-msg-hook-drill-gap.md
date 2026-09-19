# Finding — `.githooks/commit-msg` is verified but not drilled (2026-08-29)

## What the hook does

Refuses a commit that stages a submodule gitlink while naming a SHA that resolves nowhere.
Written after commit `1da68b8` said "OB1 -> 5a54f18, pushed before this bump": the gitlink
was correct and pushed (`2fb2419`), but `5a54f18` is not a commit in OB1 or anywhere — it
was typed from memory. The submodule rule here is "never bump to a commit that isn't on the
OB1 remote", and the message is where a reader checks that.

## It is verified

Run directly against a scratch repository with a real submodule, under `GIT_DIR` set the
way git sets it:

    GIT_DIR="$R/.git" sh .githooks/commit-msg <msg-naming-real-sha>   -> exit 0
    GIT_DIR="$R/.git" sh .githooks/commit-msg <msg-naming-bogus-sha>  -> exit 1

and against hex-looking English ("the defaced facade was added in a decade") -> exit 0,
which is the case that stops it failing honest messages.

It also caught a real defect in itself: the first version resolved the submodule SHA with
`git -C "$sub" cat-file`, and git runs hooks with `GIT_DIR` pointing at the PARENT repo,
which wins over `-C`. Every honest bump would have been refused. The lookup now unsets
`GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` first.

## What is missing

A case in `verify-merge-protocol.ps1`, so it stays covered without someone remembering.

Three attempts failed, each for a harness reason rather than a hook reason — and each one
first presented as a verdict about the hook:

1. `GIT_DIR` set from `rev-parse --git-dir` is RELATIVE (`.git`), and git re-resolves a
   relative `GIT_DIR` against the directory `-C` moved to — so `git -C SUB` landed on
   `SUB/.git` and found the commit anyway. The bug could not reproduce; the case passed
   with the fix reverted.
2. `Push-Location` is not the working directory a native child inherits (PS 5.1 does not
   sync `[Environment]::CurrentDirectory`), so `sh.exe` ran from the drill's directory,
   where the submodule was not.
3. A `-replace '\', '/'` written into the script was a lone-backslash regex that threw on
   every call, failing all three cases at once.

The cases were removed rather than shipped red. A drill that is normally three-red teaches
people to ignore it, which costs more than the coverage it was meant to add.

## To close this

Drive the hook from the drill with all three conditions right at once: an ABSOLUTE
`GIT_DIR` for the scratch repo, the child process's working directory set to that repo
(`Start-Process -WorkingDirectory`, verified — not `Push-Location`), and no regex
constructed by string-substitution into the script. Assert RED with the fix reverted before
believing the GREEN, which is the step that caught attempt 1.
