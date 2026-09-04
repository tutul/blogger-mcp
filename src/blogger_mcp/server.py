"""MCP server exposing the Blogger API v3.

Design notes that callers should be able to rely on:

* Every tool takes an explicit `blog_id`. The OAuth token decides *which
  account* the server acts as; `blog_id` decides *which blog* a call touches.
  The two are deliberately kept separate — nothing is defaulted or remembered.
* Writes are safe by default: `create_post`/`create_page` produce drafts, and
  deletes go to the trash unless `permanent=True` is passed explicitly.
* Blogger has no image upload endpoint. Content is stored as raw HTML and any
  <img> must already point at a publicly reachable absolute URL.
"""

from __future__ import annotations

from typing import Any, Literal

from googleapiclient.errors import HttpError
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from . import content as content_mod
from .auth import AuthError, get_service

mcp = MCPServer(
    "blogger",
    version=__version__,
    instructions=(
        "Manage Blogger blogs through the Blogger API v3. Every tool takes an "
        "explicit blog_id; use list_blogs to discover one. Writes are safe by "
        "default: create_post makes a draft and publishing is a separate "
        "publish_post call, and deletes go to the trash unless permanent=True. "
        "Blogger has no image upload endpoint, so every <img> in post content "
        "must already point at a public absolute URL."
    ),
)


def _ro() -> ToolAnnotations:
    return ToolAnnotations(read_only_hint=True, open_world_hint=True)


def _rw(destructive: bool, idempotent: bool = False) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=False,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=True,
    )


# Admin view is what surfaces drafts, the `status` field and customMetaData.
# It requires the authorized account to be an admin of the blog, which is the
# case for the blogs this server is meant to manage.
ADMIN = "ADMIN"

PostStatus = Literal["LIVE", "DRAFT", "SCHEDULED", "SOFT_TRASHED"]
ContentFormat = Literal["html", "text"]


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------


def _service():
    try:
        return get_service()
    except AuthError as exc:
        raise ToolError(str(exc)) from exc


def _execute(request) -> dict[str, Any]:
    """Run a googleapiclient request, turning API errors into readable ones."""
    try:
        return request.execute() or {}
    except HttpError as exc:
        status = exc.resp.status if exc.resp is not None else "?"
        detail = exc.reason or ""
        hint = ""
        if status == 403:
            hint = (
                " (the authorized account may not be an admin of this blog, or "
                "the Blogger API is not enabled for this Google Cloud project)"
            )
        elif status == 404:
            hint = " (check blog_id / post_id — a wrong blog_id also reads as 404)"
        elif status == 429:
            hint = " (Blogger API quota exceeded — check quotas in Cloud Console)"
        raise ToolError(f"Blogger API error {status}: {detail}{hint}") from exc


def _render(content: str, content_format: str) -> str:
    try:
        return content_mod.render(content, content_format)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def _post_summary(post: dict[str, Any]) -> dict[str, Any]:
    return {
        "post_id": post.get("id"),
        "title": post.get("title"),
        "status": post.get("status"),
        "url": post.get("url"),
        "labels": post.get("labels", []),
        "published": post.get("published"),
        "updated": post.get("updated"),
        "comment_count": (post.get("replies") or {}).get("totalItems"),
    }


def _post_detail(post: dict[str, Any]) -> dict[str, Any]:
    detail = _post_summary(post)
    detail["content"] = post.get("content")
    detail["sync_metadata"] = content_mod.sync_meta_of(post)
    if post.get("titleLink"):
        detail["title_link"] = post["titleLink"]
    if post.get("readerComments"):
        detail["reader_comments"] = post["readerComments"]
    return detail


def _page_summary(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_id": page.get("id"),
        "title": page.get("title"),
        "status": page.get("status"),
        "url": page.get("url"),
        "published": page.get("published"),
        "updated": page.get("updated"),
    }


def _listing(resp: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"count": len(items), "items": items}
    if resp.get("nextPageToken"):
        out["next_page_token"] = resp["nextPageToken"]
    return out


# --------------------------------------------------------------------------
# blogs (read-only — the Blogger API cannot create, rename or delete a blog)
# --------------------------------------------------------------------------


