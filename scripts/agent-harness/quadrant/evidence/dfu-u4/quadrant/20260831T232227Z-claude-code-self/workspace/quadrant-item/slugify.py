"""slugify - the item's implementation file. THIS is the file to write.

It ships as a stub on purpose: every quadrant receives exactly this starting point, so the
only variable in the comparison is who does the work and where.
"""

import re
import unicodedata

_SEPARATORS = re.compile(r"[^a-z0-9]+")


def slugify(text):
    """Turn arbitrary text into a URL slug. See test_slugify.py for the exact contract."""
    # NFKD splits accented characters into base + combining mark; dropping the marks
    # (category Mn) folds them to plain ASCII without a lookup table.
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    ascii_only = stripped.encode("ascii", "ignore").decode("ascii").lower()
    # Every run of non-alphanumerics is one separator; leading/trailing ones vanish.
    return _SEPARATORS.sub("-", ascii_only).strip("-")
