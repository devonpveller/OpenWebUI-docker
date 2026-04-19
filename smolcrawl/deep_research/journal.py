"""
Research journal backed by Fileshed-compatible storage.

Single Responsibility: Only handles reading/writing journal entries to disk.
Encapsulation: Path resolution and file I/O are internal details.
"""

import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional

from .models import (
    CrawlResult,
    DiscoveredDomain,
    IterationResult,
    ResearchSession,
    Valves,
)


class ResearchJournal:
    """Persistent research journal stored in Fileshed-compatible paths.

    Each research session creates a directory under the user's Storage zone.
    Files are written incrementally as the research progresses, serving as
    both working memory (context for LLM) and audit trail.
    """

    def __init__(self, valves: Valves):
        self._valves = valves

    def resolve_session_dir(
        self,
        user_id: str,
        slug: str,
        namespace: str = "deep-research",
    ) -> str:
        """Build a Fileshed-compatible directory path for a research session.

        Args:
            user_id: OWUI user ID for path scoping.
            slug: Short URL-safe session name derived from the query.
            namespace: Top-level directory name (deep-research or research).

        Returns:
            Absolute path to the session directory.
        """
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_name = f"{timestamp}-{slug}"

        if self._valves.fileshed_compatible and user_id:
            return os.path.join(
                self._valves.storage_base_path,
                "users",
                user_id,
                "Storage",
                "data",
                namespace,
                session_name,
            )
        return os.path.join(
            self._valves.storage_base_path,
            namespace,
            session_name,
        )

    def write_entry(self, session_dir: str, filename: str, content: str) -> str:
        """Write a journal entry to the session directory.

        Args:
            session_dir: Absolute path to the session directory.
            filename: Name of the file to write (e.g. '00-prompt.md').
            content: Markdown content to write.

        Returns:
            Absolute path to the written file.
        """
        if not self._valves.save_journal:
            return ""

        path = os.path.join(session_dir, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def read_entry(self, session_dir: str, filename: str) -> str:
        """Read back a journal entry for context building.

        Args:
            session_dir: Absolute path to the session directory.
            filename: Name of the file to read.

        Returns:
            File content, or empty string if not found.
        """
        path = os.path.join(session_dir, filename)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def list_entries(self, session_dir: str) -> List[str]:
        """List all journal entry filenames in the session directory.

        Returns:
            Sorted list of filenames.
        """
        if not os.path.isdir(session_dir):
            return []
        return sorted(
            f
            for f in os.listdir(session_dir)
            if os.path.isfile(os.path.join(session_dir, f))
        )

    # --- Structured Writers ---

    def write_prompt(
        self,
        session: ResearchSession,
        model_id: str,
    ) -> None:
        """Write the initial prompt file (00-prompt.md)."""
        content = (
            f"# Research Session\n\n"
            f"**Query:** {session.query}\n"
            f"**Timestamp:** {session.created_at.isoformat()}\n"
            f"**Model:** {model_id}\n"
            f"**Session ID:** {session.session_id}\n"
        )
        self.write_entry(session.session_dir, "00-prompt.md", content)

    def write_anchor(self, session: ResearchSession) -> None:
        """Write the research anchor file (00-anchor.md).

        The anchor is a structured extraction of the query's key concepts,
        intent, and scope boundaries. It is threaded through every search,
        analysis, and synthesis prompt to prevent research drift.
        """
        content = (
            f"# Research Anchor\n\n"
            f"```\n{session.anchor}\n```\n\n"
            f"This anchor was extracted at session start and is threaded through "
            f"every search, analysis, and synthesis prompt to prevent drift.\n"
        )
        self.write_entry(session.session_dir, "00-anchor.md", content)

    def write_domains(
        self,
        session: ResearchSession,
        existing_collections: List[Dict],
    ) -> None:
        """Write domain discovery results (01-domains.md)."""
        lines = ["# Domain Discovery\n"]

        if existing_collections:
            lines.append("## Existing Collections\n")
            for col in existing_collections:
                file_count = len(col.get("data", {}).get("file_ids", []))
                lines.append(
                    f"- **{col['name']}** ({file_count} files): "
                    f"{col.get('description', 'No description')}\n"
                )
            lines.append("")

        lines.append("## Discovered Domains\n")
        for i, domain in enumerate(session.discovered_domains, 1):
            status = "✅ Already covered" if domain.already_covered else "🆕 New"
            lines.append(
                f"{i}. [{domain.score:.2f}] **{domain.domain}**\n"
                f"   {domain.rationale}\n"
                f"   Status: {status}\n"
            )

        self.write_entry(session.session_dir, "01-domains.md", "\n".join(lines))

    def write_crawl_status(self, session: ResearchSession) -> None:
        """Write crawl results (02-crawl-status.md)."""
        lines = ["# Crawl Status\n"]
        for result in session.crawl_results:
            status = "✅" if result.success else "❌"
            lines.append(
                f"## {status} {result.domain}\n\n"
                f"- **KB Name:** {result.kb_name}\n"
                f"- **Pages Crawled:** {result.pages_crawled}\n"
                f"- **Duration:** {result.duration_seconds:.1f}s\n"
            )
            if result.error:
                lines.append(f"- **Error:** {result.error}\n")
            lines.append("")

        self.write_entry(session.session_dir, "02-crawl-status.md", "\n".join(lines))

    def write_iteration(
        self,
        session: ResearchSession,
        iteration: IterationResult,
    ) -> None:
        """Write an iteration result file."""
        file_num = iteration.iteration_number + 2
        filename = f"{file_num:02d}-iteration-{iteration.iteration_number}.md"

        lines = [
            f"# Iteration {iteration.iteration_number}\n",
            f"## Search Terms\n",
            ", ".join(f"`{t}`" for t in iteration.search_terms) + "\n",
            f"\n## Collections Queried\n",
            ", ".join(iteration.collections_queried) + "\n",
            f"\n## Results\n",
            f"- Chunks found: {iteration.chunks_found}\n",
            f"- New (deduplicated): {iteration.new_chunks}\n",
            f"\n## Summary\n\n",
            iteration.summary + "\n",
        ]

        if iteration.new_concepts:
            lines.append(f"\n## New Concepts Identified\n\n")
            lines.extend(f"- {c}\n" for c in iteration.new_concepts)

        self.write_entry(session.session_dir, filename, "\n".join(lines))

    def write_synthesis(
        self,
        session: ResearchSession,
        synthesis_content: str,
    ) -> None:
        """Write the final synthesis file."""
        file_num = len(session.iterations) + 3
        filename = f"{file_num:02d}-synthesis.md"

        content = f"# Synthesis\n\n{synthesis_content}\n"
        self.write_entry(session.session_dir, filename, content)

    def write_manifest(self, session: ResearchSession) -> None:
        """Write a machine-readable manifest.json for the session."""
        manifest = {
            "session_id": session.session_id,
            "query": session.query,
            "created_at": session.created_at.isoformat(),
            "phase": session.phase.value,
            "model_id": session.model_id,
            "domains": [
                {
                    "domain": d.domain,
                    "url": d.url,
                    "score": d.score,
                    "already_covered": d.already_covered,
                }
                for d in session.discovered_domains
            ],
            "crawls": [
                {
                    "domain": c.domain,
                    "kb_name": c.kb_name,
                    "pages_crawled": c.pages_crawled,
                    "success": c.success,
                }
                for c in session.crawl_results
            ],
            "iterations": [
                {
                    "number": it.iteration_number,
                    "terms": it.search_terms,
                    "collections": it.collections_queried,
                    "chunk_count": it.chunks_found,
                    "new_chunk_count": it.new_chunks,
                }
                for it in session.iterations
            ],
            "seen_chunks": len(session.seen_chunk_keys),
        }

        self.write_entry(
            session.session_dir,
            "manifest.json",
            json.dumps(manifest, indent=2),
        )

    # --- Helpers ---

    @staticmethod
    def slugify(text: str, max_length: int = 40) -> str:
        """Convert text to a URL-safe slug.

        Args:
            text: Input text to slugify.
            max_length: Maximum length of the slug.

        Returns:
            Lowercase alphanumeric slug with hyphens.
        """
        slug = re.sub(r"[^\w\s-]", "", text.lower())
        slug = re.sub(r"[\s_]+", "-", slug).strip("-")
        return slug[:max_length]