@mcp.tool(annotations=_ro())
def list_blogs() -> dict[str, Any]:
    """List the blogs the authorized Google account can access.

    Use this to discover a `blog_id` when you do not already have one. Every
    other tool requires the caller to pass `blog_id` explicitly.
    """
    resp = _execute(_service().blogs().listByUser(userId="self", view=ADMIN))
    items = [
        {
            "blog_id": b.get("id"),
            "name": b.get("name"),
            "url": b.get("url"),
            "description": b.get("description"),
            "post_count": (b.get("posts") or {}).get("totalItems"),
            "page_count": (b.get("pages") or {}).get("totalItems"),
        }
        for b in resp.get("items", [])
    ]
    return {"count": len(items), "items": items}


@mcp.tool(annotations=_ro())
def get_blog(blog_id: str | None = None, url: str | None = None) -> dict[str, Any]:
    """Look up one blog by its id, or by its public URL.

    Pass exactly one of `blog_id` or `url` (e.g. "https://example.blogspot.com/").
    """
    if bool(blog_id) == bool(url):
        raise ToolError("pass exactly one of blog_id or url")
    svc = _service().blogs()
    req = svc.getByUrl(url=url, view=ADMIN) if url else svc.get(blogId=blog_id, view=ADMIN)
    b = _execute(req)
    return {
        "blog_id": b.get("id"),
        "name": b.get("name"),
        "url": b.get("url"),
        "description": b.get("description"),
        "status": b.get("status"),
        "published": b.get("published"),
        "updated": b.get("updated"),
        "post_count": (b.get("posts") or {}).get("totalItems"),
        "page_count": (b.get("pages") or {}).get("totalItems"),
        "locale": b.get("locale"),
    }


# --------------------------------------------------------------------------
# posts
# --------------------------------------------------------------------------


@mcp.tool(annotations=_ro())
def list_posts(
    blog_id: str,
    status: PostStatus | None = None,
    labels: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    order_by: Literal["published", "updated"] = "published",
    sort: Literal["descending", "ascending"] = "descending",
    max_results: int = 20,
    page_token: str | None = None,
    fetch_bodies: bool = False,
) -> dict[str, Any]:
    """List posts in a blog.

    `status` filters to LIVE / DRAFT / SCHEDULED / SOFT_TRASHED (omit for all).
    `labels` is a comma-separated list. `start_date`/`end_date` are RFC 3339
    timestamps. Bodies are omitted by default — pass `fetch_bodies=True` only
    when you actually need the HTML, since post bodies are large.
    """
    params: dict[str, Any] = {
        "blogId": blog_id,
        "maxResults": max_results,
        "fetchBodies": fetch_bodies,
        "fetchImages": False,
        "orderBy": order_by,
        "sortOption": sort,
        "view": ADMIN,
    }
    if status:
        params["status"] = status
    if labels:
        params["labels"] = labels
    if start_date:
        params["startDate"] = start_date
    if end_date:
        params["endDate"] = end_date
    if page_token:
        params["pageToken"] = page_token

    resp = _execute(_service().posts().list(**params))
    build = _post_detail if fetch_bodies else _post_summary
    return _listing(resp, [build(p) for p in resp.get("items", [])])


@mcp.tool(annotations=_ro())
def get_post(blog_id: str, post_id: str) -> dict[str, Any]:
    """Fetch one post in full, including its HTML body and sync metadata.

    Always call this before `update_post` so you are editing against the
    current content rather than a stale copy.
    """
    post = _execute(
        _service().posts().get(
            blogId=blog_id, postId=post_id, view=ADMIN, fetchBody=True, maxComments=0
        )
    )
    return _post_detail(post)


@mcp.tool(annotations=_ro())
def search_posts(
    blog_id: str, query: str, fetch_bodies: bool = False
) -> dict[str, Any]:
    """Full-text search over the published posts of a blog.

    Note this only covers live posts; drafts are not indexed. To find a draft,
    use `list_posts` with `status="DRAFT"`.
    """
    resp = _execute(
        _service().posts().search(blogId=blog_id, q=query, fetchBodies=fetch_bodies)
    )
    build = _post_detail if fetch_bodies else _post_summary
    return _listing(resp, [build(p) for p in resp.get("items", [])])


