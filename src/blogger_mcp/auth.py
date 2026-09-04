"""OAuth 2.0 handling for the Blogger MCP server.

The interactive consent flow needs a browser, so it can never run inside the
stdio server (it would block the transport). Run `blogger-mcp-auth` once to
mint a refresh token; the server itself only ever loads and silently refreshes
the cached token.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Blogger v3 only offers these two scopes; there is no finer-grained option.
SCOPES = ["https://www.googleapis.com/auth/blogger"]
READONLY_SCOPES = ["https://www.googleapis.com/auth/blogger.readonly"]


def config_dir() -> Path:
    return Path(
        os.environ.get("BLOGGER_MCP_HOME", Path.home() / ".config" / "blogger-mcp")
    ).expanduser()


def token_path() -> Path:
    return Path(
        os.environ.get("BLOGGER_MCP_TOKEN", config_dir() / "token.json")
    ).expanduser()


def client_secrets_path() -> Path:
    return Path(
        os.environ.get(
            "BLOGGER_MCP_CLIENT_SECRETS", config_dir() / "client_secret.json"
        )
    ).expanduser()


def _scopes() -> list[str]:
    if os.environ.get("BLOGGER_MCP_READONLY", "").lower() in ("1", "true", "yes"):
        return READONLY_SCOPES
    return SCOPES


class AuthError(RuntimeError):
    pass


def load_credentials() -> Credentials:
    """Load the cached token, refreshing it if expired. Never opens a browser."""
    path = token_path()
    if not path.exists():
        raise AuthError(
            f"No cached credentials at {path}. Run `blogger-mcp-auth` once to "
            "authorize this app against your Google account."
        )
    creds = Credentials.from_authorized_user_file(str(path), _scopes())
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save(creds)
        return creds
    raise AuthError(
        f"Cached credentials at {path} are invalid and cannot be refreshed. "
        "Re-run `blogger-mcp-auth`."
    )


def _save(creds: Credentials) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())
    path.chmod(0o600)


def get_service():
    """Build an authorized Blogger v3 client."""
    return build("blogger", "v3", credentials=load_credentials(), cache_discovery=False)


def authorize() -> Path:
    """Run the interactive consent flow and cache the resulting token."""
    secrets = client_secrets_path()
    if not secrets.exists():
        raise AuthError(
            f"Client secrets file not found at {secrets}.\n"
            "Create an OAuth client (type: Desktop app) in Google Cloud Console, "
            "download the JSON, and put it there — or point "
            "BLOGGER_MCP_CLIENT_SECRETS at it."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), _scopes())
    creds = flow.run_local_server(port=0, prompt="consent")
    _save(creds)
    return token_path()


def main() -> int:
    try:
        path = authorize()
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Authorized. Token cached at {path}")
    try:
        service = get_service()
        blogs = service.blogs().listByUser(userId="self").execute().get("items", [])
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        print(f"warning: token saved but a test call failed: {exc}", file=sys.stderr)
        return 0
    if not blogs:
        print("No blogs found on this account.")
    else:
        print("Blogs on this account:")
        for blog in blogs:
            print(f"  {blog['id']}  {blog.get('name')}  {blog.get('url')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
