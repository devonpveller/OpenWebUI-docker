---
name: github-chat-mcp
description: Instructions for using the GitHub Chat MCP tools to index and query GitHub repositories for codebase analysis
---

# GitHub Chat MCP — Tool Usage Guide

You have access to the **GitHub Chat MCP** tools for analyzing GitHub repositories. These tools let you index a repository and then ask detailed questions about its codebase, architecture, dependencies, and implementation details.

## Available Tools

### 1. `index_repository`

**Purpose:** Index a GitHub repository so it can be queried. This **must be called first** before asking any questions.

**Parameters:**

| Parameter  | Type   | Required | Description |
|-----------|--------|----------|-------------|
| `repo_url` | string | Yes      | GitHub repository URL in the format `https://github.com/owner/repo` |

**Usage rules:**
- Always index before querying. If the user provides a repo URL and a question in the same message, index first, then query.
- The URL must start with `https://github.com/` — do not pass SSH URLs, shorthand like `owner/repo`, or URLs from other forges.
- Indexing is idempotent — re-indexing an already-indexed repo is safe and refreshes the data.
- Confirm successful indexing to the user before proceeding to queries.

### 2. `query_repository`

**Purpose:** Ask a question about an already-indexed repository and get an AI-generated answer with source file references.

**Parameters:**

| Parameter              | Type          | Required | Description |
|-----------------------|---------------|----------|-------------|
| `repo_url`            | string        | Yes      | Same GitHub URL used during indexing |
| `question`            | string        | Yes      | The question to ask about the repository |
| `conversation_history` | list or null  | No       | Previous conversation messages for multi-turn context |

**Usage rules:**
- The repository **must be indexed first**. If you're unsure, index it again — it's safe.
- Write specific, targeted questions. Instead of "tell me about this repo," ask "What web framework does this project use and how are routes organized?"
- For follow-up questions about the same repo, pass previous Q&A pairs in `conversation_history` as `[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]` to maintain context.
- The response includes source file paths — reference these when presenting findings to the user.

## Recommended Workflow

1. **User provides a repo URL** → Call `index_repository` with the URL.
2. **Confirm indexing** → Tell the user the repo is ready.
3. **User asks a question** → Call `query_repository` with the URL and question.
4. **Present the answer** → Include relevant source file references from the response.
5. **Follow-up questions** → Pass conversation history for continuity.

## Effective Question Patterns

Use these question strategies for thorough analysis:

- **Architecture:** "What is the high-level architecture? Describe the main components and how they interact."
- **Tech stack:** "What are the core dependencies and what versions are used?"
- **Entry points:** "Where is the main entry point and how does the application bootstrap?"
- **Specific feature:** "How is authentication implemented? Walk through the flow."
- **Code patterns:** "What design patterns are used in this codebase?"
- **Testing:** "How are tests organized and what testing frameworks are used?"
- **Configuration:** "How is the application configured? What environment variables are required?"

## Error Handling

- If indexing fails, verify the URL is correct and the repository is public (or accessible).
- If a query returns an error, try re-indexing the repository first.
- If the API is unreachable, inform the user that the GitHub Chat service may be temporarily unavailable.

## Important Constraints

- Only works with **public GitHub repositories** (or repos accessible to the configured API key).
- Requires the repository to be **indexed before querying** — always index first.
- The GitHub Chat API is a freemium service — no API key is required for basic usage.
- Do not fabricate repository analysis results. If the tool returns an error or empty result, say so.
