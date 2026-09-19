"""Reference solution - FIXTURE ONLY, never planted into a workspace.

The fixture runner copies this over the stub to prove the harness end to end without a
model. It lives outside files/ so that it is not part of what a real runner is given, and
it is excluded from the item digest for the same reason: it is scaffolding, not the item.
"""

import re
import unicodedata


def slugify(text):
    if text is None:
        return ""
    folded = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
