# Copyright 2026 Sebastian Gil (fork).
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Helpers for constructing safe Colab browser connection URLs.

The MCP token is intentionally kept in the URL fragment.  Fragments are not
sent in HTTP requests, so the token does not reach a proxy or a web server.
The query string contains only non-secret cache-busting values.  Keeping this
logic in one module is important: the direct FastMCP tools and the legacy
session-proxy stubs must produce exactly the same URL.
"""

from __future__ import annotations

from dataclasses import dataclass
import secrets
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from colab_mcp.websocket_server import COLAB, COLAB_ALT_DOMAIN, SCRATCH_PATH


class InvalidNotebookUrl(ValueError):
    """Raised when a URL is not a supported Google Colab notebook URL."""


_ALLOWED_HOSTS = {
    urlparse(COLAB).hostname,
    urlparse(COLAB_ALT_DOMAIN).hostname,
}
_RESERVED_QUERY_KEYS = {
    "p",
    "mcpport",
    "mcpnonce",
    "mcpproxyport",
    "mcpproxytoken",
}


def _is_sensitive_query_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _RESERVED_QUERY_KEYS or lowered in {
        "token",
        "access_token",
        "authorization",
    }


@dataclass(frozen=True)
class ColabConnectionInfo:
    """Connection coordinates for browser or manual Colab handoff."""

    notebook_url: str
    url: str
    fragment: str
    token: str
    port: int
    nonce: str

    def as_dict(self) -> dict[str, object]:
        # Keep token and port as separate fields.  This is useful when the
        # Colab UI asks for them separately, while ``url`` is convenient for
        # an agent or a browser handoff.
        return {
            "notebook_url": self.notebook_url,
            "url": self.url,
            "fragment": self.fragment,
            "token": self.token,
            "port": self.port,
            "nonce": self.nonce,
            "paste_ready": {
                "token": self.token,
                "port": self.port,
            },
        }


def normalize_notebook_url(notebook_url: str | None) -> str:
    """Validate and normalize a user-provided Colab notebook URL.

    An empty value retains the historical ``empty.ipynb`` behavior.  Only
    HTTPS URLs on the two Colab hostnames are accepted; this prevents an MCP
    token from accidentally being attached to an arbitrary site.
    """

    if notebook_url is None or not notebook_url.strip():
        return f"{COLAB}{SCRATCH_PATH}"

    value = notebook_url.strip()
    try:
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
    except ValueError as exc:
        raise InvalidNotebookUrl("notebook_url is malformed.") from exc
    if parsed.scheme.lower() != "https" or hostname not in _ALLOWED_HOSTS:
        raise InvalidNotebookUrl(
            "notebook_url must be an HTTPS Google Colab URL "
            "(https://colab.research.google.com/... or "
            "https://colab.google.com/...)."
        )
    if parsed.username is not None or parsed.password is not None:
        raise InvalidNotebookUrl("notebook_url must not contain user credentials.")

    # A bare Colab origin is not a notebook.  Treat it like the old default so
    # callers can pass a value copied from the browser without a path.
    path = parsed.path or SCRATCH_PATH
    if path == "/":
        path = SCRATCH_PATH
    safe_query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not _is_sensitive_query_key(key)
        ],
        doseq=True,
    )
    return urlunparse(("https", hostname, path, parsed.params, safe_query, ""))


def build_connection_info(
    notebook_url: str | None,
    *,
    token: str,
    port: int,
    nonce: str | None = None,
) -> ColabConnectionInfo:
    """Build a URL with current MCP credentials and non-secret cache busting.

    Existing notebook query parameters are retained.  The reserved ``p`` and
    MCP nonce keys are replaced so a copied URL can never keep an old port.
    The fragment is replaced wholesale, which prevents a stale
    ``mcpProxyToken``/``mcpProxyPort`` pair from winning over the current one
    in a cached Colab tab.
    """

    base = normalize_notebook_url(notebook_url)
    parsed = urlparse(base)
    current_nonce = nonce or secrets.token_urlsafe(8)

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_sensitive_query_key(key)
    ]
    # ``p`` remains for compatibility with this fork's stale-tab fix.  The
    # nonce makes two calls for the same port unique as well.
    query_pairs.extend((("p", str(port)), ("mcpNonce", current_nonce)))
    query = urlencode(query_pairs, doseq=True)
    fragment = urlencode(
        (("mcpProxyToken", token), ("mcpProxyPort", str(port))),
    )
    url = urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, fragment)
    )
    return ColabConnectionInfo(
        notebook_url=base,
        url=url,
        fragment=fragment,
        token=token,
        port=int(port),
        nonce=current_nonce,
    )
