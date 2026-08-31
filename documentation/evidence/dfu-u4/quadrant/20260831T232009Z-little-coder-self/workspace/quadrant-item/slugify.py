"""slugify - the item's implementation file. THIS is the file to write.

It ships as a stub on purpose: every quadrant receives exactly this starting point, so the
only variable in the comparison is who does the work and where.
"""


import re
import unicodedata


def slugify(text):
    """Turn arbitrary text into a URL slug. See test_slugify.py for the exact contract."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")
