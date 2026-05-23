# Reading the project — start here on every task

Each task is fresh: you have only the prompt and the workspace. The repo
itself, plus `git` history, IS your memory. Read enough to act; stop before
you have listed everything. The cheaper your orientation, the more of your
budget is available for the actual task.

## Orient in four commands

These answer most "what is this repo?" questions in seconds. Run them
BEFORE asking yourself questions you can answer from the files.

1. **Recent history** — what has been happening:
   `git log --oneline -n 10`
2. **Working state** — current branch and any uncommitted changes:
   `git status -sb`
3. **Top-level layout** — what files exist at the root:
   `ls -la`
4. **Purpose** — read the README if there is one:
   `cat README.md` (or `cat README.*` if the extension differs)

Stop here unless the task needs more.

## Identify the project type

Pick the one root file that names the project; you do not need to inspect
all of them.

- `package.json` → Node / TypeScript. Read `scripts` + `dependencies`.
- `pyproject.toml` / `setup.py` → Python. Read `[project]` or
  `install_requires`.
- `Cargo.toml` → Rust. Read `[dependencies]`.
- `go.mod` → Go.
- `pom.xml` / `build.gradle` → Java / Kotlin.
- `Gemfile` → Ruby.

## When you need more

- Find where something is defined:
  `git grep -n '<symbol>'` — faster and more accurate than `find` + `cat`.
- Recent changes touching a path:
  `git log --oneline -n 10 -- <path>`.
- Files added or changed in the last few commits:
  `git log -n 5 --name-only`.
- Authoritative file inventory:
  `git ls-files` (pipe through `head` if large).

## Anti-patterns

- Do **not** `cd`. `/workspace` is already your cwd; relative paths work.
- Do **not** `find /` or walk outside `/workspace` — there is nothing useful
  there. Inside the workspace, `git ls-files` is the inventory.
- Do **not** `cat` every file. Read the README and the project-type file,
  then go directly to what the task touches.
- Do **not** repeat orientation reads later in the task. You already know
  the layout — trust it.

## Why this works

You are stateless across tasks — every task starts blank. But project state
lives in two durable places that survive your restart: the **filesystem**
(what is there) and **`git` history** (how it got that way). Reading them
cheaply at the start of a task replaces the urge to re-derive context turn
by turn, and leaves your context budget for the real work.
