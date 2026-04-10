---
name: github-repo-analyzer
description: Instructions for using the GitHub Repo Analyzer tools to explore, read files, and search code in any public GitHub repository
---

# GitHub Repo Analyzer — Tool Usage Guide

You have access to the **GitHub Repo Analyzer** tools for exploring GitHub repositories. These tools use the free GitHub REST API to fetch repo metadata, read source files, and search code. You then analyze and summarize the results yourself.

## Available Tools

### 1. `get_repo_overview`

**Purpose:** Get a comprehensive overview of a repository — metadata, languages, directory structure, and README. **Call this first** when a user asks about a repository.

| Parameter  | Type   | Required | Description                                               |
| ---------- | ------ | -------- | --------------------------------------------------------- |
| `repo_url` | string | Yes      | `https://github.com/owner/repo` or `owner/repo` shorthand |

**Usage rules:**

- Always start here when a user shares a repo URL.
- Accepts both full URLs and `owner/repo` shorthand.
- Returns the README, file tree, and metadata in one call — usually enough to answer general questions.

### 2. `get_repo_file`

**Purpose:** Read a specific file's contents from the repository. Use this to examine source code, configs, or documentation in detail.

| Parameter   | Type   | Required | Description                                              |
| ----------- | ------ | -------- | -------------------------------------------------------- |
| `repo_url`  | string | Yes      | Repository URL or `owner/repo`                           |
| `file_path` | string | Yes      | Path within the repo, e.g. `src/main.py`, `package.json` |

**Usage rules:**

- Use the file tree from `get_repo_overview` to identify which files to read.
- If you pass a directory path, it lists the directory contents instead.
- Large files are automatically truncated; focus on the most relevant files.

### 3. `search_repo_code`

**Purpose:** Search for code patterns, function names, class names, or keywords within the repository.

| Parameter  | Type   | Required | Description                                       |
| ---------- | ------ | -------- | ------------------------------------------------- |
| `repo_url` | string | Yes      | Repository URL or `owner/repo`                    |
| `query`    | string | Yes      | Search terms — function names, keywords, patterns |

**Usage rules:**

- Use specific terms: function names, class names, imports, config keys.
- Combine terms for better results, e.g. `def authenticate` or `import redis`.
- Returns matching files with code snippets.
- Requires a GitHub token for text match snippets (without token, only file paths are returned).

## Recommended Workflow

### General repo analysis:

1. **Call `get_repo_overview`** — get the big picture (metadata, tree, README).
2. **Read key files** — use `get_repo_file` on entry points like `main.py`, `index.ts`, `package.json`, `Cargo.toml`, `pyproject.toml`, etc.
3. **Summarize** — synthesize findings into a clear answer for the user.

### Answering specific questions:

1. **Call `get_repo_overview`** — scan the file tree and README for clues.
2. **Search** — use `search_repo_code` to find relevant code (e.g. `search_repo_code("owner/repo", "authentication")`)
3. **Read found files** — use `get_repo_file` to examine the matches in detail.
4. **Explain** — provide a clear answer citing the specific files you read.

### Deep dive workflow:

1. Overview → identify key directories
2. Read `get_repo_file` on the directory to list contents
3. Read individual source files
4. Search for cross-cutting concerns (error handling, logging, config patterns)

## Analysis Strategies

When analyzing a repository, look for these in order:

- **Purpose:** README + repo description
- **Tech stack:** Language breakdown, dependency files (`package.json`, `requirements.txt`, `go.mod`, `Cargo.toml`)
- **Architecture:** Directory structure, entry points, module organization
- **Key patterns:** How routing, data models, error handling, and configuration work
- **Quality signals:** Tests, CI configs, linting, type checking

## Error Handling

- **404 Not Found** → repo doesn't exist or is private. Verify the URL.
- **403 Rate Limit** → tell the user to add a GitHub personal access token in tool settings (raises limit from 60 to 5,000 requests/hour).
- **Empty results** → try different search terms or read files directly.
- Do not fabricate results. If a tool returns an error or empty data, say so.
