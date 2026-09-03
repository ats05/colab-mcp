# Copyright 2026 Google Inc.
# Added to this fork by Sebastian Gil Pinzon, 2026.
# Modified by Atsushi Onozawa, 2026.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import os
from pathlib import Path
import tempfile
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.auth.transport import requests

OAUTH_SERVER_PORT = 8085

SCOPES = [
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/colaboratory",
    "openid",
]

TOKEN_CONFIG_PATH = os.path.expanduser("~/.colab-mcp-auth-token.json")


def _ensure_private_permissions(token_path: Path) -> None:
    """Restrict an existing token file to its owner on POSIX systems."""
    if os.name != "nt" and token_path.exists():
        token_path.chmod(0o600)


def _write_token_atomically(token_path: Path, contents: str) -> None:
    """Atomically replace the OAuth token cache with owner-only permissions."""
    token_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{token_path.name}.", dir=token_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
            descriptor = -1
            token_file.write(contents)
            token_file.flush()
            os.fsync(token_file.fileno())
        os.replace(temporary_path, token_path)
        _ensure_private_permissions(token_path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)
        raise


def get_credentials(config):
    creds = None
    token_path = Path(TOKEN_CONFIG_PATH)
    if token_path.exists():
        _ensure_private_permissions(token_path)
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(config, SCOPES)
            creds = flow.run_local_server(port=OAUTH_SERVER_PORT)

        _write_token_atomically(token_path, creds.to_json())

    return requests.AuthorizedSession(creds)
