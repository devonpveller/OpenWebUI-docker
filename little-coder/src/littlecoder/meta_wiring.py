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
from .judge_gate import require as require_judge_admission
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
        can mint clusters AND draft tier-0 skills (Chapter 4 §4e —
        skill_dir from `config.paths.skill_dir`).

    THE JUDGE FLAG IS NOT READ HERE. `judge_gate.require` is the single
    reader of `observer.judge_enabled` in this package and the single place
    the "may the judge run?" question is decided; it RAISES
    `JudgeNotCalibratedError` if the config asks for the judge without a valid
    human rating record at `/app/config/judge-enablement-rating.yaml`, so a
    config edited inside the container, or committed past the pre-commit
    guard, still cannot start a judging daemon. See judge_gate.py's header and
    tests/test_judge_gate_chokepoint.py, which fails if any other module reads
    the flag or constructs a `Judge`."""
    judge_permitted = require_judge_admission(config)
    if not config.observer.enabled or not judge_permitted:
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
        # Chapter 4 §4e — when the judge is wired, drafting is too.
        # `little-coder-skill/` is declared in Tool (`PathsConfig.skill_dir`).
        skill_dir=config.paths.skill_dir,
    )
