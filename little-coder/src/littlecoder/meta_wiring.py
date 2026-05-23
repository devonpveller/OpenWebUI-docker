"""Boot-time construction of the Observer's `MetaRunner` (Chapter 3).

Pulled out of `daemon.py` so it can be unit-tested without the daemon's
FastAPI / uvicorn imports. The function is otherwise straight wiring —
the three flavors are explained in its docstring.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import Config
from .judge import Judge
from .llm import ChatClient, EmbeddingClient
from .meta import MetaRunner, default_similarity
from .sanitize import Sanitizer
from .similarity import EmbeddingSimilarity


def build_meta_runner(config: Config) -> MetaRunner:
    """Construct the Observer's MetaRunner. Three flavors:
      - `observer.enabled=False` → stub similarity, no judge. Reports
        still work; iterations produce empty stores.
      - `observer.enabled=True, judge_enabled=False` → stub similarity,
        no judge — same shape as disabled but reports the real cohort
        store. Useful for the operator's prompt-calibration window.
      - both True → embedding-based similarity + Judge wired; iteration
        can mint clusters."""
    if not config.observer.enabled or not config.observer.judge_enabled:
        return MetaRunner(
            observer_cfg=config.observer,
            journals_dir=config.journals.dir,
            cohorts_dir=config.paths.cohorts_dir,
            similarity=default_similarity,
            judge=None,
        )

    embedder = EmbeddingClient(
        base_url=config.inference.embedding_base_url,
        api_key=os.environ.get(config.inference.api_key_env, ""),
        default_model=config.inference.embedding_model,
    )
    similarity = EmbeddingSimilarity(embedder)
    chat = ChatClient(
        base_url=config.inference.base_url,
        api_key=os.environ.get(config.inference.api_key_env, ""),
        default_model=config.inference.model_reasoning,
        # ChatClient defaults to enforcing-mode sanitization. Explicit
        # here so the boot-time wiring is auditable (design §10.2).
        sanitizer=Sanitizer(
            mode="enforcing", max_body_bytes=config.sanitization.max_body_bytes
        ),
    )
    judge = Judge(
        chat=chat,
        founding_knowledge_paths=[
            Path(p) for p in config.observer.founding_knowledge_paths
        ],
        model=config.inference.model_reasoning,
    )
    return MetaRunner(
        observer_cfg=config.observer,
        journals_dir=config.journals.dir,
        cohorts_dir=config.paths.cohorts_dir,
        similarity=similarity,
        judge=judge,
    )
