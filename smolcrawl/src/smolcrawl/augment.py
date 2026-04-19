"""
Markdown RAG Augmenter Module

Normalizes pseudo-headers (bold, colon, date, numbered, ALL CAPS) into proper
markdown headers and injects metadata blocks for optimal RAG chunking.

Zero external dependencies — only re and pathlib from the standard library.
"""

import re
from typing import List, Optional, Tuple

# Lazy import to avoid circular dependency at module level
Page = None


def _get_page_class():
    global Page
    if Page is None:
        from smolcrawl.db import Page as _Page
        Page = _Page
    return Page


# --- Header pattern detection ---

# Patterns in priority order
_RE_MARKDOWN_HEADER = re.compile(r'^(#{1,6})\s+(.+)$')
_RE_BOLD = re.compile(r'^\*\*(.+?)\*\*\s*$')
_RE_COLON = re.compile(r'^([A-Z][A-Za-z0-9 ]{2,50}):\s*$')
_RE_DATE = re.compile(r'^\d{4}[\s./-]\d{2}[\s./-]\d{2}\b')
_RE_NUMBERED = re.compile(r'^\d+\.\s+(.+)$')
_RE_ALL_CAPS = re.compile(r'^[A-Z][A-Z0-9 ]{2,60}$')

# Words that look like ALL CAPS but are not headers
_ALL_CAPS_STOPWORDS = frozenset({
    'NOTE', 'TODO', 'FIXME', 'HACK', 'XXX', 'WARNING', 'CAUTION',
    'IMPORTANT', 'TIP', 'INFO', 'DEPRECATED', 'BUG', 'REVIEW',
})


def check_header_patterns(line: str) -> Optional[Tuple[str, str]]:
    """Classify a line into a header pattern type.

    Returns:
        Tuple of (pattern_type, extracted_text) or None if not a header.
        pattern_type is one of: 'markdown', 'bold', 'colon', 'date', 'numbered', 'allcaps'
    """
    stripped = line.strip()
    if not stripped:
        return None

    m = _RE_MARKDOWN_HEADER.match(stripped)
    if m:
        return ('markdown', m.group(2).strip())

    m = _RE_BOLD.match(stripped)
    if m:
        return ('bold', m.group(1).strip())

    m = _RE_COLON.match(stripped)
    if m:
        return ('colon', m.group(1).strip())

    if _RE_DATE.match(stripped):
        return ('date', stripped.strip())

    m = _RE_NUMBERED.match(stripped)
    if m:
        return ('numbered', stripped.strip())

    if _RE_ALL_CAPS.match(stripped):
        word = stripped.strip()
        if word not in _ALL_CAPS_STOPWORDS:
            return ('allcaps', word)

    return None


def determine_header_level_by_context(
    breadcrumb: List[Tuple[int, str]],
    pattern_type: str,
    text: str,
) -> int:
    """Assign header level 1-6 based on breadcrumb context and pattern type.

    Args:
        breadcrumb: Current header stack as [(level, text), ...]
        pattern_type: One of 'bold', 'colon', 'date', 'numbered', 'allcaps'
        text: The header text

    Returns:
        Header level (1-6)
    """
    current_depth = breadcrumb[-1][0] if breadcrumb else 0

    if pattern_type == 'date':
        return min(max(current_depth, 1), 2)

    if pattern_type == 'colon':
        return min(current_depth + 1, 3) if current_depth >= 1 else 2

    if pattern_type == 'numbered':
        return min(current_depth + 1, 4) if current_depth >= 1 else 2

    if pattern_type == 'allcaps':
        return min(max(current_depth, 1), 2)

    if pattern_type == 'bold':
        return min(current_depth + 1, 3) if current_depth >= 1 else 1

    # Fallback
    return 2


def convert_to_proper_header(text: str, level: int, pattern_type: str) -> str:
    """Build a clean markdown header string.

    Args:
        text: Header text content
        level: Header level (1-6)
        pattern_type: Original pattern type for cleanup rules

    Returns:
        Proper markdown header line
    """
    # Clean the text
    clean = text.strip()

    # For all-caps, convert to title case
    if pattern_type == 'allcaps':
        clean = clean.title()

    # Remove trailing colon for colon-style headers
    if pattern_type == 'colon':
        clean = clean.rstrip(':').strip()

    # Remove bold markers if still present
    clean = clean.strip('*').strip()

    prefix = '#' * level
    return f"{prefix} {clean}"


