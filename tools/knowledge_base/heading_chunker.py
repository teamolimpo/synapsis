"""Heading-based chunker for Markdown vault files.

Splits ``.md`` files by H2/H3 headings, respecting code fences and
frontmatter boundaries.  Produces a list of :class:`Chunk` objects with
hierarchical heading paths, line ranges, and YAML frontmatter metadata.

Usage::

    from tools.knowledge_base.heading_chunker import chunk_markdown, Chunk

    text = Path("some-file.md").read_text()
    chunks = chunk_markdown(text, "some-file.md")
    for c in chunks:
        print(c.heading_path, c.token_count)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

# Matches opening `---` that could start frontmatter
_RE_FM_DELIMITER = re.compile(r"^---\s*$", re.MULTILINE)

# Matches H2 (##) and H3 (###) headings with optional trailing ` #` markers
_RE_HEADING = re.compile(r"^(#{2,3})\s+(.+?)(?:\s+#+)?$")

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class Chunk:
    """A single chunk extracted from a Markdown file.

    Attributes:
        content: The chunk's text content (including the heading line).
        file_path: Path to the source file (relative or absolute).
        file_hash: SHA256 hex digest of the **entire** source file.
        heading_path: Hierarchical path, e.g. ``/Architettura/Pattern``.
            ``"/"`` for preamble (before any heading).
        heading_level: 0 = preamble, 2 = H2, 3 = H3.
        frontmatter: Parsed YAML frontmatter dict (inherited from the file).
        start_line: 1-indexed start line in the source file.
        end_line: 1-indexed end line (inclusive).
        token_count: Approximate token count (``len(content) // 4``).
    """

    content: str
    file_path: str
    file_hash: str
    heading_path: str
    heading_level: int
    start_line: int
    end_line: int
    token_count: int
    frontmatter: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict[str, Any], int | None]:
    """Extract YAML frontmatter and return ``(frontmatter_dict, body_start).

    *body_start* is the 0-indexed line number where body content begins
    (the line **after** the closing ``---``).  Returns ``({}, None)`` when
    the file has no frontmatter.

    Args:
        text: Full text of the Markdown file.

    Returns:
        Tuple of ``(parsed_dict, body_start_line_or_None)``.
    """
    lines = text.splitlines()
    if not lines or not lines[0].strip().startswith("---"):
        return {}, None

    # Find closing ---
    for i in range(1, len(lines)):
        if _RE_FM_DELIMITER.match(lines[i]):
            fm_text = "\n".join(lines[1:i])
            body_start = i + 1  # 0-indexed line after closing ---
            try:
                fm = yaml.safe_load(fm_text) or {}
            except yaml.YAMLError:
                fm = {}
            if not isinstance(fm, dict):
                fm = {}
            # Convert date/datetime to ISO string for JSON safety
            for k, v in list(fm.items()):
                if hasattr(v, "isoformat"):
                    fm[k] = v.isoformat()
            return fm, body_start

    return {}, None


# ---------------------------------------------------------------------------
# Core chunking
# ---------------------------------------------------------------------------


def chunk_markdown(
    text: str,
    file_path: str,
    max_chunk_size: int = 300,
    min_chunk_size: int = 50,
) -> list[Chunk]:
    """Split a Markdown document into chunks by H2/H3 headings.

    Algorithm
    ---------
    1. Parse YAML frontmatter (first ``---`` … ``---`` block).
    2. Iterate lines, tracking `` ``` `` code fence nesting.
    3. On an H2/H3 heading **outside** a code fence:
       - Finalise the previous chunk.
       - Update the heading-path hierarchy.
       - Start a new chunk headed by this heading.
    4. After the main loop, apply post-processing:
       - Merge chunks smaller than *min_chunk_size* into the next chunk.
       - Merge dangling headers (chunk is only a heading line).
       - Split chunks larger than *max_chunk_size* by paragraph break.

    Args:
        text: Full content of the Markdown file.
        file_path: Path used for ``Chunk.file_path``.
        max_chunk_size: Maximum lines per chunk before forced split.
        min_chunk_size: Minimum lines per chunk; smaller chunks are merged.

    Returns:
        List of :class:`Chunk` objects.
    """
    file_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    frontmatter, body_start = parse_frontmatter(text)
    lines = text.splitlines()
    total_lines = len(lines)

    if body_start is not None and body_start >= total_lines:
        return []
    if total_lines == 0:
        return []

    # ---- Main loop state ----
    raw_chunks: list[dict[str, Any]] = []  # temp list-of-dicts
    current_lines: list[str] = []
    heading_path = "/"
    heading_level = 0
    heading_stack: list[str] = []  # e.g. ["Architettura", "Pattern"]
    in_fence = False
    # 1-indexed start of the current chunk in the original file
    chunk_start = (body_start if body_start is not None else 0) + 1

    def _build_path() -> str:
        if not heading_stack:
            return "/"
        return "/" + "/".join(heading_stack)

    def _save() -> None:
        """Flush *current_lines* into *raw_chunks*."""
        nonlocal current_lines, chunk_start
        if not current_lines:
            return
        content = "\n".join(current_lines)
        end_line = chunk_start + len(current_lines) - 1
        raw_chunks.append(
            {
                "content": content,
                "file_path": file_path,
                "file_hash": file_hash,
                "heading_path": heading_path,
                "heading_level": heading_level,
                "frontmatter": frontmatter,
                "start_line": chunk_start,
                "end_line": end_line,
                "token_count": len(content) // 4,
            }
        )
        current_lines = []

    for i, line in enumerate(lines):
        line_num = i + 1

        # -- code fence tracking --
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence

        # -- skip content before body_start --
        if body_start is not None and i < body_start:
            continue

        if in_fence:
            current_lines.append(line)
            continue

        # -- heading detection (outside fence) --
        m = _RE_HEADING.match(line)
        if m:
            _save()

            level = len(m.group(1))  # 2 or 3
            heading_text = m.group(2).strip()

            # Update heading hierarchy stack
            if level == 2:
                heading_stack = [heading_text]
            elif level == 3:
                if heading_stack:
                    heading_stack = heading_stack[:1] + [heading_text]
                else:
                    heading_stack = [heading_text]

            heading_path = _build_path()
            heading_level = level
            current_lines = [line]
            chunk_start = line_num
            continue

        # -- normal line --
        current_lines.append(line)

    # -- final flush --
    _save()

    if not raw_chunks:
        return []

    # ---- Post-processing ----

    # 1. Merge preamble (heading_level == 0) with first real chunk if
    #    preamble is tiny
    raw_chunks = _merge_small_chunks(raw_chunks, min_chunk_size)

    # 2. Dangling header fix: a chunk whose only content is its heading
    #    line — merge into the next chunk.
    raw_chunks = _fix_dangling_headers(raw_chunks)

    # 3. Split overlarge chunks by paragraph break
    raw_chunks = _split_large_chunks(raw_chunks, max_chunk_size)

    # -- Convert to dataclass --
    return [_dict_to_chunk(d) for d in raw_chunks]


# ---------------------------------------------------------------------------
# Post-processing helpers
# ---------------------------------------------------------------------------


def _merge_small_chunks(
    chunks: list[dict[str, Any]],
    min_size: int,
) -> list[dict[str, Any]]:
    """Merge chunks with fewer than *min_size* lines into the next chunk.

    Each chunk is evaluated against its **original** line count (before any
    merge).  If it qualifies, its content is folded into the *next* chunk
    and the next chunk inherits the updated line range.  No cascading.
    The final chunk is never merged (no successor to consume it).
    """
    if len(chunks) <= 1:
        return chunks

    result: list[dict[str, Any]] = []
    skip_next = False

    for i, chunk in enumerate(chunks):
        if skip_next:
            skip_next = False
            continue

        lines = chunk["content"].splitlines()
        if len(lines) < min_size and i + 1 < len(chunks):
            nxt = chunks[i + 1]
            # Fold small chunk into the next one
            nxt["content"] = chunk["content"] + "\n" + nxt["content"]
            nxt["start_line"] = chunk["start_line"]
            nxt["token_count"] = len(nxt["content"]) // 4
            # nxt keeps its own heading metadata (it's the "primary" chunk)
            result.append(nxt)
            skip_next = True
        else:
            result.append(chunk)

    return result


def _fix_dangling_headers(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fix dangling headers: merge a heading-only chunk into the next.

    A "dangling header" chunk has exactly 1 line (the heading itself) and
    the next chunk has more than 1 line of content.
    """
    if len(chunks) <= 1:
        return chunks

    result: list[dict[str, Any]] = []
    skip_next = False

    for i, chunk in enumerate(chunks):
        if skip_next:
            skip_next = False
            continue

        lines = chunk["content"].splitlines()
        if (
            len(lines) == 1
            and chunk["heading_level"] > 0
            and i + 1 < len(chunks)
            and len(chunks[i + 1]["content"].splitlines()) > 1
        ):
            nxt = chunks[i + 1]
            nxt["content"] = chunk["content"] + "\n" + nxt["content"]
            nxt["start_line"] = chunk["start_line"]
            nxt["token_count"] = len(nxt["content"]) // 4
            nxt["heading_path"] = chunk["heading_path"]
            nxt["heading_level"] = chunk["heading_level"]
            # chunk consumed — next will be evaluated in the next iteration
        else:
            result.append(chunk)

    return result


def _split_large_chunks(
    chunks: list[dict[str, Any]],
    max_size: int,
) -> list[dict[str, Any]]:
    """Split chunks whose line count exceeds *max_size*.

    Splits at paragraph breaks (blank lines) nearest to the ``max_size``
    boundary.  If no blank line is found within 10 lines of the boundary,
    a hard split at ``max_size`` is used.
    """
    result: list[dict[str, Any]] = []
    for chunk in chunks:
        lines = chunk["content"].splitlines()
        if len(lines) <= max_size:
            result.append(chunk)
            continue

        start = 0
        chunk_lines_total = len(lines)
        while start < chunk_lines_total:
            end = min(start + max_size, chunk_lines_total)

            # Try to find a paragraph break near the boundary
            if end < chunk_lines_total:
                # Search backward for a blank line within 10 rows
                split_at = end
                search_start = max(start, end - 10)
                for j in range(end - 1, search_start - 1, -1):
                    if not lines[j].strip():
                        split_at = j + 1  # blank line belongs to previous
                        break
                if split_at != end and split_at > start:
                    end = split_at

            sub = "\n".join(lines[start:end])
            sub_chunk = {
                **chunk,
                "content": sub,
                "start_line": chunk["start_line"] + start,
                "end_line": chunk["start_line"] + end - 1,
                "token_count": len(sub) // 4,
            }
            result.append(sub_chunk)
            start = end

    return result


def _dict_to_chunk(d: dict[str, Any]) -> Chunk:
    """Convert a raw chunk dict to a :class:`Chunk` dataclass."""
    return Chunk(
        content=d["content"],
        file_path=d["file_path"],
        file_hash=d["file_hash"],
        heading_path=d["heading_path"],
        heading_level=d["heading_level"],
        frontmatter=d["frontmatter"],
        start_line=d["start_line"],
        end_line=d["end_line"],
        token_count=d["token_count"],
    )
