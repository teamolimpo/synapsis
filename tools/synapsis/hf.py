"""Synapsis HF — handoff absorption into Synapsis.

Replaces the standalone ``tools/handoff/`` MCP server.
Provides pure functions (no MCP) for creating and reading handoff files.

Usage::

    from tools.synapsis.hf import hf_new, hf_get
    from tools.synapsis.store import SynapsisStore
    from tools.common.paths import project_root

    store = SynapsisStore()
    root = project_root()
    result = hf_new(store, root, type="report", title="...",
                    body="...", agent="efesto")
    # -> {"ref": "hf-a3k9", "file": "Library/Handoff/...", ...}
"""

from __future__ import annotations

import json as json_lib
import re
import secrets
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_WIKI_KINDS = {"concept", "entity", "comparison", "overview", "decision", "research"}
_SLUG_MAX_CHARS = 50
_SLUG_MAX_WORDS = 5


# ---------------------------------------------------------------------------
# Ref generation
# ---------------------------------------------------------------------------


def generate_ref(store: Any) -> str:  # noqa: ANN401
    """Generate a unique handoff ref ``hf-`` + 4 hex chars via ``secrets.token_hex(2)``."""
    for _attempt in range(10):
        ref = "hf-" + secrets.token_hex(2)
        if not store.hf_exists(ref):
            return ref
    msg = "Failed to generate unique handoff ref after 10 attempts"
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Frontmatter builder
# ---------------------------------------------------------------------------


def build_frontmatter(
    ref: str,
    type: str,
    title: str,
    agent: str,
    tref: str | None = None,
    st: str = "done",
    prio: str = "med",
    note: str | None = None,
    refs: str | None = None,
    devi: str | None = None,
    wiki: str | None = None,
    hash: str | None = None,
) -> dict[str, Any]:
    """Build frontmatter dict for a handoff file."""
    now = datetime.now()
    fm: dict[str, Any] = {
        "ref": ref,
        "type": type,
        "title": title,
        "agent": agent,
        "data": now.strftime("%Y-%m-%d"),
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "st": st,
        "prio": prio,
    }
    if tref:
        fm["task"] = tref
    if note:
        fm["note"] = note
    if refs:
        fm["refs"] = refs
    if devi:
        fm["devi"] = devi
    if wiki:
        fm["wiki"] = wiki
    if hash:
        fm["hash"] = hash
    return fm


# ---------------------------------------------------------------------------
# Filename builder (mirrors tools/handoff/cli.py _title_to_slug)
# ---------------------------------------------------------------------------


def _title_to_slug(title: str) -> str:
    """Convert a human-readable title to a kebab-case slug.

    Steps mirror ``tools.handoff.cli._title_to_slug`` exactly.
    """
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug)
    slug = slug.strip("-")
    if len(slug) > _SLUG_MAX_CHARS:
        slug = slug[:_SLUG_MAX_CHARS].rstrip("-")
    words = slug.split("-")
    if len(words) > _SLUG_MAX_WORDS:
        slug = "-".join(words[:_SLUG_MAX_WORDS])
    return slug


def build_filename(agent: str, type: str, title: str) -> str:
    """Build canonical handoff filename ``YYYY-MM-DD_HHMM_agent_type_slug.md``."""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M")
    slug = _title_to_slug(title)
    return f"{date_str}_{time_str}_{agent}_{type}_{slug}.md"


# ---------------------------------------------------------------------------
# Handoff file writer (direct, no temp file, no subprocess)
# ---------------------------------------------------------------------------


