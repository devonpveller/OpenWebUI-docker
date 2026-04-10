"""
title: GitHub Chat MCP
author: ai-stack
version: 1.0.0
description: Analyze and query GitHub repositories using the GitHub Chat API. Index any public repo and ask questions about its codebase, architecture, and implementation.
required_open_webui_version: 0.4.0
requirements: requests
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import requests
import json


GITHUB_CHAT_API_BASE = "https://api.github-chat.com"


class Pipe:
    class Valves(BaseModel):
        GITHUB_CHAT_API_KEY: str = Field(
            default="",
            description="Optional GitHub Chat API key. Leave empty for freemium access.",
        )
        REQUEST_TIMEOUT: int = Field(
            default=120,
            description="Timeout in seconds for API requests (indexing/querying can take time on large repos).",
        )

    def __init__(self):
        self.valves = self.Valves()
        self._indexed_repos: set = set()

    def pipes(self) -> list:
        return [
            {"id": "github-chat-index", "name": "GitHub Chat: Index Repository"},
            {"id": "github-chat-query", "name": "GitHub Chat: Query Repository"},
            {"id": "github-chat-auto", "name": "GitHub Chat: Auto (Index + Query)"},
        ]

    async def pipe(self, body: dict, __user__: dict = None) -> str:
        model_id = body.get("model", "")
        messages = body.get("messages", [])
        user_message = self._get_last_user_message(messages)

        if not user_message:
            return "Please provide a message with a GitHub repository URL or a question."

        if "github-chat-index" in model_id:
            return self._handle_index(user_message)
        elif "github-chat-query" in model_id:
            return self._handle_query(user_message, messages)
        elif "github-chat-auto" in model_id:
            return self._handle_auto(user_message, messages)
        else:
            return self._handle_auto(user_message, messages)

    def _get_last_user_message(self, messages: list) -> str:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            return part.get("text", "")
                return str(content)
        return ""

    def _extract_repo_url(self, text: str) -> Optional[str]:
        import re
        match = re.search(r"https://github\.com/[\w\-\.]+/[\w\-\.]+", text)
        if match:
            url = match.group(0).rstrip("/.")
            # Strip trailing paths like /tree/main, /blob/..., etc.
            parts = url.replace("https://github.com/", "").split("/")
            if len(parts) >= 2:
                return f"https://github.com/{parts[0]}/{parts[1]}"
        return None

    def _build_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.valves.GITHUB_CHAT_API_KEY:
            headers["Authorization"] = f"Bearer {self.valves.GITHUB_CHAT_API_KEY}"
        return headers

    def _index_repository(self, repo_url: str) -> dict:
        try:
            response = requests.post(
                f"{GITHUB_CHAT_API_BASE}/verify",
                headers=self._build_headers(),
                json={"repo_url": repo_url},
                timeout=self.valves.REQUEST_TIMEOUT,
            )
            if response.status_code == 200:
                self._indexed_repos.add(repo_url)
                return {"success": True, "repo_url": repo_url}
            else:
                return {
                    "success": False,
                    "error": f"API returned status {response.status_code}: {response.text[:500]}",
                }
        except requests.Timeout:
            return {"success": False, "error": "Request timed out. The repository may be very large — try again."}
        except requests.RequestException as e:
            return {"success": False, "error": f"Network error: {str(e)}"}

    def _query_repository(
        self, repo_url: str, question: str, conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> dict:
        messages = conversation_history or []
        messages.append({"role": "user", "content": question})

        try:
            response = requests.post(
                f"{GITHUB_CHAT_API_BASE}/chat/completions/sync",
                headers=self._build_headers(),
                json={"repo_url": repo_url, "messages": messages},
                timeout=self.valves.REQUEST_TIMEOUT,
            )
            if response.status_code == 200:
                result = response.json()
                return {"success": True, "data": result}
            else:
                return {
                    "success": False,
                    "error": f"API returned status {response.status_code}: {response.text[:500]}",
                }
        except requests.Timeout:
            return {"success": False, "error": "Query timed out. Try a more specific question."}
        except requests.RequestException as e:
            return {"success": False, "error": f"Network error: {str(e)}"}

    def _format_query_response(self, data: dict) -> str:
        formatted = ""
        if "answer" in data:
            formatted += data["answer"] + "\n\n"
        if "contexts" in data and data["contexts"]:
            formatted += "**Sources:**\n"
            for i, ctx in enumerate(data["contexts"], 1):
                meta = ctx.get("meta_data", {})
                file_path = meta.get("file_path", "unknown")
                formatted += f"{i}. `{file_path}`\n"
        return formatted.strip() if formatted.strip() else "No answer returned from the API."

    def _build_conversation_history(self, messages: list) -> List[Dict[str, str]]:
        history = []
        for msg in messages[:-1]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                if isinstance(content, list):
                    text_parts = [
                        p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    content = " ".join(text_parts)
                history.append({"role": role, "content": str(content)})
        return history

    def _handle_index(self, user_message: str) -> str:
        repo_url = self._extract_repo_url(user_message)
        if not repo_url:
            return (
                "**No GitHub repository URL found.**\n\n"
                "Please provide a URL in the format: `https://github.com/owner/repo`\n\n"
                "Example: *Index https://github.com/open-webui/open-webui*"
            )
        result = self._index_repository(repo_url)
        if result["success"]:
            return f"**Repository indexed successfully.**\n\n`{repo_url}`\n\nYou can now switch to **GitHub Chat: Query Repository** or **GitHub Chat: Auto** to ask questions about this repository."
        else:
            return f"**Failed to index repository.**\n\n`{repo_url}`\n\nError: {result['error']}"

    def _handle_query(self, user_message: str, messages: list) -> str:
        repo_url = self._extract_repo_url(user_message)
        if not repo_url:
            # Search conversation history for a repo URL
            for msg in reversed(messages):
                content = msg.get("content", "")
                if isinstance(content, str):
                    repo_url = self._extract_repo_url(content)
                    if repo_url:
                        break

        if not repo_url:
            return (
                "**No repository URL found in the conversation.**\n\n"
                "Please include the GitHub URL in your message, e.g.:\n"
                "*What framework does https://github.com/owner/repo use?*"
            )

        # Remove the URL from the question text
        question = user_message.replace(repo_url, "").strip()
        if not question:
            question = "Provide a high-level overview of this repository's architecture and tech stack."

        history = self._build_conversation_history(messages)
        result = self._query_repository(repo_url, question, history)

        if result["success"]:
            return self._format_query_response(result["data"])
        else:
            return f"**Query failed.**\n\nError: {result['error']}\n\nTry re-indexing the repository first."

    def _handle_auto(self, user_message: str, messages: list) -> str:
        repo_url = self._extract_repo_url(user_message)
        if not repo_url:
            # Search history
            for msg in reversed(messages):
                content = msg.get("content", "")
                if isinstance(content, str):
                    repo_url = self._extract_repo_url(content)
                    if repo_url:
                        break

        if not repo_url:
            return (
                "**No GitHub repository URL found.**\n\n"
                "Please include a URL like: `https://github.com/owner/repo`\n\n"
                "Example: *Analyze https://github.com/open-webui/open-webui and explain its architecture*"
            )

        # Index if not already done in this session
        if repo_url not in self._indexed_repos:
            index_result = self._index_repository(repo_url)
            if not index_result["success"]:
                return f"**Failed to index repository.**\n\n`{repo_url}`\n\nError: {index_result['error']}"

        # Build the question
        question = user_message.replace(repo_url, "").strip()
        # Clean up common preamble words left after URL removal
        for prefix in ("analyze", "index", "check", "look at", "review", "examine", "and", "then"):
            if question.lower().startswith(prefix):
                question = question[len(prefix):].strip()

        if not question:
            question = "Provide a high-level overview of this repository including its purpose, architecture, tech stack, and main components."

        history = self._build_conversation_history(messages)
        result = self._query_repository(repo_url, question, history)

        if result["success"]:
            header = f"**Repository:** `{repo_url}`\n\n---\n\n"
            return header + self._format_query_response(result["data"])
        else:
            return f"**Query failed for** `{repo_url}`\n\nError: {result['error']}"