@mcp.tool(annotations=_rw(destructive=False))
def create_post(
    blog_id: str,
    title: str,
    content: str,
    content_format: ContentFormat = "html",
    labels: list[str] | None = None,
    draft: bool = True,
    source_path: str | None = None,
    reader_comments: Literal[
        "ALLOW", "DONT_ALLOW_SHOW_EXISTING", "DONT_ALLOW_HIDE_EXISTING"
    ]
    | None = None,
) -> dict[str, Any]:
    """Create a post. Creates a DRAFT by default.

    Publishing is a separate, explicit step: call `publish_post` afterwards.
    Only set `draft=False` when a human has asked for immediate publication.

    `content_format="html"` sends `content` through untouched — Blogger stores
    post bodies as raw HTML. `content_format="text"` escapes the text and wraps
    it in paragraphs. There is no Markdown support and no image upload: any
    <img> in the HTML must already point at a public absolute URL.

    `source_path` records where the content came from in the post's
    customMetaData, so a later sync can tell whether the source has changed.
    """
    html_body = _render(content, content_format)
    body: dict[str, Any] = {
        "kind": "blogger#post",
        "title": title,
        "content": html_body,
        "customMetaData": content_mod.merge_sync_meta(None, html_body, source_path),
    }
    if labels:
        body["labels"] = labels
    if reader_comments:
        body["readerComments"] = reader_comments

    post = _execute(
        _service().posts().insert(
            blogId=blog_id, body=body, isDraft=draft, fetchBody=False, fetchImages=False
        )
    )
    result = _post_summary(post)
    result["is_draft"] = draft
    result["next_step"] = (
        "Draft created. Review it, then call publish_post to make it public."
        if draft
        else "Published."
    )
    return result


@mcp.tool(annotations=_rw(destructive=True, idempotent=True))
def update_post(
    blog_id: str,
    post_id: str,
    title: str | None = None,
    content: str | None = None,
    content_format: ContentFormat = "html",
    labels: list[str] | None = None,
    source_path: str | None = None,
    reader_comments: Literal[
        "ALLOW", "DONT_ALLOW_SHOW_EXISTING", "DONT_ALLOW_HIDE_EXISTING"
    ]
    | None = None,
    publish: bool = False,
) -> dict[str, Any]:
    """Update a post, sending only the fields you pass (PATCH semantics).

    Omitted fields are left untouched, so you can change just the title without
    resending the body. Two things to know:

    * `labels` REPLACES the whole label list. To add one label, read the current
      list with `get_post` first and pass the full new list.
    * `publish=False` (the default) does not unpublish anything — a live post
      stays live. It only means "do not publish a draft as part of this edit".
      Use `revert_post` to take a live post back to draft.
    """
    if title is None and content is None and labels is None and reader_comments is None:
        raise ToolError(
            "nothing to update: pass at least one of title, content, labels, "
            "reader_comments"
        )

    body: dict[str, Any] = {}
    if title is not None:
        body["title"] = title
    if labels is not None:
        body["labels"] = labels
    if reader_comments is not None:
        body["readerComments"] = reader_comments

    if content is not None:
        html_body = _render(content, content_format)
        body["content"] = html_body
        # Read the current post so unrelated customMetaData survives the write.
        existing = _execute(
            _service().posts().get(
                blogId=blog_id, postId=post_id, view=ADMIN, fetchBody=False
            )
        )
        body["customMetaData"] = content_mod.merge_sync_meta(
            existing.get("customMetaData"), html_body, source_path
        )

    post = _execute(
        _service().posts().patch(
            blogId=blog_id,
            postId=post_id,
            body=body,
            publish=publish,
            fetchBody=False,
            fetchImages=False,
            maxComments=0,
        )
    )
    result = _post_summary(post)
    result["updated_fields"] = sorted(body)
    return result


