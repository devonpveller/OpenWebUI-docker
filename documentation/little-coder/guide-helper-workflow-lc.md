# little-coder — Step-by-Step Workflow Guide

> **Audience:** the operator, driving little-coder from **Windows Command Prompt
> (cmd.exe)**. Keep this open on a second monitor and copy-paste each command
> in order.
>
> Every fenced block below is **one command** — copy the whole block, paste it
> into Command Prompt, press Enter. Replace anything in `UPPERCASE` (e.g.
> `OWNER/REPO`, `BRANCH`) with your real value.

---

<a id="toc"></a>

## ⚡ Quick command chart

Run top to bottom. The **ID** column matches the detailed section headings
below (`A1`, `B3`, …) — jump down there only when you need the full
explanation, then use the **↑ Back to chart** link to return here.

| ID                                                                           | What                        | Command(s) — copy the line you need                                                                                                              |
| ---------------------------------------------------------------------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| [**A1**](#a1-open-command-prompt-in-the-project-folder)                      | Open cmd in the project     | `cd /d "D:\Open WebUI\ai-stack"`                                                                                                                 |
| [**A2**](#a2-private-repos-only-add-your-github-token)                       | Token for private repos     | `notepad .env` — then set `LC_DEPLOY_TOKEN=github_pat_...`                                                                                       |
| [**A3**](#a3-non-github-host-only-add-your-git-host-to-the-egress-allowlist) | Non-GitHub host (else skip) | `notepad little-coder\docker\egress-allowlist.txt`<br>`docker compose build lc-egress`<br>`docker compose up -d lc-egress`                       |
| [**A4**](#a4-build-the-images-and-start-the-services)                        | Build + start the stack     | `docker compose build little-coder open-terminal lc-egress`<br>`docker compose up -d`                                                            |
| [**A5**](#a5-confirm-everything-is-healthy)                                  | Verify healthy              | `docker compose ps`<br>`docker exec little-coder lc status`                                                                                      |
| [**B1**](#b1-check-the-stack-and-current-focus)                              | Check status / focus        | `docker exec little-coder lc status`                                                                                                             |
| [**B2**](#b2-point-little-coder-at-the-repo-you-want-to-work-on)             | Pick the repo               | `docker exec little-coder lc project https://github.com/OWNER/REPO`                                                                              |
| [**B3**](#b3-start-an-interactive-agent-session)                             | Start the agent session     | `docker exec -it -u lc -w /workspace little-coder little-coder --model llamacpp/qwen36-27b`                                                      |
| [**B4**](#b4-work-with-the-agent)                                            | Work with the agent         | In-session: tell it to **create a branch** and to **commit**. Exit with **Ctrl+C**.                                                              |
| [**B5**](#b5-review-the-agents-work)                                         | Review the work             | `docker exec -w /workspace open-terminal git.real branch --show-current`<br>`docker exec -w /workspace open-terminal git.real log --oneline -10` |
| [**B6**](#b6-push-when-youre-satisfied)                                      | Push (replace `BRANCH`)     | `docker exec -it -w /workspace open-terminal git.real push origin BRANCH`                                                                        |
| [**B7**](#b7-continue-or-switch)                                             | Loop                        | Same repo → [**B3**](#b3-start-an-interactive-agent-session) · New repo → [**B2**](#b2-point-little-coder-at-the-repo-you-want-to-work-on)       |

---

## The pieces (30-second orientation)

| Name                            | What it is                                                                       |
| ------------------------------- | -------------------------------------------------------------------------------- |
| **`little-coder`** (container)  | Runs the AI coding agent + the control daemon.                                   |
| **`little-coder`** (command)    | The AI coding agent itself — you chat with it.                                   |
| **`lc`** (command)              | The operator wrapper — pick a repo, check status, fire tracked tasks.            |
| **`open-terminal`** (container) | Where the agent's commands actually run (network-isolated).                      |
| **workspace**                   | One git repo at a time, cloned inside the stack. The agent only ever works here. |

**The flow:** pick a repo with `lc project` → chat with `little-coder` → review → push.

---

# Part A — One-time setup

Do this once (or again only after pulling new little-coder code).

### A1. Open Command Prompt in the project folder

```
cd /d "D:\Open WebUI\ai-stack"
```

<sub>[↑ Back to chart](#toc)</sub>

### A2. (Private repos only) Add your GitHub token

Open the environment file:

```
notepad .env
```

Find the line `LC_DEPLOY_TOKEN=` and paste your token after the `=`, so it reads:

```
LC_DEPLOY_TOKEN=github_pat_xxxxxxxxxxxxxxxxxxxxxxxx
```

Use a **fine-grained PAT**: GitHub → Settings → Developer settings →
Fine-grained tokens → scope it to **only** your repo → Repository permissions →
**Contents: Read and write**. Save and close the file.

> Skip A2 entirely if you only work with **public** repos.

<sub>[↑ Back to chart](#toc)</sub>

### A3. (Non-GitHub host only) Add your git host to the egress allowlist

GitHub is allowed by default. For GitLab / Bitbucket / self-hosted, edit:

```
notepad little-coder\docker\egress-allowlist.txt
```

Add a line for your host (e.g. `^(.*\.)?gitlab\.com$`), save, then rebuild the
proxy:

```
docker compose build lc-egress
```

```
docker compose up -d lc-egress
```

<sub>[↑ Back to chart](#toc)</sub>

### A4. Build the images and start the services

```
docker compose build little-coder open-terminal lc-egress
```

```
docker compose up -d little-coder
```

<sub>[↑ Back to chart](#toc)</sub>

### A5. Confirm everything is healthy

```
docker compose ps
```

Look for `little-coder`, `lc-mcpo`, and `open-terminal` showing **(healthy)**.
Then confirm the daemon answers:

```
docker exec little-coder lc status
```

You should see JSON with `"status": "ok"`.

<sub>[↑ Back to chart](#toc)</sub>

---

# Part B — Each work session

This is the repeatable loop. `docker exec` commands work from any folder, so you
don't need to `cd` again — but `docker compose` commands do.

### B1. Check the stack and current focus

```
docker exec little-coder lc status
```

`"focus"` shows which repo is currently loaded (or `null` if none).

<sub>[↑ Back to chart](#toc)</sub>

### B2. Point little-coder at the repo you want to work on

```
docker exec little-coder lc project https://github.com/OWNER/REPO
```

- Replace `OWNER/REPO` with your repository.
- If it's the **same** repo already focused → no change.
- If it's a **different** repo → the workspace is wiped and the new repo is
  cloned (the prior state is tagged first).
- Private repos require the token from step A2.

<sub>[↑ Back to chart](#toc)</sub>

### B3. Start an interactive agent session

```
docker exec -it -u lc -w /workspace little-coder little-coder --model llamacpp/qwen36-27b
```

This drops you into the little-coder agent, working inside the focused repo.

<sub>[↑ Back to chart](#toc)</sub>

### B4. Work with the agent

Type your request at the agent's prompt and press Enter. The agent reads,
edits, and writes files and runs commands, then responds. Keep the conversation
going — it's interactive.

**Two things the agent will NOT do unless you tell it:**

1. **Work on a branch.** Start with something like:
   > `Create a branch lc/my-change and do all work there.`
2. **Commit.** When you're happy with a change, tell it:
   > `Commit the changes with a clear message.`

When you're done, exit the agent with **Ctrl+C** (press twice if needed).
Type `/help` inside the session to see the agent's own commands.

> **Alternative — one-shot task** (tracked/journaled, no chat):
>
> ```
> docker exec -it little-coder lc task "add a --verbose flag and commit it"
> ```

<sub>[↑ Back to chart](#toc)</sub>

### B5. Review the agent's work

See which branch you're on:

```
docker exec -w /workspace open-terminal git.real branch --show-current
```

See recent commits:

```
docker exec -w /workspace open-terminal git.real log --oneline -10
```

See the most recent commit's contents:

```
docker exec -w /workspace open-terminal git.real show --stat HEAD
```

Check for anything uncommitted:

```
docker exec -w /workspace open-terminal git.real status
```

<sub>[↑ Back to chart](#toc)</sub>

### B6. Push when you're satisfied

Replace `BRANCH` with the branch name from B5:

```
docker exec -it -w /workspace open-terminal git.real push origin BRANCH
```

With the token from A2 set, this pushes without prompting. (Operator git
bypasses the safety proxy by design — this is the intended push path.)

<sub>[↑ Back to chart](#toc)</sub>

### B7. Continue or switch

- **Keep working the same repo** → go back to **B3** (start a new agent
  session) or **B4** (continue in the open one).
- **Switch to a different repo** → go back to **B2**.

<sub>[↑ Back to chart](#toc)</sub>

---

## Quick reference (cheat sheet)

```
:: --- status / project ---
docker exec little-coder lc status
docker exec little-coder lc project https://github.com/OWNER/REPO
docker exec little-coder lc tasks

:: --- run the agent (interactive) ---
docker exec -it -u lc -w /workspace little-coder little-coder --model llamacpp/qwen36-27b

:: --- review (run in open-terminal) ---
docker exec -w /workspace open-terminal git.real branch --show-current
docker exec -w /workspace open-terminal git.real log --oneline -10
docker exec -w /workspace open-terminal git.real show --stat HEAD
docker exec -w /workspace open-terminal git.real status

:: --- push ---
docker exec -it -w /workspace open-terminal git.real push origin BRANCH

:: --- one-shot task instead of a chat ---
docker exec -it little-coder lc task "describe the change you want"
```

<sub>[↑ Back to chart](#toc)</sub>

---

## Troubleshooting

| Symptom                                                | Fix                                                                                        |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| `409: no project focused`                              | Run **B2** (`lc project ...`) first.                                                       |
| `lc: error: the following arguments are required: cmd` | You ran `lc` with no subcommand — add one (`status`, `project`, `task`, `tasks`, `admin`). |
| Push asks for a password / fails auth                  | Token missing — redo **A2**, then `docker compose up -d little-coder`.                     |
| Private repo won't clone                               | Same as above — the token must be set before `lc project`.                                 |
| Agent seems stuck / looping                            | Stop it: `docker kill little-coder` then `docker compose up -d little-coder lc-mcpo`.      |
| See what the daemon is doing                           | `docker logs little-coder --tail 50`                                                       |
| A service isn't healthy                                | `docker compose ps` then `docker logs SERVICE --tail 50`                                   |
| Restart everything cleanly                             | `docker compose up -d` (from the project folder)                                           |

<sub>[↑ Back to chart](#toc)</sub>

---

## Optional — shorter commands (doskey)

To shorten the long commands **for the current Command Prompt window**, paste
these once after opening cmd:

```
doskey lc=docker exec -it little-coder lc $*
```

```
doskey agent=docker exec -it -u lc -w /workspace little-coder little-coder --model llamacpp/qwen36-27b
```

Then you can type `lc status`, `lc project ...`, or just `agent` to start a
session. (doskey macros last only for that window — re-paste them in a new one,
or add them to a cmd startup script.)

<sub>[↑ Back to chart](#toc)</sub>

---

## What needs credentials (and what doesn't)

| Action                                              | Credential?                              |
| --------------------------------------------------- | ---------------------------------------- |
| Agent edits / reads / writes files                  | No                                       |
| Agent runs builds, tests, `git commit`              | No — commits are local                   |
| Cloning a **private** repo, `git push`, `git fetch` | Yes — the `LC_DEPLOY_TOKEN` from step A2 |

The token is least-privilege (one repo, contents-only) by design — see
`Self-improving-little-coder-design.md` §10.3.

<sub>[↑ Back to chart](#toc)</sub>
