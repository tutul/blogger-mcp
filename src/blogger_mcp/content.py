"""Content conversion.

Blogger stores post and page bodies as raw HTML strings. The only conversion
this server performs is plain text -> HTML; there is no Markdown support.

Note there is deliberately no sync-metadata helper here. Blogger's Post
resource has a `customMetaData` field documented as "The JSON meta-data for the
Post", but the API silently discards writes to it — the value comes back as
null in the insert response itself. Tracking which local file produced which
post has to happen outside Blogger.
"""

from __future__ import annotations

import html


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