def write_handoff_file(
    project_root: Path,
    frontmatter: dict[str, Any],
    body: str,
    agent: str,
    type: str,
    title: str,
) -> str:
    """Write a handoff .md file directly to ``Library/Handoff/YYYY/MM/DD/``.

    No temporary files, no subprocess calls.

    Args:
        project_root: Absolute path to project root.
        frontmatter: Dict of YAML-safe frontmatter fields.
        body: Markdown body content (no frontmatter delimiters).
        agent: Agent name.
        type: Handoff type.
        title: Handoff title.

    Returns:
        Relative path (e.g. ``Library/Handoff/2026/06/03/2026-06-03_1430_....md``).
    """
    now = datetime.now()
    year_str = now.strftime("%Y")
    month_str = now.strftime("%m")
    day_str = now.strftime("%d")
    output_dir = project_root / "Library" / "Handoff" / year_str / month_str / day_str
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = build_filename(agent, type, title)
    output_path = output_dir / filename

    yaml_block = yaml.dump(
        frontmatter,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    content = f"---\n{yaml_block}\n---\n\n{body}"
    output_path.write_text(content, encoding="utf-8")

    rel_path = str(output_path.relative_to(project_root))
    logger.debug(f"Handoff file written: {rel_path}")
    return rel_path


# ---------------------------------------------------------------------------
# Wiki section parsing (ported from tools/handoff/server.py)
# ---------------------------------------------------------------------------


def _finalize_wiki_value(key: str, value_list: list) -> str | list:
    """Finalize a parsed wiki field value. Ported from handoff/server.py."""
    raw_items = [v for v in value_list if v]
    text = " ".join(raw_items).strip()
    text = re.sub(r"^(>[-|]?|[|-])\s*", "", text).strip()

    if key in ("tags", "sources"):
        clean_text = text.strip("[]").strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                return json_lib.loads(
                    clean_text
                    if clean_text.startswith("[")
                    else f"[{clean_text}]".replace("'", '"')
                )
            except json_lib.JSONDecodeError:
                pass
        comma_splits = [t.strip().strip('"').strip("'") for t in clean_text.split(",") if t.strip()]
        if len(comma_splits) > 1:
            return comma_splits
        if len(raw_items) > 1:
            return [t.strip().strip('"').strip("'") for t in raw_items if t.strip()]
        return comma_splits if comma_splits else raw_items
    return text


def parse_wiki_section(body: str) -> dict | None:
    """Parse ``## Wiki`` section from handoff body markdown.

    Ported from ``tools/handoff/server.py``.

    Args:
        body: Markdown body content.

    Returns:
        Dict with wiki fields or ``None`` if no section found.
    """
    pattern = r"^##\s+Wiki\s*\n(.*?)(?=\n##\s|\Z)"
    match = re.search(pattern, body, re.MULTILINE | re.DOTALL)
    if not match:
        return None

    wiki_text = match.group(1).strip()
    if not wiki_text:
        return None

    result: dict[str, object] = {}
    current_key: str | None = None
    current_value: list[str] = []

    for line in wiki_text.split("\n"):
        kv_match = re.match(r"^(\w+):\s*(.*)", line)
        if kv_match:
            if current_key:
                result[current_key] = _finalize_wiki_value(current_key, current_value)
            current_key = kv_match.group(1)
            rest = kv_match.group(2).strip()
            if rest.startswith(">-"):
                current_value = []
            elif rest.startswith("- "):
                current_value = [rest[2:].strip()]
            else:
                current_value = [rest]
        elif current_key and line.strip().startswith("- "):
            current_value.append(line.strip()[2:].strip())
        elif current_key:
            current_value.append(line.strip())

    if current_key:
        result[current_key] = _finalize_wiki_value(current_key, current_value)

    return result if result else None


def _validate_wiki_section(wiki_data: dict) -> list[str]:
    """Validate wiki section fields. Ported from handoff/server.py.

    Args:
        wiki_data: Parsed wiki section dict.

    Returns:
        List of warning messages (empty if valid).
    """
    warnings: list[str] = []
    required = ["kind", "title", "path", "summary"]
    for field in required:
        if field not in wiki_data or not wiki_data[field]:
            warnings.append(f"Wiki section: missing required field '{field}'")

    if "kind" in wiki_data and wiki_data["kind"] not in _VALID_WIKI_KINDS:
        warnings.append(
            f"Wiki section: invalid kind '{wiki_data['kind']}'.  "
            f"Valid: {', '.join(sorted(_VALID_WIKI_KINDS))}"
        )

    if (
        "summary" in wiki_data
        and isinstance(wiki_data["summary"], str)
        and len(wiki_data["summary"]) > 300
    ):
        warnings.append(
            f"Wiki section: summary too long ({len(wiki_data['summary'])} chars, max 300)"
        )

    return warnings


def write_wiki_page(
    project_root: Path,
    wiki_data: dict,
    handoff_path: str,
) -> str | None:
    """Write a wiki page based on parsed wiki section data.

    Ported from ``tools/handoff/server.py``.

    Creates ``Library/Wiki/<path>.md`` with frontmatter + summary body.
    Updates wiki index and log.

    Args:
        project_root: Absolute path to project root.
        wiki_data: Parsed wiki section dict.
        handoff_path: Relative path of the source handoff file.

    Returns:
        Relative wiki page path, or ``None`` on failure.
    """
    wiki_rel = wiki_data.get("path", "")
    if not wiki_rel:
        return None

    wiki_path = project_root / "Library" / "Wiki" / f"{wiki_rel}.md"

    already_exists = wiki_path.exists()

    frontmatter: dict[str, object] = {
        "title": wiki_data.get("title", "Untitled"),
        "kind": wiki_data.get("kind", "concept"),
        "tags": wiki_data.get("tags", []),
        "source_handoff": handoff_path,
        "confidence": wiki_data.get("confidence", "CONFIRMED"),
    }
    frontmatter["date"] = date_type.today().isoformat()

    body_parts: list[str] = [wiki_data.get("summary", "")]
    sources = wiki_data.get("sources", [])
    if sources:
        body_parts.append("\n## Sources\n")
        for s in sources:
            body_parts.append(f"- [{s}]({s})")

    wiki_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_block = yaml.dump(
        frontmatter,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    content = f"---\n{yaml_block}\n---\n\n{''.join(body_parts)}\n"
    wiki_path.write_text(content, encoding="utf-8")

    if already_exists:
        logger.warning(f"Wiki page already exists (overwritten): {wiki_path}")

    # Update index and log
    _update_wiki_index(project_root, wiki_data, wiki_path)
    _update_wiki_log(project_root, wiki_data, wiki_path)

    try:
        wiki_rel_path = str(wiki_path.relative_to(project_root))
    except ValueError:
        wiki_rel_path = str(wiki_path)
    return wiki_rel_path


# ---------------------------------------------------------------------------
# Wiki index & log (ported from tools/handoff/server.py)
# ---------------------------------------------------------------------------


def _update_wiki_index(project_root: Path, wiki_data: dict, wiki_path: Path) -> None:
    """Append entry to ``Library/Wiki/index.md`` if not already present."""
    index_path = project_root / "Library" / "Wiki" / "index.md"
    if not index_path.exists():
        return

    summary = wiki_data.get("summary", "")
    try:
        wiki_rel = wiki_path.relative_to(project_root / "Library" / "Wiki")
    except ValueError:
        wiki_rel = wiki_path
    page_name = str(wiki_rel).replace(".md", "")
    today = date_type.today().isoformat()

    index_content = index_path.read_text(encoding="utf-8")
    if f"[[{page_name}]]" in index_content:
        return

    kind = wiki_data.get("kind", "concept")
    section_header = f"## {kind.capitalize()}s"
    section_header_alt = f"## {kind}s"

    new_entry = f"| [[{page_name}]] | {summary[:80]} | {today} |"

    if section_header in index_content:
        lines = index_content.split("\n")
        for i, line in enumerate(lines):
            if line.strip() == section_header:
                insert_pos = i + 1
                while (
                    insert_pos < len(lines)
                    and not lines[insert_pos].startswith("## ")
                    and insert_pos < i + 10
                ):
                    insert_pos += 1
                lines.insert(insert_pos, new_entry)
                index_path.write_text("\n".join(lines), encoding="utf-8")
                return
    elif section_header_alt in index_content:
        lines = index_content.split("\n")
        for i, line in enumerate(lines):
            if line.strip() == section_header_alt:
                insert_pos = i + 1
                while (
                    insert_pos < len(lines)
                    and not lines[insert_pos].startswith("## ")
                    and insert_pos < i + 10
                ):
                    insert_pos += 1
                lines.insert(insert_pos, new_entry)
                index_path.write_text("\n".join(lines), encoding="utf-8")
                return

    with index_path.open("a", encoding="utf-8") as f:
        table_hdr = "| Page | Summary | Updated |"
        table_sep = "|------|---------|---------|"
        f.write(f"\n{section_header}\n\n{table_hdr}\n{table_sep}\n{new_entry}\n")


def _update_wiki_log(project_root: Path, wiki_data: dict, wiki_path: Path) -> None:
    """Append entry to ``Library/Wiki/log.md``."""
    log_path = project_root / "Library" / "Wiki" / "log.md"
    if not log_path.exists():
        return

    title = wiki_data.get("title", "Untitled")
    kind = wiki_data.get("kind", "concept")
    try:
        wiki_rel = wiki_path.relative_to(project_root / "Library" / "Wiki")
    except ValueError:
        wiki_rel = wiki_path
    page_name = str(wiki_rel).replace(".md", "")
    today = date_type.today().isoformat()

    log_entry = f"- {today} | handoff→wiki | **{kind}**: [[{page_name}]] — {title[:60]}"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"\n{log_entry}")


# ---------------------------------------------------------------------------
# Orchestration: hf_new
# ---------------------------------------------------------------------------


def hf_new(
    store: Any,  # noqa: ANN401 — SynapsisStore
    project_root: Path,
    type: str,
    title: str,
    body: str,
    agent: str,
    tref: str | None = None,
    note: str | None = None,
    refs: str | None = None,
    devi: str | None = None,
    st: str = "done",
    prio: str = "med",
) -> dict[str, Any]:
    """Create a new handoff: generate ref, write file, index in DB, parse wiki.

    Args:
        store: ``SynapsisStore`` instance.
        project_root: Absolute project root path.
        type: Handoff type.
        title: Human-readable title.
        body: Markdown body.
        agent: Agent name.
        tref: Optional task reference.
        note: Optional completion notes.
        refs: Optional output refs.
        devi: Optional deviation block.
        st: Status (done, fail, hold, kill).
        prio: Priority (low, med, high, crit).

    Returns:
        Dict with ``ref``, ``file`` (relative path), ``wiki`` (or None), ``ts``.
    """
    # 1. Generate ref
    ref = generate_ref(store)
    now_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # 2. Build frontmatter (without hash — we don't know it yet)
    fm = build_frontmatter(
        ref=ref,
        type=type,
        title=title,
        agent=agent,
        tref=tref,
        st=st,
        prio=prio,
        note=note,
        refs=refs,
        devi=devi,
    )

    # 3. Write handoff file → now we know the path
    file_path_rel = write_handoff_file(project_root, fm, body, agent, type, title)

    # 4. Register → get hash, then inject into frontmatter
    hash_str: str | None = None
    try:
        hash_str = store.deliverable_register(file_path_rel)
        if hash_str:
            _inject_hash_into_file(project_root / file_path_rel, hash_str)
    except Exception as exc:
        logger.warning(f"Deliverable register failed (non-blocking): {exc}")

    # 6. Insert record into hf table
    #    NB: body is NEVER stored in DB — only the file reference.
    #    NB: hash is in deliverables table, not duplicated in hf table
    store.hf_insert(
        ref=ref,
        type=type,
        title=title,
        agent=agent,
        task=tref,
        st=st,
        prio=prio,
        sess=None,
        file=file_path_rel,
        wiki=None,
        ts=now_ts,
    )

    # 5. Parse Wiki section and write wiki page if present
    wiki_path_rel: str | None = None
    try:
        wiki_data = parse_wiki_section(body)
        if wiki_data:
            warnings = _validate_wiki_section(wiki_data)
            for w in warnings:
                logger.warning(w)

            wiki_page = write_wiki_page(project_root, wiki_data, file_path_rel)
            if wiki_page:
                wiki_path_rel = wiki_page
                # Update hf record with wiki path
                with store.transaction():
                    store._conn.execute(
                        "UPDATE hf SET wiki = ? WHERE ref = ?",
                        (wiki_path_rel, ref),
                    )
                logger.info(f"Wiki page created: {wiki_path_rel}")

                # Try to trigger chunk indexing immediately
                try:
                    from tools.knowledge_base import chunk_indexer

                    chunk_indexer.update(verbose=False)
                except Exception as exc:
                    logger.warning(f"Wiki chunk index update failed (non-fatal): {exc}")
    except Exception as exc:
        logger.error(f"Wiki section processing failed (non-blocking): {exc}")

    logger.info(f"Handoff created: ref={ref}, file={file_path_rel}, hash={hash_str}")

    # T-GH-001 escalation: trigger on handoff with non-"done" st or non-empty devi
    # (as required by .synapsis/escalation-policy.md)
    if (st and str(st).lower() in ("fail", "hold", "kill")) or (devi and str(devi).strip()):
        try:
            from tools.synapsis.report import report_problem

            esc_title = f"Handoff {ref} escalated (st={st})" if st and str(st).lower() != "done" else f"Handoff {ref} with deviation"
            esc_body = (
                f"**Handoff ref:** {ref}\n"
                f"**Status (st):** {st}\n"
                f"**tref:** {tref or 'N/A'}\n"
                f"**File:** {file_path_rel}\n\n"
            )
            if devi:
                esc_body += f"**Deviation (devi):**\n{devi}\n\n"
            if note:
                esc_body += f"**Note:** {note}\n\n"
            esc_body += (
                "This handoff was auto-escalated per escalation-policy.md "
                "(handoff created with devi or st in fail/hold/kill)."
            )

            report_problem(
                title=esc_title,
                body=esc_body,
                tref=tref,
                # sid not threaded to hf_new yet; caller can escalate explicitly if needed
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Auto-escalation on handoff failed (non-fatal): {exc}")

    return {
        "ref": ref,
        "hash": hash_str,
        "file": file_path_rel,
        "wiki": wiki_path_rel,
        "ts": now_ts,
    }


# ---------------------------------------------------------------------------
# Orchestration: hf_get
# ---------------------------------------------------------------------------


def hf_get(
    store: Any,  # noqa: ANN401 — SynapsisStore
    project_root: Path,
    ref: str,
    tk: int = 300,
    q: str | None = None,
) -> dict[str, Any]:
    """Read a handoff by ref, optionally compressed via Token Juice.

    If ``q`` is provided, uses FTS5 (knowledge_search) to extract the
    most relevant chunk for this specific file.

    Args:
        store: ``SynapsisStore`` instance.
        project_root: Absolute project root path.
        ref: Handoff ref (e.g. ``"hf-a3k9"``).
        tk: Token budget. ``0`` = full body, no compression.
            Default ``300``.
        q: Optional FTS5 query for targeted section extraction.

    Returns:
        Dict with ``ref``, ``body``, ``tk`` (requested), ``tk_actual``.
    """
    # 1. Query DB for file path
    record = store.hf_get(ref)
    if record is None:
        logger.error(f"Handoff ref '{ref}' not found in DB")
        return {"ref": ref, "body": "", "tk": tk, "tk_actual": 0, "error": f"Ref '{ref}' not found"}

    file_rel = record.get("file", "")
    if not file_rel:
        return {"ref": ref, "body": "", "tk": tk, "tk_actual": 0, "error": "No file path in record"}

    file_path = project_root / file_rel

    # 2. Read file content
    if not file_path.is_file():
        logger.error(f"Handoff file not found: {file_path}")
        return {
            "ref": ref,
            "body": "",
            "tk": tk,
            "tk_actual": 0,
            "error": f"File not found: {file_rel}",
        }

    raw_content = file_path.read_text(encoding="utf-8")

    # 3. Extract body (strip frontmatter between --- delimiters)
    body = _extract_body(raw_content)

    # 4. If q is present, try FTS5 chunk extraction
    if q and q.strip():
        try:
            chunk_body = _extract_relevant_chunk(store, body, file_rel, q.strip())
            if chunk_body is not None:
                body = chunk_body
        except Exception as exc:
            logger.warning(f"FTS5 chunk extraction failed (falling back to full body): {exc}")

    # 5. Compression
    if tk == 0:
        compressed_body = body
    else:
        try:
            from tools.token_juice import maybe_compress

            compressed_body = maybe_compress(body, threshold=tk, intensity="ultra")
        except Exception:
            compressed_body = body

    actual_tokens = len(compressed_body.split())

    return {
        "ref": ref,
        "body": compressed_body,
        "tk": tk,
        "tk_actual": actual_tokens,
    }


# ---------------------------------------------------------------------------
# Internal helpers for hf_get
# ---------------------------------------------------------------------------


def _inject_hash_into_file(file_path: Path, hash_str: str) -> None:
    """Inject ``hash: xxx`` into frontmatter of an existing handoff file.

    Reads the file, finds the YAML frontmatter between ``---`` delimiters,
    adds ``hash: <hash_str>``, and writes back.
    """
    content = file_path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return
    parts = content.split("---", 2)
    if len(parts) < 3:
        return
    frontmatter = parts[1].rstrip()
    # Don't inject if already present
    if "hash:" in frontmatter:
        return
    frontmatter += f"\nhash: {hash_str}\n"
    parts[1] = frontmatter
    file_path.write_text("---".join(parts), encoding="utf-8")


def _extract_body(raw_content: str) -> str:
    """Extract body content from a Markdown file with YAML frontmatter.

    Strips everything between the first ``---`` and second ``---``.
    """
    if not raw_content.startswith("---"):
        return raw_content
    parts = raw_content.split("---", 2)
    if len(parts) < 3:
        return raw_content
    return parts[2].strip()


def _extract_relevant_chunk(
    store: Any,  # noqa: ANN401
    body: str,
    file_rel: str,
    query: str,
) -> str | None:
    """Use FTS5 knowledge_search to find the most relevant chunk for this file.

    Args:
        store: SynapsisStore instance.
        body: Full body text (fallback).
        file_rel: Relative handoff file path.
        query: FTS5 search query.

    Returns:
        Relevant chunk content, or ``None`` to fall back.
    """
    results = store.knowledge_search(query, limit=5, mode="bm25")
    if not results:
        return None

    # Filter chunks matching this specific file
    matching = [r for r in results if r.get("file_path", "") == file_rel]
    if not matching:
        # Also try with Library/ prefix
        prefixed = f"Library/{file_rel}" if not file_rel.startswith("Library/") else file_rel
        matching = [r for r in results if r.get("file_path", "") == prefixed]

    if not matching:
        return None

    # Take top-ranked chunk
    top = matching[0]
    chunk_content = top.get("content", "")
    heading = top.get("heading_path", "")

    if heading:
        return f"## {heading}\n\n{chunk_content}"
    return chunk_content
