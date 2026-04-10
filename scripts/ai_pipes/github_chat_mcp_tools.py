"""
title: GitHub Repo Analyzer
author: ai-stack
version: 2.0.0
description: Give any model the ability to explore GitHub repositories using the free GitHub REST API. Fetch repo overviews, read files, and search code — then the model summarizes and analyzes.
required_open_webui_version: 0.4.0
requirements: requests
"""

from pydantic import BaseModel, Field
from typing import Optional
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

    def __init__(self):
        self.valves = self.Valves()

    def _build_headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.valves.GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {self.valves.GITHUB_TOKEN}"
        return headers

    def _parse_repo(self, repo_url: str) -> Optional[tuple]:
        """Extract owner/repo from a GitHub URL."""
        match = re.match(
            r"https?://github\.com/([\w\-\.]+)/([\w\-\.]+)", repo_url.strip()
        )
        if match:
            return match.group(1), match.group(2)
        # Also accept owner/repo shorthand
        match = re.match(r"^([\w\-\.]+)/([\w\-\.]+)$", repo_url.strip())
        if match:
            return match.group(1), match.group(2)
        return None

    def _get(self, url: str) -> requests.Response:
        return requests.get(
            url,
            headers=self._build_headers(),
            timeout=self.valves.REQUEST_TIMEOUT,
        )

    def get_repo_overview(self, repo_url: str) -> str:
        """
        Get a comprehensive overview of a GitHub repository including its description, stats, languages, directory structure, and README content.
        Call this first when a user asks about a GitHub repository.

        :param repo_url: GitHub repository URL (e.g. https://github.com/owner/repo) or owner/repo shorthand
        :return: Repository overview with metadata, file tree, and README
        """
        parsed = self._parse_repo(repo_url)
        if not parsed:
            return "Error: Invalid repository URL. Use https://github.com/owner/repo or owner/repo format."

        owner, repo = parsed
        sections = []

        # 1. Repo metadata
        try:
            r = self._get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}")
            if r.status_code == 404:
                return f"Error: Repository {owner}/{repo} not found. Check the URL and ensure it's public."
            if r.status_code == 403:
                return "Error: GitHub API rate limit exceeded. Add a GITHUB_TOKEN in the tool settings for 5,000 requests/hour."
            r.raise_for_status()
            data = r.json()
            sections.append(
                f"# {data.get('full_name', f'{owner}/{repo}')}\n\n"
                f"**Description:** {data.get('description') or 'No description'}\n"
                f"**Language:** {data.get('language') or 'Unknown'}\n"
                f"**Stars:** {data.get('stargazers_count', 0)} | **Forks:** {data.get('forks_count', 0)} | **Issues:** {data.get('open_issues_count', 0)}\n"
                f"**Default branch:** {data.get('default_branch', 'main')}\n"
                f"**License:** {data.get('license', {}).get('spdx_id', 'None') if data.get('license') else 'None'}\n"
                f"**Topics:** {', '.join(data.get('topics', [])) or 'None'}\n"
                f"**Created:** {data.get('created_at', 'Unknown')}\n"
                f"**Last pushed:** {data.get('pushed_at', 'Unknown')}"
            )
            default_branch = data.get("default_branch", "main")
        except requests.RequestException as e:
            return f"Error fetching repository metadata: {str(e)}"

        # 2. Languages
        try:
            r = self._get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}/languages")
            if r.status_code == 200:
                langs = r.json()
                if langs:
                    total = sum(langs.values())
                    lang_str = ", ".join(
                        f"{lang} ({bytes_count * 100 // total}%)"
                        for lang, bytes_count in sorted(langs.items(), key=lambda x: -x[1])[:10]
                    )
                    sections.append(f"**Languages:** {lang_str}")
        except requests.RequestException:
            pass

        # 3. File tree (root level + one level deep for key dirs)
        try:
            r = self._get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1"
            )
            if r.status_code == 200:
                tree_data = r.json()
                items = tree_data.get("tree", [])
                truncated = tree_data.get("truncated", False)

                # Limit to reasonable size
                tree_lines = []
                count = 0
                for item in items:
                    path = item["path"]
                    depth = path.count("/")
                    if depth <= 2 and count < 200:
                        prefix = "📁 " if item["type"] == "tree" else "📄 "
                        indent = "  " * depth
                        tree_lines.append(f"{indent}{prefix}{path.split('/')[-1]}")
                        count += 1

                if tree_lines:
                    tree_str = "\n".join(tree_lines)
                    note = " (truncated)" if truncated or count >= 200 else ""
                    sections.append(f"## Directory Structure{note}\n\n```\n{tree_str}\n```")
        except requests.RequestException:
            pass

        # 4. README
        try:
            r = self._get(f"{GITHUB_API_BASE}/repos/{owner}/{repo}/readme")
            if r.status_code == 200:
                readme_data = r.json()
                content = base64.b64decode(readme_data.get("content", "")).decode(
                    "utf-8", errors="replace"
                )
                max_len = self.valves.MAX_FILE_SIZE
                if len(content) > max_len:
                    content = content[:max_len] + f"\n\n... (README truncated at {max_len} chars)"
                sections.append(f"## README\n\n{content}")
        except requests.RequestException:
            pass

        return "\n\n---\n\n".join(sections)

    def get_repo_file(self, repo_url: str, file_path: str) -> str:
        """
        Read the contents of a specific file from a GitHub repository.
        Use this when you need to examine a particular source file in detail.

        :param repo_url: GitHub repository URL (e.g. https://github.com/owner/repo) or owner/repo shorthand
        :param file_path: Path to the file within the repository (e.g. src/main.py, package.json)
        :return: The file contents
        """
        parsed = self._parse_repo(repo_url)
        if not parsed:
            return "Error: Invalid repository URL."

        owner, repo = parsed
        clean_path = file_path.strip().lstrip("/")

        try:
            r = self._get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{clean_path}"
            )
            if r.status_code == 404:
                return f"Error: File '{clean_path}' not found in {owner}/{repo}."
            if r.status_code == 403:
                return "Error: GitHub API rate limit exceeded. Add a GITHUB_TOKEN in tool settings."
            r.raise_for_status()
            data = r.json()

            if isinstance(data, list):
                # It's a directory, list contents
                entries = []
                for item in data:
                    prefix = "📁" if item["type"] == "dir" else "📄"
                    size = f" ({item.get('size', 0)} bytes)" if item["type"] == "file" else ""
                    entries.append(f"{prefix} {item['name']}{size}")
                return f"**Directory: {clean_path}**\n\n" + "\n".join(entries)

            if data.get("encoding") == "base64":
                content = base64.b64decode(data.get("content", "")).decode(
                    "utf-8", errors="replace"
                )
                max_len = self.valves.MAX_FILE_SIZE
                if len(content) > max_len:
                    content = content[:max_len] + f"\n\n... (truncated at {max_len} chars)"
                return f"**File: {clean_path}** ({data.get('size', 0)} bytes)\n\n```\n{content}\n```"
            else:
                return f"Error: File encoding '{data.get('encoding')}' not supported. File may be binary."

        except requests.RequestException as e:
            return f"Error reading file: {str(e)}"

    def search_repo_code(self, repo_url: str, query: str) -> str:
        """
        Search for code within a GitHub repository. Use this to find specific functions, classes, imports, configuration, or patterns.

        :param repo_url: GitHub repository URL (e.g. https://github.com/owner/repo) or owner/repo shorthand
        :param query: Search query — can include keywords, function names, class names, or code patterns
        :return: Matching files and code snippets
        """
        parsed = self._parse_repo(repo_url)
        if not parsed:
            return "Error: Invalid repository URL."

        owner, repo = parsed

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

            results = [f"**Found {total} result(s) for** `{query}` **in** {owner}/{repo}\n"]
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

                results.append(f"### {path}\n{snippet if snippet else f'*File: {name}*'}")

            return "\n\n".join(results)

        except requests.RequestException as e:
            return f"Error searching repository: {str(e)}"

    def get_repo_commits(self, repo_url: str, path: Optional[str] = None, max_commits: int = 20) -> str:
        """
        Get recent commit history for a GitHub repository or a specific file/directory.
        Use this to understand recent changes, who contributed, and what was modified.

        :param repo_url: GitHub repository URL (e.g. https://github.com/owner/repo) or owner/repo shorthand
        :param path: Optional file or directory path to filter commits (e.g. src/main.py). Leave empty for all commits.
        :param max_commits: Maximum number of commits to return (default 20, max 100)
        :return: List of recent commits with authors, dates, and messages
        """
        parsed = self._parse_repo(repo_url)
        if not parsed:
            return "Error: Invalid repository URL."

        owner, repo = parsed
        max_commits = min(max(1, max_commits), 100)

        params = f"per_page={max_commits}"
        if path:
            params += f"&path={requests.utils.quote(path.strip().lstrip('/'))}"

        try:
            r = self._get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits?{params}"
            )
            if r.status_code == 404:
                return f"Error: Repository or path not found in {owner}/{repo}."
            if r.status_code == 403:
                return "Error: GitHub API rate limit exceeded. Add a GITHUB_TOKEN in tool settings."
            r.raise_for_status()
            commits = r.json()

            if not commits:
                return f"No commits found for {owner}/{repo}" + (f" at path '{path}'" if path else "") + "."

            header = f"**Recent commits for {owner}/{repo}**" + (f" (path: `{path}`)" if path else "") + f"\n\n"
            lines = []
            for c in commits:
                sha = c.get("sha", "")[:7]
                commit_data = c.get("commit", {})
                message = commit_data.get("message", "").split("\n")[0]  # First line only
                author_data = commit_data.get("author", {})
                author = author_data.get("name", "Unknown")
                date = author_data.get("date", "Unknown")
                if date and len(date) >= 10:
                    date = date[:10]  # Just the date portion
                lines.append(f"- `{sha}` {date} **{author}**: {message}")

            return header + "\n".join(lines)

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

        owner, repo = parsed

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
                    icon = {"added": "+", "removed": "-", "modified": "~", "renamed": ">"}.get(status, "?")
                    file_lines.append(f"- [{icon}] `{filename}` (+{adds} -{dels})")
                sections.append(f"**Changed files ({len(files)}):**\n" + "\n".join(file_lines))

            return "\n\n".join(sections)

        except requests.RequestException as e:
            return f"Error fetching commit detail: {str(e)}"
