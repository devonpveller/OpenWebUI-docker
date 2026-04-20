"""
title: GitHub Repo Analyzer
author: ai-stack
version: 5.1.0
description: Give any model the ability to progressively explore GitHub repositories using the free GitHub REST API. Returns compact results first so models can decide what to drill into. Supports branch selection, file previews, bulk reads, code search, commits, branch comparison, and feature validation.
required_open_webui_version: 0.4.0
requirements: requests
"""

from pydantic import BaseModel, Field
from typing import Optional, List
import requests
import re
import base64

GITHUB_API_BASE = "https://api.github.com"


class Tools:
    class Valves(BaseModel):
        GITHUB_TOKEN: str = Field(
            default="",
            description="GitHub personal access token (optional). Without one: 60 requests/hour. With one: 5,000/hour. Create at https://github.com/settings/tokens",
        )
        REQUEST_TIMEOUT: int = Field(
            default=30,
            description="Timeout in seconds for GitHub API requests.",
        )
        MAX_FILE_SIZE: int = Field(
            default=50000,
            description="Maximum file content size in characters to return (prevents context overflow).",
        )
        OVERVIEW_README_CHARS: int = Field(
            default=500,
            description="Max README characters in overview. Just enough to understand the project. Use get_repo_file('README.md') for full.",
        )
        OVERVIEW_TREE_MAX_ITEMS: int = Field(
            default=60,
            description="Max entries in the overview root tree.",
        )
        PREVIEW_LINES: int = Field(
            default=50,
            description="Lines returned per file in preview mode (bulk_read_files preview=true, get_repo_file max_lines).",
        )
        CONTEXT_BUDGET: int = Field(
            default=0,
            description="Total character budget across all tool calls in a conversation. As usage approaches this limit, outputs are automatically compressed. Set to 0 to disable. Disabled by default — the GitHelper pipe manages context overflow.",
        )

    def __init__(self):
        self.valves = self.Valves()
        self._context_used = 0

    def _budget_wrap(self, result: str) -> str:
        """Track cumulative output and auto-truncate when approaching the context budget.
        As budget fills: 80% → warn, 90% → hard truncate, 100% → refuse with summary hint."""
        budget = self.valves.CONTEXT_BUDGET
        if not budget:
            return result

        ratio = self._context_used / budget
        result_len = len(result)

        # Over budget — return only a short pointer
        if ratio >= 1.0:
            self._context_used += 200
            return (
                "**Context budget exhausted.** "
                "Your previous tool results contain all the data gathered so far. "
                "Recall your FileShed notes with `shed_read_file` and synthesize your answer now."
            )

        # 90%+ — hard truncate to fit remaining budget
        remaining = budget - self._context_used
        if ratio >= 0.9 or result_len > remaining:
            cap = max(2000, int(remaining * 0.8))
            if result_len > cap:
                result = result[:cap] + (
                    f"\n\n... **[Auto-truncated: {result_len - cap} chars cut — context budget {int(ratio * 100)}% used]**\n"
                    "*Shed your analysis notes now and synthesize your answer from FileShed.*"
                )

        # 80%+ — append a warning
        elif ratio >= 0.8:
            result += (
                f"\n\n---\n*Context budget ~{int(ratio * 100)}% used. "
                "Shed your notes now and wrap up, or remaining tool calls will be auto-truncated.*"
            )

        self._context_used += len(result)
        return result

    def _build_headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.valves.GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {self.valves.GITHUB_TOKEN}"
        return headers

    def _parse_repo(self, repo_url: str) -> Optional[tuple]:
        """Extract owner, repo, branch, and file path from a GitHub URL.

        Handles all common GitHub URL patterns:
          https://github.com/owner/repo
          https://github.com/owner/repo/tree/branch-name
          https://github.com/owner/repo/blob/branch-name/path/to/file.ts
          owner/repo  (shorthand)

        Returns (owner, repo, branch_or_None, filepath_or_None) or None on failure.
        """
        url = repo_url.strip().rstrip("/")

        # Full GitHub URL with optional /tree/branch or /blob/branch/path
        match = re.match(
            r"https?://github\.com/([\w\-\.]+)/([\w\-\.]+?)(?:\.git)?(?:/(tree|blob)/(.+))?$",
            url,
        )
        if match:
            owner = match.group(1)
            repo = match.group(2)
            url_type = match.group(3)  # "tree", "blob", or None
            remainder = match.group(4)  # "branch" or "branch/path/to/file"

            branch = None
            file_path = None
            if remainder:
                if url_type == "blob":
                    # blob URLs: first segment is branch, rest is file path
                    # Handle multi-segment branch names by checking against known patterns
                    parts = remainder.split("/", 1)
                    branch = parts[0]
                    file_path = parts[1] if len(parts) > 1 else None
                else:
                    # tree URLs: everything is the branch (could contain slashes)
                    branch = remainder

            return owner, repo, branch, file_path

        # owner/repo shorthand (no branch info)
        match = re.match(r"^([\w\-\.]+)/([\w\-\.]+)$", url)
        if match:
            return match.group(1), match.group(2), None, None

        return None

    def _ref_param(self, branch: Optional[str], prefix: str = "?") -> str:
        """Build a ?ref=branch or &ref=branch query parameter."""
        if branch:
            return f"{prefix}ref={requests.utils.quote(branch, safe='')}"
        return ""

    def _get(self, url: str) -> requests.Response:
        return requests.get(
            url,
            headers=self._build_headers(),
            timeout=self.valves.REQUEST_TIMEOUT,
        )

    def list_branches(self, repo_url: str) -> str:
        """
        List all branches in a GitHub repository with their latest commit info.
        Use this when a user asks about branches, or before comparing branches, or when
        you need to discover which branches exist in a repository.

        :param repo_url: GitHub repository URL (e.g. https://github.com/owner/repo) or owner/repo shorthand
        :return: List of branches with their latest commit SHA and date
        """
        parsed = self._parse_repo(repo_url)
        if not parsed:
            return "Error: Invalid repository URL."

        owner, repo, _, _ = parsed

        try:
            # Get default branch info first
            r = self._get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}")
            default_branch = "main"
            if r.status_code == 200:
                default_branch = r.json().get("default_branch", "main")

            # List branches (paginated, up to 100)
            r = self._get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/branches?per_page=100"
            )
            if r.status_code == 404:
                return f"Error: Repository {owner}/{repo} not found."
            if r.status_code == 403:
                return "Error: GitHub API rate limit exceeded. Add a GITHUB_TOKEN in tool settings."
            r.raise_for_status()
            branches = r.json()

            if not branches:
                return f"No branches found in {owner}/{repo}."

            lines = [f"**Branches in {owner}/{repo}** ({len(branches)} total)\n"]
            for b in branches:
                name = b.get("name", "unknown")
                sha = b.get("commit", {}).get("sha", "")[:7]
                is_default = " ← default" if name == default_branch else ""
                protected = " 🔒" if b.get("protected", False) else ""
                lines.append(f"- `{name}` ({sha}){is_default}{protected}")

            return self._budget_wrap("\n".join(lines))

        except requests.RequestException as e:
            return f"Error listing branches: {str(e)}"

    def compare_branches(self, repo_url: str, base: str, head: str) -> str:
        """
        Compare two branches in a GitHub repository. Shows commits ahead/behind, changed files, and diff stats.
        Use this when a user wants to see what changed between branches, or to compare a feature branch against main.

        :param repo_url: GitHub repository URL (e.g. https://github.com/owner/repo) or owner/repo shorthand
        :param base: Base branch name (e.g. "main") — the reference point
        :param head: Head branch name (e.g. "feature-branch") — the branch with new changes
        :return: Comparison summary with changed files and stats
        """
        parsed = self._parse_repo(repo_url)
        if not parsed:
            return "Error: Invalid repository URL."

        owner, repo, _, _ = parsed

        try:
            base_encoded = requests.utils.quote(base.strip(), safe="")
            head_encoded = requests.utils.quote(head.strip(), safe="")
            r = self._get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/compare/{base_encoded}...{head_encoded}"
            )
            if r.status_code == 404:
                return f"Error: Could not compare '{base}' and '{head}' in {owner}/{repo}. Check that both branches exist."
            if r.status_code == 403:
                return "Error: GitHub API rate limit exceeded. Add a GITHUB_TOKEN in tool settings."
            r.raise_for_status()
            data = r.json()

            status = data.get("status", "unknown")  # ahead, behind, identical, diverged
            ahead_by = data.get("ahead_by", 0)
            behind_by = data.get("behind_by", 0)
            total_commits = data.get("total_commits", 0)

            sections = [
                f"## Branch Comparison: `{base}` ← `{head}` in {owner}/{repo}\n\n"
                f"**Status:** {status}\n"
                f"**Commits:** {head} is {ahead_by} ahead, {behind_by} behind {base}\n"
                f"**Total unique commits:** {total_commits}"
            ]

            # List commits
            commits = data.get("commits", [])
            if commits:
                commit_lines = []
                for c in commits[:30]:  # Limit display
                    sha = c.get("sha", "")[:7]
                    msg = c.get("commit", {}).get("message", "").split("\n")[0]
                    author = (
                        c.get("commit", {}).get("author", {}).get("name", "Unknown")
                    )
                    commit_lines.append(f"- `{sha}` **{author}**: {msg}")
                sections.append(
                    f"**Commits on `{head}` not in `{base}` ({len(commits)}"
                    + (f", showing 30" if len(commits) > 30 else "")
                    + f"):**\n"
                    + "\n".join(commit_lines)
                )

            # List changed files
            files = data.get("files", [])
            if files:
                file_lines = []
                for f in files[:50]:  # Limit display
                    status_icon = {
                        "added": "+",
                        "removed": "-",
                        "modified": "~",
                        "renamed": ">",
                    }.get(f.get("status", "modified"), "?")
                    filename = f.get("filename", "unknown")
                    adds = f.get("additions", 0)
                    dels = f.get("deletions", 0)
                    file_lines.append(
                        f"- [{status_icon}] `{filename}` (+{adds} -{dels})"
                    )
                sections.append(
                    f"**Changed files ({len(files)}"
                    + (f", showing 50" if len(files) > 50 else "")
                    + f"):**\n"
                    + "\n".join(file_lines)
                )

            # Summary stats
            if files:
                total_adds = sum(f.get("additions", 0) for f in files)
                total_dels = sum(f.get("deletions", 0) for f in files)
                sections.append(
                    f"**Total:** {len(files)} files changed, +{total_adds} additions, -{total_dels} deletions"
                )

            return self._budget_wrap("\n\n".join(sections))

        except requests.RequestException as e:
            return f"Error comparing branches: {str(e)}"

    def get_repo_overview(self, repo_url: str, branch: Optional[str] = None) -> str:
        """
        Get a compact map of a GitHub repository: metadata, root-level file tree, and a brief README snippet.
        ALWAYS call this first for any new repository. Output is deliberately small (~2-4k chars) to leave room for follow-up calls.
        This is a navigation aid — use it to decide what to read next, then call get_repo_file or bulk_read_files.
        For full README: get_repo_file(repo, "README.md"). For subdirectory contents: get_repo_file(repo, "src/").

        :param repo_url: GitHub repository URL (e.g. https://github.com/owner/repo or https://github.com/owner/repo/tree/branch-name) or owner/repo shorthand
        :param branch: Branch name to inspect (optional). If omitted, uses the branch from the URL or the repo default branch.
        :return: Compact repository map with metadata, root tree, and README snippet
        """
        parsed = self._parse_repo(repo_url)
        if not parsed:
            return "Error: Invalid repository URL. Use https://github.com/owner/repo or owner/repo format."

        owner, repo, url_branch, url_file = parsed
        branch = branch or url_branch
        sections = []

        # 1. Repo metadata — compact single block
        try:
            r = self._get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}")
            if r.status_code == 404:
                return f"Error: Repository {owner}/{repo} not found. Check the URL and ensure it's public."
            if r.status_code == 403:
                return "Error: GitHub API rate limit exceeded. Add a GITHUB_TOKEN in the tool settings for 5,000 requests/hour."
            r.raise_for_status()
            data = r.json()
            sections.append(
                f"# {data.get('full_name', f'{owner}/{repo}')}"
                + (f" (branch: `{branch}`)" if branch else "")
                + f"\n"
                f"{data.get('description') or 'No description'} | "
                f"{data.get('language') or 'Unknown'} | "
                f"{data.get('stargazers_count', 0)} stars | "
                f"{data.get('license', {}).get('spdx_id', 'None') if data.get('license') else 'No license'}\n"
                f"Default branch: {data.get('default_branch', 'main')} | "
                f"Topics: {', '.join(data.get('topics', [])) or 'None'} | "
                f"Last pushed: {data.get('pushed_at', 'Unknown')}"
            )
            default_branch = data.get("default_branch", "main")
            effective_branch = branch or default_branch
        except requests.RequestException as e:
            return f"Error fetching repository metadata: {str(e)}"

        # 2. Languages (single line)
        try:
            r = self._get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}/languages")
            if r.status_code == 200:
                langs = r.json()
                if langs:
                    total = sum(langs.values())
                    lang_str = ", ".join(
                        f"{lang} {bytes_count * 100 // total}%"
                        for lang, bytes_count in sorted(
                            langs.items(), key=lambda x: -x[1]
                        )[:8]
                    )
                    sections.append(f"**Languages:** {lang_str}")
        except requests.RequestException:
            pass

        # 3. Root-level tree only — just the top-level map (no recursive deep dive)
        top_dirs = []
        try:
            r = self._get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{requests.utils.quote(effective_branch, safe='')}"
            )
            if r.status_code == 200:
                tree_data = r.json()
                items = tree_data.get("tree", [])
                max_items = self.valves.OVERVIEW_TREE_MAX_ITEMS

                tree_lines = []
                for item in items[:max_items]:
                    path = item["path"]
                    if item["type"] == "tree":
                        tree_lines.append(f"{path}/")
                        top_dirs.append(path)
                    else:
                        tree_lines.append(path)

                if tree_lines:
                    tree_str = "\n".join(tree_lines)
                    sections.append(
                        f"## Root Tree ({len(items)} entries)\n```\n{tree_str}\n```"
                    )
        except requests.RequestException:
            pass

        # 4. README snippet — just the opening to understand what the repo is
        try:
            r = self._get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/readme{self._ref_param(branch)}"
            )
            if r.status_code == 200:
                readme_data = r.json()
                readme_name = readme_data.get("name", "README.md")
                full_size = readme_data.get("size", 0)
                content = base64.b64decode(readme_data.get("content", "")).decode(
                    "utf-8", errors="replace"
                )
                readme_limit = self.valves.OVERVIEW_README_CHARS
                if len(content) > readme_limit:
                    cut = content[:readme_limit].rfind("\n")
                    if cut > readme_limit // 2:
                        content = content[:cut]
                    else:
                        content = content[:readme_limit]
                    content += f"\n\n... ({full_size} bytes — `get_repo_file(repo, \"{readme_name}\")` for full)"
                sections.append(f"## README (snippet)\n{content}")
        except requests.RequestException:
            pass

        # 5. Actionable next-step hints
        dir_hint = ""
        if top_dirs:
            key_dirs = [d for d in top_dirs if d.lower() in {"src", "lib", "app", "pkg", "packages", "cmd", "internal", "api", "core", "server", "client"}]
            if not key_dirs:
                key_dirs = top_dirs[:3]
            dir_hint = f" Key dirs: {', '.join(key_dirs[:4])}."

        sections.append(
            f"---\n**Next:** `bulk_read_files(preview=true)` to scan key files. "
            f"`get_repo_file(path, max_lines=50)` for previews.{dir_hint} "
            f"Store large results in FileShed."
        )

        return self._budget_wrap("\n\n".join(sections))

    def get_repo_file(
        self, repo_url: str, file_path: str, branch: Optional[str] = None, max_lines: int = 0
    ) -> str:
        """
        Read the contents of a specific file or list a directory from a GitHub repository.
        Set max_lines > 0 to get a preview (first N lines) instead of the full file — useful to check relevance before committing tokens.
        If the preview looks relevant, call again with max_lines=0 for full content, or store it in FileShed.

        :param repo_url: GitHub repository URL (e.g. https://github.com/owner/repo or https://github.com/owner/repo/blob/branch/path/to/file) or owner/repo shorthand
        :param file_path: Path to the file within the repository (e.g. src/main.py, package.json). If the URL already contains a file path (blob URL), this can be left as an empty string.
        :param branch: Branch name (optional). If omitted, uses the branch from the URL or the repo default branch.
        :param max_lines: Return only the first N lines (0 = full file). Use for previews to save context tokens.
        :return: The file contents (or preview), or a directory listing
        """
        parsed = self._parse_repo(repo_url)
        if not parsed:
            return "Error: Invalid repository URL."

        owner, repo, url_branch, url_file = parsed
        branch = branch or url_branch
        # If file_path is empty/not provided but URL had a file path (blob URL), use that
        effective_path = (
            file_path.strip().lstrip("/")
            if file_path and file_path.strip()
            else (url_file or "")
        )
        if not effective_path:
            return "Error: No file path provided. Pass a file path or use a blob URL like https://github.com/owner/repo/blob/branch/path/to/file."
        clean_path = effective_path

        try:
            r = self._get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{clean_path}{self._ref_param(branch)}"
            )
            if r.status_code == 404:
                hint = f" on branch '{branch}'" if branch else ""
                return f"Error: File '{clean_path}' not found in {owner}/{repo}{hint}."
            if r.status_code == 403:
                return "Error: GitHub API rate limit exceeded. Add a GITHUB_TOKEN in tool settings."
            r.raise_for_status()
            data = r.json()

            if isinstance(data, list):
                # It's a directory, list contents
                entries = []
                for item in data:
                    prefix = "📁" if item["type"] == "dir" else "📄"
                    size = (
                        f" ({item.get('size', 0)} bytes)"
                        if item["type"] == "file"
                        else ""
                    )
                    entries.append(f"{prefix} {item['name']}{size}")
                return self._budget_wrap(f"**Directory: {clean_path}**\n\n" + "\n".join(entries))

            if data.get("encoding") == "base64":
                content = base64.b64decode(data.get("content", "")).decode(
                    "utf-8", errors="replace"
                )
                total_lines = content.count("\n") + 1
                file_size = data.get("size", 0)

                # Apply max_lines preview if requested
                if max_lines and max_lines > 0:
                    lines = content.split("\n")
                    if len(lines) > max_lines:
                        content = "\n".join(lines[:max_lines])
                        content += f"\n\n... (preview: {max_lines}/{total_lines} lines, {file_size} bytes total — call with max_lines=0 for full)"

                max_len = self.valves.MAX_FILE_SIZE
                if len(content) > max_len:
                    content = (
                        content[:max_len] + f"\n\n... (truncated at {max_len} chars)"
                    )
                result = f"**File: {clean_path}** ({file_size} bytes, {total_lines} lines)\n\n```\n{content}\n```"

                # Hint: nudge shedding for large files during investigation
                if len(result) > 8000:
                    result += (
                        "\n\n---\n*Large file (~"
                        + str(len(result) // 1000)
                        + "k chars). Analyze it now, then shed your findings with `shed_create_file` before reading more files.*"
                    )

                return self._budget_wrap(result)
            else:
                return f"Error: File encoding '{data.get('encoding')}' not supported. File may be binary."

        except requests.RequestException as e:
            return f"Error reading file: {str(e)}"

    def search_repo_code(self, repo_url: str, query: str) -> str:
        """
        Search for code within a GitHub repository. Use this to find specific functions, classes, imports, configuration, or patterns.
        After getting results, immediately call bulk_read_files on the top matching files to get full context — do not stop at search results alone.
        Note: GitHub code search always searches the default branch. To search other branches, use get_repo_overview to get the file tree, then bulk_read_files to check specific files.

        :param repo_url: GitHub repository URL (e.g. https://github.com/owner/repo) or owner/repo shorthand
        :param query: Search query — can include keywords, function names, class names, or code patterns
        :return: Matching files and code snippets
        """
        parsed = self._parse_repo(repo_url)
        if not parsed:
            return "Error: Invalid repository URL."

        owner, repo, _, _ = parsed

        try:
            r = self._get(
                f"{GITHUB_API_BASE}/search/code?q={requests.utils.quote(query)}+repo:{owner}/{repo}&per_page=10"
            )
            if r.status_code == 403:
                return "Error: GitHub API rate limit exceeded. Add a GITHUB_TOKEN in tool settings."
            if r.status_code == 422:
                return "Error: Search query too broad. Try more specific terms."
            r.raise_for_status()
            data = r.json()

            items = data.get("items", [])
            total = data.get("total_count", 0)

            if not items:
                return f"No results found for '{query}' in {owner}/{repo}."

            results = [
                f"**Found {total} result(s) for** `{query}` **in** {owner}/{repo}\n"
            ]
            for item in items:
                path = item.get("path", "unknown")
                name = item.get("name", "unknown")
                # Get a text match snippet if available
                matches = item.get("text_matches", [])
                snippet = ""
                if matches:
                    for m in matches[:2]:
                        fragment = m.get("fragment", "")
                        if fragment:
                            snippet += f"\n```\n{fragment}\n```\n"

                results.append(
                    f"### {path}\n{snippet if snippet else f'*File: {name}*'}"
                )

            return self._budget_wrap("\n\n".join(results))

        except requests.RequestException as e:
            return f"Error searching repository: {str(e)}"

    def get_repo_commits(
        self,
        repo_url: str,
        path: Optional[str] = None,
        max_commits: int = 20,
        branch: Optional[str] = None,
    ) -> str:
        """
        Get recent commit history for a GitHub repository or a specific file/directory.
        Use this to understand recent changes, who contributed, and what was modified.

        :param repo_url: GitHub repository URL (e.g. https://github.com/owner/repo or https://github.com/owner/repo/tree/branch-name) or owner/repo shorthand
        :param path: Optional file or directory path to filter commits (e.g. src/main.py). Leave empty for all commits.
        :param max_commits: Maximum number of commits to return (default 20, max 100)
        :param branch: Branch name (optional). If omitted, uses the branch from the URL or the repo default branch.
        :return: List of recent commits with authors, dates, and messages
        """
        parsed = self._parse_repo(repo_url)
        if not parsed:
            return "Error: Invalid repository URL."

        owner, repo, url_branch, _ = parsed
        branch = branch or url_branch
        max_commits = min(max(1, max_commits), 100)

        params = f"per_page={max_commits}"
        if branch:
            params += f"&sha={requests.utils.quote(branch, safe='')}"
        if path:
            params += f"&path={requests.utils.quote(path.strip().lstrip('/'))}"

        try:
            r = self._get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits?{params}")
            if r.status_code == 404:
                return f"Error: Repository or path not found in {owner}/{repo}."
            if r.status_code == 403:
                return "Error: GitHub API rate limit exceeded. Add a GITHUB_TOKEN in tool settings."
            r.raise_for_status()
            commits = r.json()

            if not commits:
                return (
                    f"No commits found for {owner}/{repo}"
                    + (f" on branch '{branch}'" if branch else "")
                    + (f" at path '{path}'" if path else "")
                    + "."
                )

            header = (
                f"**Recent commits for {owner}/{repo}**"
                + (f" (branch: `{branch}`)" if branch else "")
                + (f" (path: `{path}`)" if path else "")
                + f"\n\n"
            )
            lines = []
            for c in commits:
                sha = c.get("sha", "")[:7]
                commit_data = c.get("commit", {})
                message = commit_data.get("message", "").split("\n")[
                    0
                ]  # First line only
                author_data = commit_data.get("author", {})
                author = author_data.get("name", "Unknown")
                date = author_data.get("date", "Unknown")
                if date and len(date) >= 10:
                    date = date[:10]  # Just the date portion
                lines.append(f"- `{sha}` {date} **{author}**: {message}")

            return self._budget_wrap(header + "\n".join(lines))

        except requests.RequestException as e:
            return f"Error fetching commits: {str(e)}"

    def get_commit_detail(self, repo_url: str, commit_sha: str) -> str:
        """
        Get detailed information about a specific commit including the full message, stats, and list of changed files.
        Use this after get_repo_commits to drill into a particular change.

        :param repo_url: GitHub repository URL (e.g. https://github.com/owner/repo) or owner/repo shorthand
        :param commit_sha: The commit SHA (full or abbreviated, e.g. abc1234)
        :return: Commit details with changed files and diff stats
        """
        parsed = self._parse_repo(repo_url)
        if not parsed:
            return "Error: Invalid repository URL."

        owner, repo, _, _ = parsed

        try:
            r = self._get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{commit_sha.strip()}"
            )
            if r.status_code == 404:
                return f"Error: Commit '{commit_sha}' not found in {owner}/{repo}."
            if r.status_code == 403:
                return "Error: GitHub API rate limit exceeded. Add a GITHUB_TOKEN in tool settings."
            r.raise_for_status()
            data = r.json()

            commit_info = data.get("commit", {})
            sha = data.get("sha", "")[:7]
            message = commit_info.get("message", "No message")
            author = commit_info.get("author", {})
            author_name = author.get("name", "Unknown")
            author_date = author.get("date", "Unknown")

            stats = data.get("stats", {})
            additions = stats.get("additions", 0)
            deletions = stats.get("deletions", 0)
            total = stats.get("total", 0)

            sections = [
                f"## Commit `{sha}`\n\n"
                f"**Author:** {author_name}\n"
                f"**Date:** {author_date}\n"
                f"**Stats:** +{additions} -{deletions} ({total} total changes)\n\n"
                f"**Message:**\n{message}"
            ]

            files = data.get("files", [])
            if files:
                file_lines = []
                for f in files[:50]:  # Limit to 50 files
                    status = f.get("status", "modified")
                    filename = f.get("filename", "unknown")
                    adds = f.get("additions", 0)
                    dels = f.get("deletions", 0)
                    icon = {
                        "added": "+",
                        "removed": "-",
                        "modified": "~",
                        "renamed": ">",
                    }.get(status, "?")
                    file_lines.append(f"- [{icon}] `{filename}` (+{adds} -{dels})")
                sections.append(
                    f"**Changed files ({len(files)}):**\n" + "\n".join(file_lines)
                )

            return self._budget_wrap("\n\n".join(sections))

        except requests.RequestException as e:
            return f"Error fetching commit detail: {str(e)}"

    def bulk_read_files(
        self, repo_url: str, file_paths: str, branch: Optional[str] = None, preview: bool = False
    ) -> str:
        """
        Read multiple files from a GitHub repository in a single tool call.
        ALWAYS prefer this over get_repo_file when reading 2 or more files.
        Set preview=true to get only the first ~50 lines per file — scan for relevance before reading in full.
        After previewing, call get_repo_file on the files that matter, or store results in FileShed.

        :param repo_url: GitHub repository URL (e.g. https://github.com/owner/repo or https://github.com/owner/repo/tree/branch-name) or owner/repo shorthand
        :param file_paths: Comma-separated list of file paths to read (e.g. "src/main.py, README.md, src/config.ts")
        :param branch: Branch name (optional). If omitted, uses the branch from the URL or the repo default branch.
        :param preview: If true, return only the first ~50 lines per file instead of full contents. Much more context-efficient for scanning.
        :return: Contents (or previews) of all requested files with clear separators
        """
        parsed = self._parse_repo(repo_url)
        if not parsed:
            return "Error: Invalid repository URL."

        owner, repo, url_branch, _ = parsed
        branch = branch or url_branch
        ref_param = self._ref_param(branch)
        paths = [p.strip().lstrip("/") for p in file_paths.split(",") if p.strip()]

        if not paths:
            return "Error: No file paths provided. Pass comma-separated paths."

        if len(paths) > 20:
            return f"Error: Too many files requested ({len(paths)}). Maximum is 20 per call."

        results = []
        errors = []
        total_chars = 0
        max_total = self.valves.MAX_FILE_SIZE * 3  # Allow 3x single file limit for bulk

        for file_path in paths:
            if total_chars >= max_total:
                results.append(
                    f"\n--- TRUNCATED: Remaining {len(paths) - len(results) - len(errors)} files skipped (output size limit) ---"
                )
                break

            try:
                r = self._get(
                    f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{file_path}{ref_param}"
                )
                if r.status_code == 404:
                    errors.append(f"  - `{file_path}`: Not found")
                    continue
                if r.status_code == 403:
                    errors.append(f"  - `{file_path}`: Rate limited")
                    continue
                r.raise_for_status()
                data = r.json()

                if isinstance(data, list):
                    entries = [
                        f"{'📁' if item['type'] == 'dir' else '📄'} {item['name']}"
                        for item in data[:30]
                    ]
                    content_str = f"**Directory: {file_path}**\n" + "\n".join(entries)
                elif data.get("encoding") == "base64":
                    content = base64.b64decode(data.get("content", "")).decode(
                        "utf-8", errors="replace"
                    )
                    total_lines = content.count("\n") + 1
                    file_size = data.get("size", 0)

                    # Preview mode: first N lines only
                    if preview:
                        preview_n = self.valves.PREVIEW_LINES
                        lines = content.split("\n")
                        if len(lines) > preview_n:
                            content = "\n".join(lines[:preview_n])
                            content += f"\n... (preview: {preview_n}/{total_lines} lines — use get_repo_file for full)"

                    remaining = max_total - total_chars
                    per_file_limit = min(self.valves.MAX_FILE_SIZE, remaining)
                    if len(content) > per_file_limit:
                        content = (
                            content[:per_file_limit]
                            + f"\n... (truncated at {per_file_limit} chars)"
                        )
                    content_str = f"**File: {file_path}** ({file_size} bytes, {total_lines} lines)\n\n```\n{content}\n```"
                else:
                    content_str = f"**File: {file_path}** — binary file, cannot display"

                results.append(content_str)
                total_chars += len(content_str)

            except requests.RequestException as e:
                errors.append(f"  - `{file_path}`: {str(e)}")

        output_parts = []
        if results:
            mode_label = " (preview)" if preview else ""
            output_parts.append(
                f"**Read {len(results)} file(s){mode_label} from {owner}/{repo}**\n"
            )
            output_parts.extend(results)
        if errors:
            output_parts.append(f"\n**Errors ({len(errors)}):**\n" + "\n".join(errors))
        if not results and not errors:
            output_parts.append("No files could be read.")

        result = "\n\n---\n\n".join(output_parts)

        # Hint: if output is large, nudge model to use FileShed
        if total_chars > 15000:
            result += (
                "\n\n---\n*Large output (~"
                + str(total_chars // 1000)
                + "k chars). Write your analysis notes to FileShed with `shed_create_file` (key findings, patterns, what to read next) — then continue exploring with free context.*"
            )

        return self._budget_wrap(result)

    def validate_features(
        self,
        repo_url: str,
        features: List[str],
        max_files_per_feature: int = 3,
        branch: Optional[str] = None,
    ) -> str:
        """
        Batch-validate a list of features/requirements against a GitHub repository.
        For each feature, searches the codebase and reads relevant files to gather evidence of implementation.
        Returns a structured report with evidence found (or not) for every feature — in a single tool call.

        Use this when a user provides a specification or feature list and asks you to verify implementation.
        The results can be large — store them in FileShed (shed_create_file) if you need to free context, then retrieve later.

        :param repo_url: GitHub repository URL (e.g. https://github.com/owner/repo or https://github.com/owner/repo/tree/branch-name) or owner/repo shorthand
        :param features: List of feature descriptions to validate (e.g. ["JWT authentication", "rate limiting", "WebSocket support"])
        :param max_files_per_feature: Maximum files to read per feature (default 3, max 5) to control output size
        :param branch: Branch name (optional). If omitted, uses the branch from the URL or the repo default branch.
        :return: Structured validation report with evidence per feature
        """
        parsed = self._parse_repo(repo_url)
        if not parsed:
            return "Error: Invalid repository URL."

        owner, repo, url_branch, _ = parsed
        branch = branch or url_branch
        ref_param = self._ref_param(branch)
        max_files_per_feature = min(max(1, max_files_per_feature), 5)

        # Step 1: Get the file tree for cross-referencing
        tree_ref = requests.utils.quote(branch, safe="") if branch else "HEAD"
        tree_paths = []
        try:
            r = self._get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{tree_ref}?recursive=1"
            )
            if r.status_code == 200:
                tree_paths = [
                    item["path"]
                    for item in r.json().get("tree", [])
                    if item["type"] == "blob"
                ]
        except requests.RequestException:
            pass

        report_sections = [
            f"# Feature Validation Report\n**Repository:** {owner}/{repo}"
            + (f"\n**Branch:** {branch}" if branch else "")
            + f"\n**Features checked:** {len(features)}\n"
        ]

        for i, feature in enumerate(features, 1):
            section = f"## {i}. {feature}\n\n"
            evidence_files = []
            search_error = None

            # Generate search keywords from the feature description
            keywords = self._extract_search_keywords(feature)

            # Search for each keyword
            found_paths = set()
            for keyword in keywords[:3]:  # Limit to 3 keyword searches per feature
                try:
                    r = self._get(
                        f"{GITHUB_API_BASE}/search/code?q={requests.utils.quote(keyword)}+repo:{owner}/{repo}&per_page=5"
                    )
                    if r.status_code == 200:
                        for item in r.json().get("items", []):
                            path = item.get("path", "")
                            if (
                                path
                                and path not in found_paths
                                and len(found_paths) < max_files_per_feature * 2
                            ):
                                found_paths.add(path)
                    elif r.status_code == 403:
                        search_error = "Rate limited"
                        break
                except requests.RequestException:
                    pass

            # Also check file tree for name-based matches
            feature_lower = feature.lower()
            for path in tree_paths:
                name_lower = path.lower()
                for kw in keywords[:3]:
                    if kw.lower() in name_lower and path not in found_paths:
                        found_paths.add(path)

            if search_error:
                section += f"**Status:** SEARCH ERROR — {search_error}\n"
                report_sections.append(section)
                continue

            if not found_paths:
                section += "**Status:** NOT FOUND\n**Evidence:** No matching code found in the repository.\n"
                report_sections.append(section)
                continue

            # Read the most relevant files (limited)
            files_to_read = sorted(found_paths)[:max_files_per_feature]
            section += f"**Status:** EVIDENCE FOUND ({len(found_paths)} file(s) match, showing {len(files_to_read)})\n\n"

            for file_path in files_to_read:
                try:
                    r = self._get(
                        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{file_path}{ref_param}"
                    )
                    if r.status_code == 200:
                        data = r.json()
                        if data.get("encoding") == "base64":
                            content = base64.b64decode(data.get("content", "")).decode(
                                "utf-8", errors="replace"
                            )
                            # Extract relevant snippet (lines containing keywords)
                            snippet = self._extract_relevant_snippet(
                                content, keywords, max_lines=30
                            )
                            evidence_files.append(
                                f"### `{file_path}`\n```\n{snippet}\n```"
                            )
                        else:
                            evidence_files.append(
                                f"### `{file_path}`\n*Binary file — cannot display*"
                            )
                except requests.RequestException:
                    evidence_files.append(f"### `{file_path}`\n*Failed to read file*")

            section += (
                "\n\n".join(evidence_files)
                if evidence_files
                else "*Files matched but could not be read.*"
            )
            report_sections.append(section)

        # Summary
        summary_lines = [
            "\n---\n## Summary\n\n| # | Feature | Status |",
            "|----|---------|--------|",
        ]
        for i, feature in enumerate(features, 1):
            section_text = report_sections[i] if i < len(report_sections) else ""
            if "NOT FOUND" in section_text:
                status = "Not Found"
            elif "SEARCH ERROR" in section_text:
                status = "Search Error"
            elif "EVIDENCE FOUND" in section_text:
                status = "Evidence Found"
            else:
                status = "Unknown"
            summary_lines.append(f"| {i} | {feature} | {status} |")

        report_sections.append("\n".join(summary_lines))

        result = "\n\n".join(report_sections)

        # Hint: validation reports are large — nudge FileShed usage
        if len(result) > 10000:
            result += (
                "\n\n---\n*This report is ~"
                + str(len(result) // 1000)
                + "k chars. Store your analysis notes with `shed_create_file` (status per feature, key evidence, gaps found) to free context for follow-up investigation.*"
            )

        return self._budget_wrap(result)

    def _extract_search_keywords(self, feature: str) -> List[str]:
        """Extract meaningful search keywords from a feature description."""
        # Remove common filler words
        stop_words = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "shall",
            "can",
            "need",
            "must",
            "ought",
            "for",
            "and",
            "but",
            "or",
            "nor",
            "not",
            "so",
            "yet",
            "both",
            "either",
            "neither",
            "each",
            "every",
            "all",
            "any",
            "few",
            "more",
            "most",
            "other",
            "some",
            "such",
            "no",
            "only",
            "own",
            "same",
            "than",
            "too",
            "very",
            "just",
            "because",
            "as",
            "until",
            "while",
            "of",
            "at",
            "by",
            "about",
            "between",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "to",
            "from",
            "up",
            "down",
            "in",
            "out",
            "on",
            "off",
            "over",
            "under",
            "with",
            "without",
            "into",
            "onto",
            "upon",
            "that",
            "this",
            "these",
            "those",
            "it",
            "its",
            "they",
            "them",
            "their",
            "we",
            "us",
            "our",
            "you",
            "your",
            "he",
            "him",
            "his",
            "she",
            "her",
            "support",
            "implementation",
            "implement",
            "feature",
            "functionality",
            "system",
            "using",
            "based",
        }
        words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", feature)
        keywords = [w for w in words if w.lower() not in stop_words and len(w) > 2]

        # Also try the full phrase (hyphenated/compound terms)
        compound = feature.strip()
        if len(compound.split()) <= 4 and len(compound) <= 50:
            keywords.insert(0, compound)

        return keywords[:5] if keywords else [feature.strip()[:50]]

    def _extract_relevant_snippet(
        self, content: str, keywords: List[str], max_lines: int = 30
    ) -> str:
        """Extract lines from content that are most relevant to the keywords."""
        lines = content.split("\n")
        if len(lines) <= max_lines:
            return content[: self.valves.MAX_FILE_SIZE]

        # Score each line by keyword matches
        scored = []
        keywords_lower = [k.lower() for k in keywords]
        for idx, line in enumerate(lines):
            line_lower = line.lower()
            score = sum(1 for kw in keywords_lower if kw in line_lower)
            if score > 0:
                scored.append((idx, score))

        if not scored:
            # No keyword matches — return start of file
            return (
                "\n".join(lines[:max_lines])
                + f"\n... ({len(lines) - max_lines} more lines)"
            )

        # Collect lines around the best matches with context
        scored.sort(key=lambda x: -x[1])
        selected_indices = set()
        for idx, _ in scored[: max_lines // 3]:
            for ctx in range(max(0, idx - 2), min(len(lines), idx + 3)):
                selected_indices.add(ctx)
            if len(selected_indices) >= max_lines:
                break

        sorted_indices = sorted(selected_indices)[:max_lines]
        result_lines = []
        prev = -2
        for idx in sorted_indices:
            if idx > prev + 1:
                result_lines.append(f"... (line {idx + 1})")
            result_lines.append(lines[idx])
            prev = idx

        remaining = len(lines) - len(sorted_indices)
        if remaining > 0:
            result_lines.append(f"... ({remaining} more lines)")

        return "\n".join(result_lines)
