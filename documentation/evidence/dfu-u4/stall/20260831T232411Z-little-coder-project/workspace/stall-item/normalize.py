"""normalize - the item's implementation file. THIS is the file to write.

It ships as a stub on purpose: every attempt receives exactly this starting point.
"""


def normalize(text):
    """Normalize a label: trim, collapse internal whitespace, lowercase."""
    return " ".join(text.split()).lower()
