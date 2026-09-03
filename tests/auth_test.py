# Copyright 2026 Atsushi Onozawa.
# Licensed under the Apache License, Version 2.0.

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from colab_mcp import auth


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes do not apply on Windows")
def test_atomic_token_write_uses_owner_only_permissions(tmp_path):
    token_path = tmp_path / "oauth-token.json"

    auth._write_token_atomically(token_path, '{"refresh_token":"test fixture"}')

    assert token_path.read_text(encoding="utf-8") == (
        '{"refresh_token":"test fixture"}'
    )
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [token_path]


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes do not apply on Windows")
def test_existing_token_permissions_are_restricted_before_read(tmp_path, monkeypatch):
    token_path = tmp_path / "oauth-token.json"
    token_path.write_text("{}", encoding="utf-8")
    token_path.chmod(0o644)
    credentials = Mock(valid=True)
    authorized_session = object()

    monkeypatch.setattr(auth, "TOKEN_CONFIG_PATH", str(token_path))
    load_credentials = Mock(return_value=credentials)
    monkeypatch.setattr(
        auth.Credentials, "from_authorized_user_file", load_credentials
    )
    monkeypatch.setattr(
        auth.requests, "AuthorizedSession", Mock(return_value=authorized_session)
    )

    result = auth.get_credentials("unused-client-config.json")

    assert result is authorized_session
    assert token_path.stat().st_mode & 0o777 == 0o600
    load_credentials.assert_called_once_with(Path(token_path), auth.SCOPES)