def guess_aliases_from_heading(text: str) -> str:
    """Extract up to 5 keywords from a heading for the Aliases metadata field.

    Args:
        text: The heading text

    Returns:
        Comma-separated alias string (up to 120 chars)
    """
    # Strip markdown formatting and punctuation
    clean = re.sub(r'[#*_`\[\](){}|\\/<>:;!?.,\'"@$%^&+=~]', ' ', text)
    words = clean.split()

    # Filter: keep words >= 3 chars, skip common stop words
    stop = {'the', 'and', 'for', 'with', 'from', 'that', 'this', 'are', 'was',
            'were', 'been', 'have', 'has', 'had', 'not', 'but', 'can', 'all'}
    keywords = [w for w in words if len(w) >= 3 and w.lower() not in stop]

    # Take up to 5 unique keywords
    seen = set()
    unique = []
    for kw in keywords:
        lower = kw.lower()
        if lower not in seen:
            seen.add(lower)
            unique.append(kw)
        if len(unique) >= 5:
            break

    result = ', '.join(unique)
    return result[:120] if len(result) > 120 else result


def build_metadata_block(
    doc_title: str,
    source_url: str,
    breadcrumb: List[Tuple[int, str]],
    aliases: str,
) -> str:
    """Format the 4-line metadata block injected after each header.

    Args:
        doc_title: Document title (H1 or filename)
        source_url: Source URL or file path
        breadcrumb: Current header stack as [(level, text), ...]
        aliases: Comma-separated keyword aliases

    Returns:
        Metadata block string (with trailing newline)
    """
    section = ' > '.join(t for _, t in breadcrumb) if breadcrumb else ''
    lines = [
        f'[DocTitle: {doc_title}]',
        f'[Path: {source_url}]',
        f'[Section: {section}]',
    ]
    if aliases:
        lines.append(f'[Aliases: {aliases}]')
    return '\n'.join(lines) + '\n'


def augment_markdown(
    md_text: str,
    source_url: str = '',
    doc_title: str = '',
) -> str:
    """Orchestrate line-by-line markdown augmentation.

    Walks through the markdown text, detects pseudo-headers, normalizes them
    to proper markdown headers, and injects metadata blocks.

    Args:
        md_text: Raw markdown text
        source_url: Source URL or file path for metadata
        doc_title: Document title (auto-detected from H1 if empty)

    Returns:
        Augmented markdown string
    """
    lines = md_text.split('\n')
    output_lines: List[str] = []
    breadcrumb: List[Tuple[int, str]] = []
    found_title = doc_title

    for i, line in enumerate(lines):
        result = check_header_patterns(line)

        if result is None:
            output_lines.append(line)
            continue

        pattern_type, text = result

        if pattern_type == 'markdown':
            # Already a proper header — extract level and text
            m = _RE_MARKDOWN_HEADER.match(line.strip())
            level = len(m.group(1))
            header_text = m.group(2).strip()
        else:
            # Pseudo-header — determine level and convert
            level = determine_header_level_by_context(breadcrumb, pattern_type, text)
            header_text = text

        # Auto-detect title from first H1
        if not found_title and level == 1:
            found_title = header_text

        # Update breadcrumb: pop deeper or same-level entries
        while breadcrumb and breadcrumb[-1][0] >= level:
            breadcrumb.pop()
        breadcrumb.append((level, header_text))

        # Build the proper header line
        if pattern_type == 'markdown':
            output_lines.append(line)
        else:
            header_line = convert_to_proper_header(header_text, level, pattern_type)
            output_lines.append(header_line)

        # Inject metadata block
        aliases = guess_aliases_from_heading(header_text)
        meta = build_metadata_block(
            doc_title=found_title or 'Untitled',
            source_url=source_url,
            breadcrumb=breadcrumb,
            aliases=aliases,
        )
        output_lines.append(meta)

    return '\n'.join(output_lines)


def augment_pages(pages: list) -> list:
    """Batch process Page objects, returning new Pages with augmented content.

    Args:
        pages: List of Page objects to augment

    Returns:
        New list of Page objects with augmented .content fields
    """
    PageClass = _get_page_class()
    augmented = []
    for page in pages:
        new_content = augment_markdown(
            md_text=page.content,
            source_url=page.url,
            doc_title=page.title,
        )
        augmented.append(PageClass(
            url=page.url,
            title=page.title,
            content=new_content,
            raw_html=page.raw_html,
        ))
    return augmented
