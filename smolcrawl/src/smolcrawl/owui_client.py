"""
Open WebUI Knowledge Client

Manages knowledge base lifecycle and file uploads to Open WebUI.
Supports manifest-based incremental sync for efficient re-crawling.
"""

import hashlib
import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import httpx
from loguru import logger

from smolcrawl.db import Page
from smolcrawl.utils import get_storage_path


@dataclass
class OwuiConfig:
    """Configuration for the OWUI Knowledge Client."""
    base_url: str = "http://localhost:3000"
    api_key: str = ""
    knowledge_base_name: str = ""
    upload_concurrency: int = 1
    retry_attempts: int = 3
    retry_backoff_base: float = 1.0
    processing_timeout: int = 300


@dataclass
class SyncResult:
    """Result of a sync operation."""
    knowledge_base_id: str = ""
    total_files: int = 0
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    deleted: int = 0
    errors: List[str] = field(default_factory=list)


class OwuiKnowledgeClient:
    """Manages knowledge base lifecycle and file uploads to Open WebUI."""

    def __init__(self, config: OwuiConfig):
        self.config = config
        self._lock = threading.Lock()
        self._client = httpx.Client(
            base_url=config.base_url,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Accept": "application/json",
            },
            timeout=120.0,
        )

    def close(self):
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # --- Knowledge Base Management ---

    def find_knowledge_base(self, name: str) -> Optional[dict]:
        """Find a knowledge base by name.

        Args:
            name: The knowledge base name to search for.

        Returns:
            KB dict if found, None otherwise.
        """
        resp = self._request_with_retry("GET", "/api/v1/knowledge/")
        if resp is None:
            return None

        # Handle both list and paginated dict responses
        items = resp.get("items", resp) if isinstance(resp, dict) else resp
        for kb in items:
            if kb.get("name") == name:
                return kb
        return None

    def create_knowledge_base(self, name: str, description: str = "") -> dict:
        """Create a new knowledge base.

        Args:
            name: Name for the new KB.
            description: Optional description.

        Returns:
            Created KB dict.
        """
        body = {"name": name, "description": description}
        resp = self._request_with_retry("POST", "/api/v1/knowledge/create", json=body)
        if resp is None:
            raise RuntimeError(f"Failed to create knowledge base '{name}'")
        logger.info(f"Created knowledge base '{name}' (id={resp.get('id', 'unknown')})")
        return resp

    def get_or_create_knowledge_base(self, name: str, description: str = "") -> str:
        """Get existing KB id or create a new one.

        Args:
            name: Knowledge base name.
            description: Description for new KB.

        Returns:
            Knowledge base id string.
        """
        existing = self.find_knowledge_base(name)
        if existing:
            kb_id = existing["id"]
            logger.info(f"Found existing knowledge base '{name}' (id={kb_id})")
            return kb_id
        created = self.create_knowledge_base(name, description)
        return created["id"]

    # --- File Upload ---

    def upload_file(self, filename: str, content: bytes) -> str:
        """Upload a file to OWUI.

        Args:
            filename: Name for the uploaded file.
            content: File content as bytes.

        Returns:
            The uploaded file id.
        """
        files = {"file": (filename, content, "text/markdown")}
        resp = self._request_with_retry(
            "POST", "/api/v1/files/", files=files
        )
        if resp is None:
            raise RuntimeError(f"Failed to upload file '{filename}'")
        return resp["id"]

    def wait_for_processing(self, file_id: str) -> dict:
        """Poll file processing status until completed or failed.

        Args:
            file_id: The OWUI file id to poll.

        Returns:
            Final status dict.

        Raises:
            TimeoutError: if processing exceeds configured timeout.
            RuntimeError: if processing fails.
        """
        deadline = time.monotonic() + self.config.processing_timeout
        poll_interval = 2.0

        while time.monotonic() < deadline:
            resp = self._request_with_retry(
                "GET", f"/api/v1/files/{file_id}"
            )
            if resp is None:
                raise RuntimeError(f"Failed to get status for file {file_id}")

            # OWUI may return the file object directly with embedded status info
            # or the file may be ready as soon as upload completes
            meta = resp.get("meta", {})
            status = meta.get("status", "completed")

            if status == "completed":
                return resp
            if status == "failed":
                error = meta.get("error", "unknown error")
                raise RuntimeError(
                    f"File processing failed for {file_id}: {error}"
                )

            time.sleep(poll_interval)

        raise TimeoutError(
            f"File {file_id} processing timed out after "
            f"{self.config.processing_timeout}s"
        )

    def add_file_to_knowledge_base(self, kb_id: str, file_id: str) -> dict:
        """Link a processed file to a knowledge base.

        Args:
            kb_id: Knowledge base id.
            file_id: File id to link.

        Returns:
            Response dict.
        """
        body = {"file_id": file_id}
        try:
            resp = self._request_with_retry(
                "POST", f"/api/v1/knowledge/{kb_id}/file/add", json=body
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                detail = ""
                try:
                    detail = e.response.json().get("detail", "")
                except Exception:
                    detail = e.response.text
                if "duplicate" in detail.lower():
                    logger.info(
                        f"File {file_id} has duplicate content in KB {kb_id}, "
                        "treating as already indexed"
                    )
                    return {"duplicate": True}
            raise
        if resp is None:
            raise RuntimeError(
                f"Failed to add file {file_id} to KB {kb_id}"
            )
        return resp

    def remove_file_from_knowledge_base(self, kb_id: str, file_id: str) -> Optional[dict]:
        """Unlink a file from a knowledge base.

        Args:
            kb_id: Knowledge base id.
            file_id: File id to unlink.

        Returns:
            Response dict or None on failure.
        """
        body = {"file_id": file_id}
        return self._request_with_retry(
            "POST", f"/api/v1/knowledge/{kb_id}/file/remove", json=body
        )

    def delete_file(self, file_id: str) -> Optional[dict]:
        """Delete an uploaded file.

        Args:
            file_id: File id to delete.

        Returns:
            Response dict or None on failure.
        """
        return self._request_with_retry("DELETE", f"/api/v1/files/{file_id}")

    # --- Sync Orchestration ---

    def sync_pages(
        self,
        pages: List[Page],
        kb_name: str,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> SyncResult:
        """Full sync workflow: upload pages to a named KB with incremental dedup.

        Steps:
            1. Get or create knowledge base.
            2. Load manifest for incremental sync.
            3. For each page: skip if unchanged, upload if new/changed.
            4. Delete manifest entries for pages no longer in the crawl.
            5. Save updated manifest.

        Args:
            pages: List of Page objects to sync.
            kb_name: Name of the target knowledge base.
            on_progress: Callback(current, total, filename) for progress updates.

        Returns:
            SyncResult with upload statistics.
        """
        result = SyncResult(total_files=len(pages))

        # Step 1: KB
        try:
            kb_id = self.get_or_create_knowledge_base(
                kb_name,
                description=f"Auto-synced by SmolCrawl on {datetime.now(timezone.utc).isoformat()}",
            )
            result.knowledge_base_id = kb_id
        except Exception as e:
            result.errors.append(f"KB creation failed: {e}")
            return result

        # Step 2: Manifest
        manifest = self._load_manifest(kb_name)
        new_manifest_files: Dict[str, dict] = {}
        page_urls = {page.url for page in pages}

        # Step 3: Process pages
        counter = {"current": 0}

        def _process_page(page: Page) -> Optional[str]:
            """Process a single page. Returns error string or None."""
            content_bytes = page.content.encode("utf-8")
            content_hash = self._content_hash(page.content)
            filename = self._url_to_filename(page.url)

            # Check manifest for unchanged content
            existing = manifest.get("files", {}).get(page.url)
            if existing and existing.get("content_hash") == content_hash:
                with self._lock:
                    new_manifest_files[page.url] = existing
                    result.skipped += 1
                    counter["current"] += 1
                if on_progress:
                    on_progress(counter["current"], len(pages), filename)
                return None

            try:
                # Delete old file if updating
                if existing and existing.get("owui_file_id"):
                    try:
                        self.remove_file_from_knowledge_base(
                            kb_id, existing["owui_file_id"]
                        )
                        self.delete_file(existing["owui_file_id"])
                    except Exception:
                        pass  # Best effort cleanup

                # Upload new file
                file_id = self.upload_file(filename, content_bytes)
                self.wait_for_processing(file_id)
                add_resp = self.add_file_to_knowledge_base(kb_id, file_id)
                is_duplicate = isinstance(add_resp, dict) and add_resp.get("duplicate")

                if is_duplicate:
                    # Content already in vector DB; clean up the orphan upload
                    try:
                        self.delete_file(file_id)
                    except Exception:
                        pass

                with self._lock:
                    new_manifest_files[page.url] = {
                        "content_hash": content_hash,
                        "owui_file_id": file_id if not is_duplicate else None,
                        "last_updated": datetime.now(timezone.utc).isoformat(),
                    }
                    if is_duplicate:
                        result.skipped += 1
                    else:
                        result.uploaded += 1
                    counter["current"] += 1

                if on_progress:
                    on_progress(counter["current"], len(pages), filename)
                return None

            except Exception as e:
                with self._lock:
                    result.failed += 1
                    result.errors.append(f"{page.url}: {e}")
                    counter["current"] += 1
                if on_progress:
                    on_progress(counter["current"], len(pages), filename)
                return str(e)

        # Run with thread pool
        with ThreadPoolExecutor(
            max_workers=self.config.upload_concurrency
        ) as executor:
            futures = {
                executor.submit(_process_page, page): page for page in pages
            }
            for future in as_completed(futures):
                future.result()  # Propagate unexpected exceptions

        # Step 4: Delete removed pages
        old_urls = set(manifest.get("files", {}).keys())
        removed_urls = old_urls - page_urls
        for url in removed_urls:
            entry = manifest["files"][url]
            file_id = entry.get("owui_file_id")
            if file_id:
                try:
                    self.remove_file_from_knowledge_base(kb_id, file_id)
                    self.delete_file(file_id)
                    result.deleted += 1
                except Exception as e:
                    result.errors.append(f"Delete {url}: {e}")

        # Step 5: Save manifest
        updated_manifest = {
            "knowledge_base_id": kb_id,
            "knowledge_base_name": kb_name,
            "last_sync": datetime.now(timezone.utc).isoformat(),
            "files": new_manifest_files,
        }
        self._save_manifest(kb_name, updated_manifest)

        logger.info(
            f"Sync complete: {result.uploaded} uploaded, "
            f"{result.skipped} skipped, {result.failed} failed, "
            f"{result.deleted} deleted"
        )
        return result

    # --- Manifest (Incremental Sync) ---

    def _get_manifest_dir(self) -> str:
        """Get the manifests directory path."""
        path = os.path.join(get_storage_path(), "owui-manifests")
        os.makedirs(path, exist_ok=True)
        return path

    def _manifest_path(self, kb_name: str) -> str:
        """Get the manifest file path for a given KB name."""
        safe_name = kb_name.replace(" ", "_").replace("/", "_")
        return os.path.join(self._get_manifest_dir(), f"{safe_name}.json")

    def _load_manifest(self, kb_name: str) -> dict:
        """Load manifest from disk.

        Args:
            kb_name: Knowledge base name.

        Returns:
            Manifest dict with 'files' key, or empty dict.
        """
        path = self._manifest_path(kb_name)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Could not load manifest at {path}: {e}")
        return {"files": {}}

    def _save_manifest(self, kb_name: str, manifest: dict):
        """Persist manifest to disk.

        Args:
            kb_name: Knowledge base name.
            manifest: The manifest dict to save.
        """
        path = self._manifest_path(kb_name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        logger.debug(f"Saved manifest to {path}")

    @staticmethod
    def _content_hash(content: str) -> str:
        """SHA-256 hash of content for deduplication."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _url_to_filename(url: str) -> str:
        """Convert a URL to a safe markdown filename."""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path.strip("/").replace("/", "_") or "index"
        # Remove unsafe characters
        path = "".join(c for c in path if c.isalnum() or c in ("_", "-", "."))
        if not path.endswith(".md"):
            path += ".md"
        return path

    # --- HTTP Helpers ---

    def _request_with_retry(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> Optional[dict]:
        """Make an HTTP request with exponential backoff retry.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            path: URL path relative to base_url.
            **kwargs: Additional arguments passed to httpx.

        Returns:
            Response JSON as dict, or None on failure.
        """
        last_error = None
        for attempt in range(self.config.retry_attempts):
            try:
                resp = self._client.request(method, path, **kwargs)
                resp.raise_for_status()

                if resp.status_code == 204:
                    return {}

                return resp.json()

            except httpx.HTTPStatusError as e:
                last_error = e
                # Don't retry client errors (4xx) — they won't succeed
                if e.response.status_code < 500:
                    raise
                if attempt < self.config.retry_attempts - 1:
                    backoff = self.config.retry_backoff_base * (2 ** attempt)
                    backoff = min(backoff, 5.0)
                    logger.warning(
                        f"Request {method} {path} failed (attempt "
                        f"{attempt + 1}/{self.config.retry_attempts}): {e}. "
                        f"Retrying in {backoff:.1f}s"
                    )
                    time.sleep(backoff)

            except httpx.RequestError as e:
                last_error = e
                if attempt < self.config.retry_attempts - 1:
                    backoff = self.config.retry_backoff_base * (2 ** attempt)
                    backoff = min(backoff, 5.0)
                    logger.warning(
                        f"Request {method} {path} failed (attempt "
                        f"{attempt + 1}/{self.config.retry_attempts}): {e}. "
                        f"Retrying in {backoff:.1f}s"
                    )
                    time.sleep(backoff)

        logger.error(f"Request {method} {path} failed after "
                     f"{self.config.retry_attempts} attempts: {last_error}")
        return None

    def test_connection(self) -> bool:
        """Test connectivity to the OWUI instance.

        Returns:
            True if the connection and authentication are valid.
        """
        try:
            resp = self._request_with_retry("GET", "/api/v1/knowledge/")
            return resp is not None
        except Exception:
            return False
