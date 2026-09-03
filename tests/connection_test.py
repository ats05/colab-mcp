# Copyright 2026 Atsushi Onozawa.
# Licensed under the Apache License, Version 2.0.

from urllib.parse import parse_qs, parse_qsl, urlparse

import pytest

from colab_mcp.connection import (
    InvalidNotebookUrl,
    build_connection_info,
    normalize_notebook_url,
)


def test_empty_url_keeps_scratch_notebook_behavior():
    assert normalize_notebook_url("").endswith("/notebooks/empty.ipynb")


def test_existing_url_preserves_safe_query_and_replaces_connection_values():
    info = build_connection_info(
        "https://colab.research.google.com/drive/abc?authuser=2&p=old&token=old-secret",
        token="new-secret",
        port=4321,
        nonce="handoff-1",
    )
    parsed = urlparse(info.url)
    query = parse_qs(parsed.query)
    assert query["authuser"] == ["2"]
    assert query["p"] == ["4321"]
    assert query["mcpNonce"] == ["handoff-1"]
    assert "old-secret" not in info.url
    assert parse_qsl(parsed.fragment) == [
        ("mcpProxyToken", "new-secret"),
        ("mcpProxyPort", "4321"),
    ]


def test_notebook_url_rejects_non_colab_hosts():
    with pytest.raises(InvalidNotebookUrl):
        build_connection_info(
            "https://example.com/notebook.ipynb",
            token="secret",
            port=1,
        )


def test_notebook_url_rejects_malformed_authority():
    with pytest.raises(InvalidNotebookUrl):
        normalize_notebook_url("https://[not-a-host]/notebook.ipynb")
