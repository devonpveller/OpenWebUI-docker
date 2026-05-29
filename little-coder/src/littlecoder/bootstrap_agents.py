"""Operator-triggered AGENTS.md bootstrap prompts (design §3.7 layer 3).

Three modes — exactly the three operations the operator-facing block
in `agent-knowledge/project-context.md` documents. The daemon endpoint
(`POST /admin/bootstrap-agents`) translates a mode into the matching
prompt and triggers a task; the Pipe and CLI shortcuts both call that
endpoint so the prompt strings live in exactly one place.

The prompts are deliberately specific (numbered steps, named commands)
so the agent's compliance is high — these are operator-initiated
actions, not autonomous reasoning.
"""

from __future__ import annotations

from typing import Literal

BootstrapMode = Literal["commit", "nocommit", "revert"]

VALID_MODES: tuple[BootstrapMode, ...] = ("commit", "nocommit", "revert")


_PROMPT_COMMIT = """\
Bootstrap AGENTS.md for the focused repo per the founding-knowledge
project-context rule. This is an explicit operator-triggered bootstrap,
not autonomous — execute deterministically, do not skip steps.

1. Run the four-command orient (git log -n 10 / git status -sb /
   ls -la / cat README.md or readme.*) to inform the file's content.
2. Identify the project-type file (package.json / pyproject.toml /
   Cargo.toml / go.mod / etc.) and read its build/test/run sections.
3. Skim 3-5 representative source files to spot conventions; do not
   cat the whole tree.
4. Write `AGENTS.md` at the workspace root following the template
   from project-context.md: "What this repo is", "How to work in
   it", "Layout", "Conventions noticed", "Keep this file in sync".
   Keep it under 200 lines — orient-fast, not be-exhaustive.
5. Commit it as a SEPARATE commit (do NOT bundle other changes):
   `git add AGENTS.md && git commit -m "Bootstrap AGENTS.md for
   agent orientation"`. The commit body should include the standard
   revert/opt-out instructions per project-context.md.
6. End your answer with the standard operator-facing block from
   project-context.md (📄 Bootstrapped AGENTS.md...) including the
   short-SHA of the bootstrap commit.

Do not make ANY other changes to the repo — no refactoring, no
incidental fixes. This task is bootstrap-only."""


_PROMPT_NOCOMMIT = """\
Bootstrap AGENTS.md for the focused repo per the founding-knowledge
project-context rule, but DO NOT COMMIT IT. This is an explicit
operator-triggered draft — the operator will review the file by
hand and decide whether to commit.

1. Run the four-command orient (git log -n 10 / git status -sb /
   ls -la / cat README.md or readme.*) to inform the file's content.
2. Identify the project-type file (package.json / pyproject.toml /
   Cargo.toml / go.mod / etc.) and read its build/test/run sections.
3. Skim 3-5 representative source files to spot conventions; do not
   cat the whole tree.
4. Write `AGENTS.md` at the workspace root following the template
   from project-context.md: "What this repo is", "How to work in
   it", "Layout", "Conventions noticed", "Keep this file in sync".
   Keep it under 200 lines.
5. DO NOT commit the file. Run `git status -sb` to confirm
   AGENTS.md shows as untracked / modified, then stop.
6. End your answer with EXACTLY this operator-facing block:

   ---
   📄 Drafted `AGENTS.md` at the workspace root (UNCOMMITTED). The
   file will be wiped on the next `/project` switch unless committed.
   Review from a host shell with `git diff AGENTS.md`, then:
   - Keep it: `git add AGENTS.md && git commit -m "Add AGENTS.md"`
   - Discard it: `rm AGENTS.md` (or `git restore AGENTS.md` if it
     was already tracked).

Do not make ANY other changes to the repo — no refactoring, no
incidental fixes. This task is draft-only."""


_PROMPT_REVERT = """\
Undo the AGENTS.md bootstrap for the focused repo, then permanently
opt out so future first-contact tasks don't re-create it.

Execute these steps IN ORDER and report what each one did:

1. `git log --oneline | grep -i "Bootstrap AGENTS.md" | head -1`
   If a commit is found, capture its SHA.

2. If a bootstrap commit was found in step 1:
   `git revert --no-edit <sha>`
   Capture the revert commit's SHA.

3. If NO bootstrap commit was found in step 1 but AGENTS.md is
   present in the working tree:
   - If AGENTS.md is tracked: `git rm AGENTS.md && git commit -m
     "Remove AGENTS.md"`
   - If AGENTS.md is untracked: `rm AGENTS.md` (no commit needed).

4. Create the opt-out marker:
   `touch .no-agents-md && git add .no-agents-md && git commit -m
   "Opt out of AGENTS.md bootstrap"`

5. End your answer summarizing exactly what fired and the resulting
   commit SHAs (or "no-op" for steps that didn't apply).

Do not make ANY other changes to the repo. This task is revert-and-
opt-out only."""


_PROMPTS: dict[BootstrapMode, str] = {
    "commit": _PROMPT_COMMIT,
    "nocommit": _PROMPT_NOCOMMIT,
    "revert": _PROMPT_REVERT,
}


def prompt_for(mode: BootstrapMode) -> str:
    """Return the agent prompt for the requested mode. Raises
    ValueError on unknown mode — the daemon endpoint surfaces it as
    422 to the caller."""
    if mode not in _PROMPTS:
        raise ValueError(
            f"unknown bootstrap mode {mode!r}; valid: {VALID_MODES}"
        )
    return _PROMPTS[mode]