@mcp.tool(annotations=_rw(destructive=False, idempotent=True))
def publish_post(
    blog_id: str, post_id: str, publish_date: str | None = None
) -> dict[str, Any]:
    """Publish a draft post, making it publicly visible.

    This is the irreversible-feeling step — confirm with the human before
    calling it unless they already asked for publication.

    Pass `publish_date` as an RFC 3339 timestamp (e.g. "2026-09-10T09:00:00Z")
    to schedule publication for a future time instead of publishing now; the
    post then sits in SCHEDULED status until then.
    """
    params: dict[str, Any] = {"blogId": blog_id, "postId": post_id}
    if publish_date:
        params["publishDate"] = publish_date
    post = _execute(_service().posts().publish(**params))
    return _post_summary(post)


@mcp.tool(annotations=_rw(destructive=False, idempotent=True))
def revert_post(blog_id: str, post_id: str) -> dict[str, Any]:
    """Take a live or scheduled post back to DRAFT, removing it from the blog.

    The content is kept; only its visibility changes. This is the safe way to
    pull a post down.
    """
    post = _execute(_service().posts().revert(blogId=blog_id, postId=post_id))
    return _post_summary(post)


@mcp.tool(annotations=_rw(destructive=True, idempotent=True))
def delete_post(
    blog_id: str, post_id: str, permanent: bool = False
) -> dict[str, Any]:
    """Delete a post. Moves it to the trash by default, which is recoverable.

    `permanent=True` destroys the post with no way back — only pass it when a
    human has explicitly asked for permanent deletion. If the goal is just to
    take a post off the blog, prefer `revert_post`.
    """
    _execute(
        _service().posts().delete(
            blogId=blog_id, postId=post_id, useTrash=not permanent
        )
    )
    return {
        "post_id": post_id,
        "deleted": True,
        "permanent": permanent,
        "note": (
            "Permanently deleted."
            if permanent
            else "Moved to trash; recoverable from the Blogger UI or by listing "
            "posts with status='SOFT_TRASHED'."
        ),
    }


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------


