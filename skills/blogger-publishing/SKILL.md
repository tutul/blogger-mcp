---
name: blogger-publishing
description: Publish and maintain Blogger posts and pages via the blogger MCP server. Use when turning documents into Blogger posts, or updating, scheduling, unpublishing, syncing or moderating them.
---

# Blogger publishing

Workflows for the `blogger` MCP server, which wraps the Blogger API v3.

## Know the shape of the data

```
Google account (fixed by the server's OAuth token)
└── Blog          the site itself — has a blog_id; READ-ONLY via the API
    ├── Post      a timeline article: title, HTML body, labels, publish date
    ├── Page      a static page (About, Contact): no labels, not in the feed
    └── Comment   attached to a post; can be moderated but not answered
```

Every tool needs an explicit `blog_id`. The token says *who* you are; `blog_id`
says *which blog*. Nothing is defaulted — if you do not have a `blog_id`, call
`list_blogs` and, when the account has more than one blog, ask the user which
one before writing anything.

## Publishing something new

Never create and publish in one motion. The two steps are separate on purpose.

1. **Check the content for local image paths first** — see below. Fix this
   before creating anything.
2. `create_post(blog_id, title, content, content_format=..., labels=[...])` —
   this creates a **draft**. Leave `draft=True`.
3. Report back the post id and title, and tell the user it is a draft they can
   preview in the Blogger UI.
4. Wait for the user to say go.
5. `publish_post(blog_id, post_id)` — or pass `publish_date` (RFC 3339, e.g.
   `"2026-09-10T09:00:00Z"`) to schedule it instead.

Only pass `draft=False` to `create_post` when the user has explicitly asked for
immediate publication.

## Content rules

`content_format="html"` (default) sends the string through untouched — Blogger
stores post bodies as raw HTML. `content_format="text"` escapes the text and
wraps blank-line-separated blocks in `<p>`.

**There is no Markdown support.** If the user hands you Markdown, convert it to
HTML yourself and send it as `html`; do not send Markdown source, it will
appear literally on the page.

**There is no image upload.** The Blogger API has no media endpoint at all.
Before creating or updating a post, scan the HTML for `<img src=...>` values
that are not absolute `http(s)` URLs — relative paths (`./img/a.png`,
`images/x.jpg`), `file://` URLs and bare filenames all produce a broken image
on the live blog. When you find any:

> Stop. Tell the user which images need hosting, and that they must upload
> those images somewhere public (Google Drive with a public link, GitHub, an
> image host) and give you the URLs.

Do not silently strip the images, and do not invent URLs.

## Updating an existing post

1. `get_post(blog_id, post_id)` — always read the current state first, so you
   are editing against what is actually live.
2. `update_post(...)` with **only the fields that change**. It uses PATCH
   semantics, so omitted fields are left alone; there is no need to resend the
   body just to fix a title.

Two traps:

* **`labels` replaces the entire list.** To add one label, take the list from
  `get_post`, append, and send the full list.
* **`publish=False` does not unpublish.** A live post stays live through an
  edit. To take a post off the blog use `revert_post`, which returns it to
  draft with its content intact.

## Removing a post

Prefer `revert_post` — it takes the post off the public blog but keeps
everything, and is trivially undone by publishing again.

Use `delete_post` only when the user actually wants it gone, and confirm first.
Whether it can be undone depends on the post's state, and the result's
`outcome` tells you which happened:

* **`trashed`** — a published or scheduled post went to the trash.
  `revert_post` brings it back as a draft with its content.
* **`deleted`** — gone for good. This is what happens to a **draft**, to
  anything deleted with `permanent=True`, and to a post that was **already in
  the trash**.

Never retry a `delete_post` to be safe: a second delete of a trashed post
destroys it. Pages behave the same, but this server has no tool to restore a
page — treat a page delete as final.

## There is no way to link a post back to a local file

Blogger has no usable per-post metadata field — `customMetaData` exists in the
API but silently discards writes. So there is no marker on a post saying which
document it came from. To find the post for a file, `list_posts` and match on
the title, or keep your own mapping outside Blogger. Do not promise the user
that re-running a sync will detect what changed.

## Drafts are not searchable

`search_posts` only covers published posts. To find a draft, use
`list_posts(blog_id, status="DRAFT")` and match on the title.

## Comments

`list_comments(blog_id, status="PENDING")` finds anything awaiting moderation.
`moderate_comment` does `approve` / `spam` / `remove_content` / `delete`;
`delete` is not recoverable, so confirm it first.

**The API cannot post or reply to comments.** If the user wants to answer a
commenter, say so plainly — they have to do it in the Blogger UI.

## Things the API simply cannot do

Do not attempt workarounds for these; say they are not possible:

- Upload images or any other media
- Create, rename or delete a blog
- Change the theme, template, layout, widgets or blog settings
- Rename a label across the blog (only by patching each post individually)
- Post or reply to comments
- Per-post view counts (`get_pageviews` is blog-wide and approximate)
- Store any metadata of your own on a post
- Recover a deleted draft, or anything deleted with `permanent=True`
- Restore a page from the trash (posts can be: `revert_post`)
