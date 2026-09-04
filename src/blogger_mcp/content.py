"""Content conversion and sync metadata.

Blogger stores post/page bodies as raw HTML strings. This module handles the
only conversion we support (plain text -> HTML) and the bookkeeping we keep in
the Post resource's `customMetaData` field, which Blogger treats as an opaque
JSON string and never renders on the page.
"""

from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone
from typing import Any

# Namespace inside customMetaData, so we never clobber metadata the blog owner
# (or another tool) put there.
META_KEY = "blogger_mcp"


def text_to_html(text: str) -> str:
    """Convert plain text to HTML.

    Escapes HTML-significant characters, turns blank-line-separated blocks into
    paragraphs, and single newlines inside a block into <br />.
    """
    blocks = [b for b in text.replace("\r\n", "\n").split("\n\n") if b.strip()]
    paragraphs = []
    for block in blocks:
        escaped = html.escape(block.strip(), quote=False)
        paragraphs.append("<p>" + escaped.replace("\n", "<br />\n") + "</p>")
    return "\n\n".join(paragraphs)


def render(content: str, content_format: str) -> str:
    """Render `content` to the HTML Blogger will store."""
    if content_format == "html":
        return content
    if content_format == "text":
        return text_to_html(content)
    raise ValueError(
        f"unknown content_format {content_format!r}; expected 'html' or 'text'"
    )


def content_hash(rendered_html: str) -> str:
    return hashlib.sha256(rendered_html.encode("utf-8")).hexdigest()


def parse_meta(raw: str | None) -> dict[str, Any]:
    """Parse a customMetaData string, tolerating garbage."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def merge_sync_meta(
    existing_raw: str | None,
    rendered_html: str,
    source_path: str | None = None,
) -> str:
    """Return an updated customMetaData string carrying our sync bookkeeping.

    Anything already in customMetaData outside our namespace is preserved, and
    a previously recorded source_path survives if the caller does not pass one.
    """
    meta = parse_meta(existing_raw)
    ours = meta.get(META_KEY) if isinstance(meta.get(META_KEY), dict) else {}
    entry = {
        "content_sha256": content_hash(rendered_html),
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = source_path or ours.get("source_path")
    if path:
        entry["source_path"] = path
    meta[META_KEY] = entry
    return json.dumps(meta, ensure_ascii=False)


def sync_meta_of(resource: dict[str, Any]) -> dict[str, Any]:
    """Extract just our sync bookkeeping from a Post/Blog resource."""
    return parse_meta(resource.get("customMetaData")).get(META_KEY, {}) or {}