@mcp.tool(annotations=_ro())
def list_pages(
    blog_id: str,
    status: Literal["LIVE", "DRAFT"] | None = None,
    fetch_bodies: bool = False,
    max_results: int = 20,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List the static pages of a blog (About, Contact, ...).

    Pages differ from posts: no labels, no dates in the archive, and they do
    not appear in the blog's post feed.
    """
    params: dict[str, Any] = {
        "blogId": blog_id,
        "maxResults": max_results,
        "fetchBodies": fetch_bodies,
        "view": ADMIN,
    }
    if status:
        params["status"] = status
    if page_token:
        params["pageToken"] = page_token
    resp = _execute(_service().pages().list(**params))
    items = []
    for p in resp.get("items", []):
        item = _page_summary(p)
        if fetch_bodies:
            item["content"] = p.get("content")
        items.append(item)
    return _listing(resp, items)


@mcp.tool(annotations=_ro())
def get_page(blog_id: str, page_id: str) -> dict[str, Any]:
    """Fetch one static page in full, including its HTML body."""
    page = _execute(_service().pages().get(blogId=blog_id, pageId=page_id, view=ADMIN))
    result = _page_summary(page)
    result["content"] = page.get("content")
    return result


@mcp.tool(annotations=_rw(destructive=False))
def create_page(
    blog_id: str,
    title: str,
    content: str,
    content_format: ContentFormat = "html",
    draft: bool = True,
) -> dict[str, Any]:
    """Create a static page. Creates a DRAFT by default.

    Same content rules as `create_post`: raw HTML or plain text, no Markdown,
    no image upload.
    """
    body = {
        "kind": "blogger#page",
        "title": title,
        "content": _render(content, content_format),
    }
    page = _execute(_service().pages().insert(blogId=blog_id, body=body, isDraft=draft))
    result = _page_summary(page)
    result["is_draft"] = draft
    return result


@mcp.tool(annotations=_rw(destructive=True, idempotent=True))
def update_page(
    blog_id: str,
    page_id: str,
    title: str | None = None,
    content: str | None = None,
    content_format: ContentFormat = "html",
    publish: bool = False,
) -> dict[str, Any]:
    """Update a static page, sending only the fields you pass (PATCH semantics).

    Set `publish=True` to publish a draft page as part of the edit.
    """
    if title is None and content is None:
        raise ToolError("nothing to update: pass title and/or content")
    body: dict[str, Any] = {}
    if title is not None:
        body["title"] = title
    if content is not None:
        body["content"] = _render(content, content_format)
    page = _execute(
        _service().pages().patch(
            blogId=blog_id, pageId=page_id, body=body, publish=publish
        )
    )
    result = _page_summary(page)
    result["updated_fields"] = sorted(body)
    return result


@mcp.tool(annotations=_rw(destructive=True, idempotent=True))
def delete_page(
    blog_id: str, page_id: str, permanent: bool = False
) -> dict[str, Any]:
    """Delete a static page. Moves it to the trash by default."""
    _execute(
        _service().pages().delete(blogId=blog_id, pageId=page_id, useTrash=not permanent)
    )
    return {"page_id": page_id, "deleted": True, "permanent": permanent}


# --------------------------------------------------------------------------
# comments (moderation only — the Blogger API cannot post a comment or reply)
# --------------------------------------------------------------------------


@mcp.tool(annotations=_ro())
def list_comments(
    blog_id: str,
    post_id: str | None = None,
    status: Literal["live", "emptied", "pending", "spam"] | None = None,
    max_results: int = 20,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List comments on one post, or across the whole blog if `post_id` is omitted.

    Use `status="pending"` to find comments waiting for moderation.
    """
    params: dict[str, Any] = {
        "blogId": blog_id,
        "maxResults": max_results,
        "fetchBodies": True,
    }
    if status:
        params["status"] = status
    if page_token:
        params["pageToken"] = page_token

    svc = _service().comments()
    if post_id:
        resp = _execute(svc.list(postId=post_id, view=ADMIN, **params))
    else:
        resp = _execute(svc.listByBlog(**params))

    items = [
        {
            "comment_id": c.get("id"),
            "post_id": (c.get("post") or {}).get("id"),
            "author": (c.get("author") or {}).get("displayName"),
            "content": c.get("content"),
            "status": c.get("status"),
            "published": c.get("published"),
            "in_reply_to": (c.get("inReplyTo") or {}).get("id"),
        }
        for c in resp.get("items", [])
    ]
    return _listing(resp, items)


@mcp.tool(annotations=_rw(destructive=True, idempotent=True))
def moderate_comment(
    blog_id: str,
    post_id: str,
    comment_id: str,
    action: Literal["approve", "spam", "remove_content", "delete"],
) -> dict[str, Any]:
    """Moderate one comment.

    * `approve` — publish a pending comment (also un-spams it)
    * `spam` — mark as spam and hide it
    * `remove_content` — blank the text but leave the "comment removed" stub
    * `delete` — remove it entirely; this one is NOT recoverable, so confirm
      with the human first

    The Blogger API cannot create comments, so replying to a commenter has to
    be done by hand in the Blogger UI.
    """
    svc = _service().comments()
    args = {"blogId": blog_id, "postId": post_id, "commentId": comment_id}
    if action == "approve":
        result = _execute(svc.approve(**args))
    elif action == "spam":
        result = _execute(svc.markAsSpam(**args))
    elif action == "remove_content":
        result = _execute(svc.removeContent(**args))
    elif action == "delete":
        _execute(svc.delete(**args))
        result = {}
    else:  # pragma: no cover - Literal already constrains this
        raise ToolError(f"unknown action {action!r}")
    return {
        "comment_id": comment_id,
        "action": action,
        "status": result.get("status", "deleted" if action == "delete" else None),
    }


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------


@mcp.tool(annotations=_ro())
def get_pageviews(
    blog_id: str, time_range: Literal["7DAYS", "30DAYS", "all"] = "30DAYS"
) -> dict[str, Any]:
    """Blog-level pageview counts.

    Blogger only exposes three coarse buckets and the numbers are approximate —
    treat them as a rough signal, not analytics. There is no per-post breakdown.
    """
    resp = _execute(
        _service().pageViews().get(blogId=blog_id, range=[time_range])
    )
    return {
        "blog_id": resp.get("blogId", blog_id),
        "counts": [
            {"range": c.get("timeRange"), "count": c.get("count")}
            for c in resp.get("counts", [])
        ],
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
