"""knowledge_types - the ONE knowledge-question type shared by the question generator (plan D1,
kp-questions) and the package builder (knowledge_package, D2). Agent knowledge package, operator
D12 (2026-10-15): tags are `codebase` / `api` / `concept`; the generator assigns the id.

Kept in its own module so neither consumer imports the other: the generator stays free of the
package's research client, and the package does not import the generator's model call (which
has no live call site yet).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, get_args

#: The tag set (closed). codebase = this repository's code, conventions, tests; api = a library's
#: syntax / signature / parameters at a version; concept = how-to / understanding / why.
Tag = Literal["codebase", "api", "concept"]
TAGS: tuple[str, ...] = get_args(Tag)

#: Question text bounds (inclusive), the generator's: a value outside them is dropped by the
#: generator and refused (OPEN `question_text_rejected`) by the package.
MIN_QUESTION_CHARS = 15
# 600: real local Qwen list lines in the bake-off rig (620 lines, 2026-10-14) have median 200
# chars, p90 448, p95 555; the prompt asks for ~160 but the model will not always comply.
MAX_QUESTION_CHARS = 600

_QID_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


@dataclass(frozen=True)
class KnowledgeQuestion:
    """One knowledge question. `id` is assigned by the generator (Q1..Qn in accepted order);
    `tag` decides who answers it; `status` is never changed here (the package reports answered /
    open on its own items). The text is NOT bounds-checked here: the generator drops an
    out-of-bounds value and the package turns it into OPEN `question_text_rejected`."""

    id: str
    text: str
    tag: str
    status: str = "open"

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _QID_RE.match(self.id):
            raise ValueError("question id must match [A-Za-z0-9_-]{1,16}")
        if self.tag not in TAGS:
            raise ValueError(f"question tag must be one of {TAGS}")
        if not isinstance(self.text, str):
            raise ValueError("question text must be a string")
