# blogger-mcp

An MCP server for [Blogger](https://www.blogger.com/), built on the Blogger
API v3. It lets an MCP client (Claude Code, Claude Desktop, ...) list, draft,
edit, publish and schedule Blogger posts and pages, and moderate comments.

## What it can and cannot do

The Blogger API v3 is the ceiling here, and it has real gaps. Worth knowing
before you build a workflow on top of this:

**Supported**

- Posts: list / get / search / create / update / publish / schedule / revert to
  draft / delete
- Pages (static pages like *About*): list / get / create / update / delete
- Comments: list and moderate (approve, mark spam, remove content, delete)
- Blogs: read only — list the account's blogs, look one up by id or URL
- Pageviews: coarse 7-day / 30-day / all-time counts

**Not supported — by the API itself, not by this server**

- **No image upload.** There is no media endpoint. Post bodies are HTML, and
  every `<img>` must already point at a publicly reachable absolute URL. Host
  images elsewhere first (Drive, GitHub, an image host) and paste in the URL.
- **No creating, renaming or deleting a blog.** The `blogs` resource is
  read-only.
- **No theme, template, layout, widget or blog-settings editing.**
- **No label management.** Labels are just strings on a post; renaming a label
  across a blog means patching every post that carries it.
- **No posting or replying to comments.** Moderation only.
- **No Markdown.** Content is raw HTML (this server can also wrap plain text).
- **No per-post metadata of your own.** The `customMetaData` field exists in
  the API schema and is documented as "The JSON meta-data for the Post", but
  Blogger discards writes to it — the value is already null in the insert
  response. There is no place to record which local file produced a post.
- **No recoverable trash.** `posts.delete` accepts `useTrash`, but a post
  deleted that way returns 404 on the next read and never appears under
  `status=SOFT_TRASHED`. Treat every delete as final and use `revert_post` when
  you only want a post off the public blog.

Several of these were found by running against a live blog; the reference
documentation does not mention them. See the git history for the isolation
tests.

## Setup

### 1. Google Cloud project (once, ~5 minutes)

1. Create a project at <https://console.cloud.google.com/>.
2. Enable the **Blogger API v3** for it
   (APIs & Services → Library → "Blogger API v3" → Enable).
3. Configure the OAuth consent screen:
   - User type **External**
   - Add the scope `https://www.googleapis.com/auth/blogger`
   - Add your own Google account as a **test user**
4. APIs & Services → Credentials → Create credentials → **OAuth client ID** →
   application type **Desktop app**. Download the JSON.
5. Save it as `~/.config/blogger-mcp/client_secret.json` (or point the
   `BLOGGER_MCP_CLIENT_SECRETS` environment variable at wherever you put it).

> **Important — do not leave the consent screen in "Testing".** While an app is
> in Testing status, Google expires its refresh tokens after **7 days**, so you
> would have to re-authorize every week. On the OAuth consent screen, click
> **Publish app** to move it to Production. An unverified app is fine for
> personal use: the consent screen shows a "Google hasn't verified this app"
> warning, and you continue via *Advanced → Go to (app name)*. Once in
> Production the refresh token does not expire.

### 2. Authorize (once)

```bash
blogger-mcp-auth
```

This opens a browser, asks you to sign in and consent, then caches the token at
`~/.config/blogger-mcp/token.json` (mode 0600). It finishes by printing the
blogs on the account together with their **blog ids** — you will need one of
those ids for every call.

Re-run it any time you need to re-authorize or switch account.

### 3. Register the server with your MCP client

Claude Code:

```bash
claude mcp add blogger -- /path/to/blogger/.venv/bin/blogger-mcp
```

Or in a client config file (Claude Desktop's `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "blogger": {
      "command": "/path/to/blogger/.venv/bin/blogger-mcp"
    }
  }
}
```

The server never opens a browser. If the cached token is missing or broken it
returns an error telling you to run `blogger-mcp-auth`.

## Install

```bash
uv venv
uv pip install -e .
```

## Configuration

All optional:

| Variable | Default | Purpose |
| --- | --- | --- |
| `BLOGGER_MCP_HOME` | `~/.config/blogger-mcp` | Directory for credentials |
| `BLOGGER_MCP_CLIENT_SECRETS` | `$BLOGGER_MCP_HOME/client_secret.json` | OAuth client JSON |
| `BLOGGER_MCP_TOKEN` | `$BLOGGER_MCP_HOME/token.json` | Cached token |
| `BLOGGER_MCP_READONLY` | unset | Set to `1` to request the read-only scope |

To manage two Google accounts, run two server instances with different
`BLOGGER_MCP_TOKEN` paths.

## Design decisions

**`blog_id` is always an explicit argument.** The OAuth token decides *which
account* the server acts as; `blog_id` decides *which blog* a call touches. The
server never defaults or remembers a blog — the caller is expected to know
which blog it is operating on. `list_blogs` exists to discover ids, not as a
required first step.

**Writes are safe by default.** `create_post` and `create_page` produce drafts.
Publishing is always a separate, explicit `publish_post` call. The intent is
that "organize a document" and "put it on the public internet" are never the
same action. Deleting is the one thing this server cannot make safe — Blogger's
trash is not reachable through the API — so `delete_post` is documented as
final and `revert_post` is the reversible way to take a post down.

**No `fetchBody` on writes.** `fetchBody` reads like a response-shaping option,
and it is on `posts.get`. On `posts.insert` and `posts.patch`, passing
`fetchBody=False` makes Blogger blank the post body. This server never sends it
on a write. Do not add it back.

## Agent guidance (the Skill)

`skills/blogger-publishing/SKILL.md` is an Agent Skill describing the intended
workflows: draft → review → publish, safely updating an existing post, syncing
local files, and what to do about the image limitation. The MCP server gives an
agent the *tools*; the Skill tells it *how to use them well*.

The Skill is only useful alongside the MCP server — install both in whichever
client you use.

**Claude Code** — copy or symlink the folder into your skills directory:

```bash
ln -s "$PWD/skills/blogger-publishing" ~/.claude/skills/blogger-publishing
```

**Claude Desktop / claude.ai** — build the ZIP and upload it:

```bash
./build-skill.sh          # writes dist/blogger-publishing.zip
```

Then in Claude: **Settings → Capabilities → enable code execution** (skills
require it), then **Customize → Skills → +** and upload
`dist/blogger-publishing.zip`.

The ZIP deliberately contains `blogger-publishing/` as its root entry, which is
the structure the uploader expects. Note that `description:` in the frontmatter
is capped at 200 characters — keep it under that if you edit it, or the upload
is rejected.

## License

MIT — see [LICENSE](LICENSE).
