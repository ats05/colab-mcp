# Copyright 2026 Google Inc.
# Modified by Sebastian Gil Pinzon and Atsushi Onozawa, 2026.
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

import argparse
import asyncio
import datetime
import ipaddress
import json
import logging
import os
import tempfile
import sys
import time
import webbrowser

from fastmcp import FastMCP
from fastmcp.utilities import logging as fastmcp_logger

from colab_mcp.connection import (
    InvalidNotebookUrl,
    build_connection_info,
    normalize_notebook_url,
)
from colab_mcp.execution import CodeExecutionRegistry
from colab_mcp.runtime import DirectRuntimeError, DirectRuntimeManager, RuntimeTarget
from colab_mcp.session import ColabSessionProxy, NOT_CONNECTED_MSG
from colab_mcp import process_registry


mcp = FastMCP(name="ColabMCP")

# These will be set during main_async() startup
_proxy_client = None
_session_mcp = None
_colab_client = None  # OAuth-backed runtime API
_direct_runtime_manager: DirectRuntimeManager | None = None
_process_profile = "default"
_last_notebook_url: str | None = None
_connection_nonce: str | None = None
_connection_attempt_url: str | None = None
_connection_attempt_started_at: float | None = None
_connection_open_lock = asyncio.Lock()
_process_entry = None
_execution_registry = CodeExecutionRegistry()
CONNECTION_FAILURE_GRACE_SECONDS = 60.0


def _mark_process_state(state: str) -> None:
    """Best-effort lifecycle update used to clean failed client restarts."""
    try:
        entry = _process_entry
        process_registry.mark_state(
            state,
            pid=os.getpid(),
            started_at=entry.started_at if entry is not None else None,
            instance_id=entry.instance_id if entry is not None else None,
        )
    except Exception as exc:  # pragma: no cover - diagnostics must not break tools
        logging.debug("Could not update process state to %s: %s", state, exc)


async def _forward_or_stub(tool_name: str, arguments: dict) -> str:
    """Forward a tool call to the browser if connected, otherwise return stub message."""
    if _proxy_client is not None and _proxy_client.is_connected():
        _mark_process_state("connected")
        try:
            result = await _proxy_client.proxy_mcp_client.call_tool(tool_name, arguments)
            # Extract text from result
            if hasattr(result, 'content'):
                return "\n".join(c.text for c in result.content if hasattr(c, 'text'))
            return str(result)
        except Exception as e:
            return f"Error calling {tool_name}: {e}. Try calling open_colab_browser_connection to reconnect."
    if (
        _connection_attempt_url is not None
        and _connection_attempt_started_at is not None
        and time.monotonic() - _connection_attempt_started_at
        >= CONNECTION_FAILURE_GRACE_SECONDS
    ):
        # A browser URL was issued and the full connection wait window has
        # elapsed without a browser connection. This avoids marking a process
        # failed during the normal manual-tab paste race.
        _mark_process_state("connection_failed")
    return NOT_CONNECTED_MSG


def _connection_info() -> dict[str, object] | None:
    """Return current local connection coordinates without logging secrets."""
    global _connection_nonce
    if _proxy_client is None or _proxy_client.wss is None:
        return None
    info = build_connection_info(
        _last_notebook_url,
        token=_proxy_client.wss.token,
        port=_proxy_client.wss.port,
        nonce=_connection_nonce,
    )
    _connection_nonce = info.nonce
    return info.as_dict()


@mcp.tool()
async def get_colab_connection_info() -> dict[str, object]:
    """Return current browser and direct-runtime connection information.

    This is a read-only diagnostic: it does not open a browser, wait for a
    connection, or reserve a new manual-handoff target. Use
    ``prepare_colab_browser_connection`` when an existing tab should receive
    fresh credentials.

    The token is returned only because the caller explicitly requested this
    tool. It is never written to logs, the process registry, or diagnostics.
    The ``token`` and ``port`` fields are diagnostic coordinates. For Colab's
    single manual connection field, use ``prepare_colab_browser_connection``
    so the returned ``TOKEN&PORT`` value can be pasted as-is.
    """
    info = _connection_info()
    if info is None and _direct_runtime_manager is None:
        return {
            "connected": False,
            "direct_runtime_connected": False,
            "error": "COLAB_MCP_NOT_INITIALIZED",
            "message": (
                "The local Colab MCP proxy is not initialized yet. "
                "Start the server with proxy support and retry."
            ),
        }
    if info is None:
        info = {}
    info["connected"] = bool(_proxy_client and _proxy_client.is_connected())
    info["direct_runtime_connected"] = bool(
        _direct_runtime_manager and _direct_runtime_manager.connected
    )
    if _direct_runtime_manager and _direct_runtime_manager.endpoint:
        info["runtime_endpoint"] = _direct_runtime_manager.endpoint
    if not info["connected"] and not info["direct_runtime_connected"]:
        info["message"] = (
            "No direct runtime or browser connection is active. With OAuth "
            "configured, call open_colab_browser_connection to attach to an "
            "active runtime. For an already-open tab, call "
            "prepare_colab_browser_connection; otherwise call "
            "open_colab_browser_connection to start the browser handoff."
        )
    return info


@mcp.tool()
async def prepare_colab_browser_connection() -> str:
    """Prepare a manual connection for an already-open Colab tab.

    This tool has no browser side effect and does not wait for a WebSocket
    connection. It returns only the single ``TOKEN&PORT`` value expected by
    Colab's MCP proxy-token field. It does not open or target a notebook URL.
    Once prepared, use the existing tab to connect; do not call the open tool
    again from a second client for the same daemon.
    """
    global _connection_attempt_url, _connection_attempt_started_at

    if _proxy_client is None or _proxy_client.wss is None:
        return (
            "COLAB_MCP_NOT_INITIALIZED: The local Colab MCP proxy is not "
            "initialized yet. Start the server with proxy support and retry."
        )

    async with _connection_open_lock:
        connection_token = f"{_proxy_client.wss.token}&{_proxy_client.wss.port}"
        if _proxy_client.is_connected():
            return connection_token

        # Never replace a handoff that another client has already prepared.
        # Return only the current credentials so the caller can use the same
        # tab without exposing a notebook URL.
        if _connection_attempt_url is not None:
            return connection_token

        # An empty marker reserves this process for the existing-tab handoff
        # without inventing or returning a notebook URL.
        _connection_attempt_url = ""
        _connection_attempt_started_at = time.monotonic()
        return connection_token


@mcp.tool()
async def open_colab_browser_connection(
    notebook_url: str = "",
    open_new_tab: bool = True,
    runtime_endpoint: str = "",
) -> str:
    """Connect to Colab, preferring a direct runtime when OAuth is available.

    ``notebook_url`` may be any existing HTTPS Google Colab notebook URL. If
    omitted, the historical ``empty.ipynb`` scratch notebook is opened. The
    current token and port are attached to a URL fragment. A non-secret
    port/nonce query identifies the initial URL; one MCP process opens at most
    one browser tab, so a shared daemon can serve multiple MCP clients through
    the same Colab tab. Set ``open_new_tab=False`` to prepare the URL without
    launching a browser, then paste it into an already-open target tab. When
    OAuth is configured, ``runtime_endpoint`` selects a direct runtime and no
    browser action is required; if omitted, a single active runtime is chosen.
    """
    if _proxy_client is not None and _proxy_client.is_connected():
        _mark_process_state("connected")
        if notebook_url:
            try:
                normalized = normalize_notebook_url(notebook_url)
            except InvalidNotebookUrl as exc:
                return f"Invalid notebook URL: {exc}"
            return (
                "Already connected to Colab. The requested notebook was not "
                f"opened because this server already has a live browser session "
                f"({normalized}). Use the existing tab and call get_cells to "
                "verify its state; use open_new_tab=false for an explicit "
                "same-tab endpoint change."
            )
        return "Already connected to Colab."

    if runtime_endpoint and _direct_runtime_manager is None:
        return (
            "Direct runtime_endpoint connection requires OAuth setup. Start the "
            "server with --client-oauth-config and retry."
        )

    if _direct_runtime_manager is not None:
        try:
            target = await _direct_runtime_manager.connect(runtime_endpoint)
            _mark_process_state("connected")
            return (
                "Connected directly to Colab runtime "
                f"{target.endpoint}; browser interaction is not required."
            )
        except DirectRuntimeError as exc:
            # An explicit endpoint means the caller requested the direct path;
            # do not silently open a browser for a typo or an ambiguous target.
            if runtime_endpoint:
                return f"Direct Colab runtime connection failed: {exc}"
            logging.info("Direct runtime unavailable; using browser bridge: %s", exc)

    if _proxy_client is None:
        return (
            "COLAB_MCP_NOT_INITIALIZED: The local Colab MCP proxy is not "
            "initialized. Please wait for the server to start and retry."
        )

    try:
        connection_info = build_connection_info(
            notebook_url,
            token=_proxy_client.wss.token,
            port=_proxy_client.wss.port,
        )
    except InvalidNotebookUrl as exc:
        return f"Invalid notebook URL: {exc}"

    global _connection_attempt_url, _connection_attempt_started_at
    global _connection_nonce, _last_notebook_url
    async with _connection_open_lock:
        # Recheck after another concurrent caller has opened the browser. This
        # lets multiple HTTP MCP clients share one daemon without opening
        # duplicate Colab tabs.
        if _proxy_client.is_connected():
            return "Already connected to Colab. Use the existing tab and call get_cells to verify its state."
        if _connection_attempt_url is not None:
            if (
                _connection_attempt_started_at is not None
                and time.monotonic() - _connection_attempt_started_at
                >= CONNECTION_FAILURE_GRACE_SECONDS
            ):
                _mark_process_state("connection_failed")
            if _connection_attempt_url == "":
                return (
                    "A manual connection has already been prepared for this "
                    "MCP process. Enter the returned TOKEN&PORT value in the "
                    "existing Colab tab; no second browser tab was opened."
                )
            return (
                "A browser connection has already been opened by this MCP "
                f"process for {_connection_attempt_url}. No second tab was "
                "opened. Reload that tab or call get_colab_connection_info "
                "to inspect the current connection coordinates."
            )

        if _connection_nonce is not None:
            connection_info = build_connection_info(
                notebook_url,
                token=_proxy_client.wss.token,
                port=_proxy_client.wss.port,
                nonce=_connection_nonce,
            )

        # Keep the token in the fragment. Do not log this URL: it contains a
        # bearer credential. The query string contains only a port and nonce.
        _connection_attempt_url = connection_info.notebook_url
        _connection_attempt_started_at = time.monotonic()
        _connection_nonce = connection_info.nonce
        _last_notebook_url = connection_info.notebook_url
        if not open_new_tab:
            return (
                "Connection URL prepared for the existing Colab tab; the "
                "browser was not opened. Paste this complete URL into that "
                "tab's address bar, then wait for it to connect:\n"
                f"{connection_info.url}\n"
                f"port={connection_info.port}\n"
                "WARNING: this response contains a bearer token. Do not log, "
                "share, or commit it."
            )
        webbrowser.open_new(connection_info.url)

    # Wait for browser to connect
    await _proxy_client.await_proxy_connection()

    if _proxy_client.is_connected():
        _mark_process_state("connected")
        tool_names = await _proxy_client.await_tools_ready()
        tools_text = ", ".join(tool_names) if tool_names else "none discovered"
        return (
            f"Connection successful for {connection_info.notebook_url}. "
            f"Available notebook tools: {tools_text}. You can now create, "
            "edit, and execute cells in the Colab notebook."
        )

    # Mark this instance so a client-launched retry can remove this failed
    # peer without terminating an active Claude/Codex session.
    _mark_process_state("connection_failed")

    # Timed out — surface diagnostic info about other running servers so the
    # user can recognize the "old browser tab pointed at a dead port" case.
    try:
        others = [
            e
            for e in process_registry.list_running(profile=_process_profile)
            if e.pid != os.getpid()
        ]
    except Exception:
        others = []
    my_port = _proxy_client.wss.port
    if others:
        peer_ports = ", ".join(f"{e.port} (pid {e.pid})" for e in others)
        return (
            f"Connection timed out. This server is on port {my_port}, but "
            f"{len(others)} other colab-mcp server(s) are also running: "
            f"{peer_ports}. If you have an old Colab tab open, it may be "
            "pointing at one of those instead of this server. Keep this "
            "daemon running and call prepare_colab_browser_connection, then "
            "paste its TOKEN&PORT value into the existing tab; use `--kill-stale` "
            "only as an explicit maintenance action."
        )
    return (
        f"Connection timed out. This server is on port {my_port}. Common causes:\n"
        "  1. Stale Colab tab(s) - reload the tab opened for this server; "
        "the current token/port are in get_colab_connection_info. Do not "
        "open a second tab from a concurrent MCP client.\n"
        "  2. Local Network Access permission denied - Chrome shows a prompt "
        "the first time Colab tries to reach localhost. Click 'Allow'. If you "
        "previously clicked 'Block', open colab.research.google.com -> site "
        "settings -> reset the 'Insecure content' / 'Other' permission and retry.\n"
        "  3. Browser tab was never opened - make sure your default browser "
        "is set and not blocking pop-ups for python.exe.\n"
        "  4. Existing-tab handoff - call prepare_colab_browser_connection. "
        "It returns one TOKEN&PORT value for Colab's single manual "
        "connection field without opening another tab."
    )


@mcp.tool()
async def add_code_cell(cellIndex: int, code: str = "", language: str = "python") -> str:
    """Add a code cell at ``cellIndex``; use ``-1`` to append.

    ``cellIndex`` is intentionally required so an omitted placement can never
    silently insert a cell at the beginning of an existing notebook.
    Requires an active browser connection via open_colab_browser_connection.
    """
    return await _forward_or_stub("add_code_cell", {"code": code, "cellIndex": cellIndex, "language": language})


@mcp.tool()
async def add_text_cell(cellIndex: int, content: str = "") -> str:
    """Add a Markdown cell at ``cellIndex``; use ``-1`` to append.

    ``cellIndex`` is intentionally required so an omitted placement can never
    silently change the order of an existing notebook. Requires an active
    browser connection via open_colab_browser_connection.
    """
    return await _forward_or_stub("add_text_cell", {"content": content, "cellIndex": cellIndex})


@mcp.tool()
async def get_cells() -> str:
    """Read the current notebook state: list of cells with their IDs, contents, and outputs. Essential for iterative work (write -> run -> read -> adjust). Requires an active browser connection via open_colab_browser_connection."""
    return await _forward_or_stub("get_cells", {})


@mcp.tool()
async def run_code_cell(cellId: str = "", code: str = "") -> dict[str, object]:
    """Start code in the background and return an execution ID.

    This is the default execution path, so the MCP client remains available
    while any cell runs. If a direct OAuth-backed runtime is connected, pass
    ``code`` and execution/output streaming happen without a browser. The
    ``cellId`` form remains available through the browser notebook bridge.
    """
    if not cellId and not code:
        return {
            "status": "failed",
            "error": "cellId or code is required to start a background code execution.",
        }
    if code and _direct_runtime_manager is not None and not _direct_runtime_manager.connected:
        try:
            await _direct_runtime_manager.connect()
        except DirectRuntimeError as exc:
            return {"status": "failed", "error": str(exc)}
    direct_runtime = (
        _direct_runtime_manager.runtime
        if _direct_runtime_manager and _direct_runtime_manager.connected
        else None
    )
    if direct_runtime is None and (_proxy_client is None or not _proxy_client.is_connected()):
        if code and _direct_runtime_manager is None:
            return {
                "status": "failed",
                "error": (
                    "Direct execution requires OAuth runtime setup. Start with "
                    "--client-oauth-config, then call open_colab_browser_connection "
                    "to attach to a runtime."
                ),
            }
        return {"status": "failed", "error": NOT_CONNECTED_MSG}
    if direct_runtime is not None and not code and (
        _proxy_client is None or not _proxy_client.is_connected()
    ):
        return {
            "status": "failed",
            "error": (
                "Direct runtime execution requires the code argument. The "
                "cellId form is provided by the optional browser notebook bridge."
            ),
        }

    async def runner(publish) -> str:
        if direct_runtime is not None and code:
            outputs = await direct_runtime.execute(code, publish)
            return json.dumps(outputs, ensure_ascii=False)

        result = await _forward_or_stub("run_code_cell", {"cellId": cellId})
        # Treat transport loss as a failed local execution so callers do not
        # mistake an error message for a completed cell result.
        if (
            result == NOT_CONNECTED_MSG
            or result.startswith("COLAB_NOT_CONNECTED:")
            or result.startswith("Error calling ")
        ):
            raise RuntimeError(result)
        publish({"output_type": "result", "text": result})
        return result

    try:
        return await _execution_registry.start(cellId or "<direct>", runner)
    except Exception as exc:  # pragma: no cover - defensive server boundary
        return {"status": "failed", "error": str(exc)}


@mcp.tool()
async def run_code_cell_blocking(cellId: str = "", code: str = "") -> str:
    """Execute a code cell and wait for its final browser-side result.

    Prefer run_code_cell for long-running work so the MCP client remains
    available while the cell executes. This blocking variant preserves the
    previous behavior for short cells and callers that need the result in the
    same tool response.
    """
    if code and _direct_runtime_manager is not None and not _direct_runtime_manager.connected:
        try:
            await _direct_runtime_manager.connect()
        except DirectRuntimeError as exc:
            return f"Direct Colab runtime connection failed: {exc}"
    if _direct_runtime_manager and _direct_runtime_manager.runtime and code:
        outputs = await _direct_runtime_manager.runtime.execute(code, lambda _event: None)
        return json.dumps(outputs, ensure_ascii=False)
    if _direct_runtime_manager and _direct_runtime_manager.connected and not code:
        return (
            "Direct runtime execution requires the code argument. The cellId "
            "form is provided by the optional browser notebook bridge."
        )
    return await _forward_or_stub("run_code_cell", {"cellId": cellId})


@mcp.tool()
async def get_code_execution(
    execution_id: str,
    cursor: int = 0,
    wait_seconds: float = 0.0,
) -> dict[str, object]:
    """Get status and incremental output for a background execution.

    ``cursor`` is the last received event ID. Set ``wait_seconds`` to a small
    positive value for long-polling when no new output has arrived yet.
    """
    return await _execution_registry.get_events(
        execution_id,
        cursor=cursor,
        wait_seconds=wait_seconds,
    )


@mcp.tool()
async def list_code_executions() -> list[dict[str, object]]:
    """List retained background code executions for this MCP process."""
    return _execution_registry.list()


@mcp.tool()
async def update_cell(cellId: str = "", content: str = "") -> str:
    """Update the contents of an existing cell in the Colab notebook. Requires an active browser connection via open_colab_browser_connection."""
    return await _forward_or_stub("update_cell", {"cellId": cellId, "content": content})


@mcp.tool()
async def delete_cell(cellId: str = "") -> str:
    """Delete a cell from the Colab notebook by cellId. Requires an active browser connection via open_colab_browser_connection."""
    return await _forward_or_stub("delete_cell", {"cellId": cellId})


@mcp.tool()
async def move_cell(cellId: str = "", cellIndex: int = 0) -> str:
    """Move a cell to a new position in the Colab notebook by cellId and target index. Requires an active browser connection via open_colab_browser_connection."""
    return await _forward_or_stub("move_cell", {"cellId": cellId, "cellIndex": cellIndex})


@mcp.tool()
async def change_runtime(accelerator: str = "T4") -> str:
    """Change the Colab runtime to use a specific GPU accelerator. Valid values: NONE, T4, L4, A100. Requires OAuth setup (first time opens browser for consent)."""
    if _colab_client is None:
        return "Runtime API not initialized. Start with --client-oauth-config flag pointing to your OAuth client secrets JSON."
    try:
        from colab_mcp.client import Accelerator, Variant
        import uuid

        acc = Accelerator(accelerator)
        variant = Variant.GPU if acc != Accelerator.NONE else Variant.DEFAULT
        notebook_hash = str(uuid.uuid4())

        # Unassign current VM if any
        try:
            assignments = _colab_client.list_assignments()
            for a in assignments:
                _colab_client.unassign(a.endpoint)
        except Exception:
            pass

        # Assign new VM
        result = _colab_client.assign(notebook_hash, variant, acc)
        if isinstance(result, dict):
            endpoint = result["assignment"].endpoint
        else:
            endpoint = result.endpoint
        if _direct_runtime_manager is not None:
            # The assignment response in older Colab API variants does not
            # always include runtimeProxyInfo. Refresh the assignment list to
            # obtain the current short-lived URL/token pair.
            assignments = await asyncio.to_thread(_colab_client.list_assignments)
            selected = next(
                (item for item in assignments if item.endpoint == endpoint), None
            )
            if selected is not None:
                await _direct_runtime_manager.attach_target(
                    RuntimeTarget(
                        endpoint=selected.endpoint,
                        url=selected.runtime_proxy_info.url,
                        token=selected.runtime_proxy_info.token,
                    )
                )
                return (
                    f"Runtime changed to {accelerator}. Directly connected to "
                    f"endpoint: {endpoint}. Browser interaction is not required."
                )
        return (
            f"Runtime changed to {accelerator}. Endpoint: {endpoint}. "
            "Use open_colab_browser_connection to connect to the new runtime."
        )
    except Exception as e:
        return f"Failed to change runtime: {e}"


def init_logger(logdir):
    log_filename = datetime.datetime.now().strftime(
        f"{logdir}/colab-mcp.%Y-%m-%d_%H-%M-%S.log"
    )
    logging.basicConfig(
        format="%(asctime)s %(levelname)s:%(message)s",
        datefmt="%m/%d/%Y %I:%M:%S %p",
        filename=log_filename,
        level=logging.INFO,
    )
    fastmcp_logger.get_logger("colab-mcp").info("logging to %s" % log_filename)


def parse_args(v):
    parser = argparse.ArgumentParser(
        description="ColabMCP is an MCP server that lets you interact with Colab."
    )
    parser.add_argument(
        "-l",
        "--log",
        help="if set, use this directory as a location for logfiles (if unset, will log to %s/colab-mcp-logs/)"
        % tempfile.gettempdir(),
        action="store",
        default=tempfile.mkdtemp(prefix="colab-mcp-logs-"),
    )
    parser.add_argument(
        "-p",
        "--enable-proxy",
        help="if set, enable the runtime proxy (enabled by default).",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--client-oauth-config",
        help=(
            "Path to OAuth client secrets JSON for direct runtime execution "
            "and the change_runtime tool."
        ),
        action="store",
        default=None,
    )
    parser.add_argument(
        "--list-running",
        help="List all currently-running colab-mcp servers and exit.",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--kill-stale",
        help="Explicitly terminate verified colab-mcp peers in this profile and exit. Does not run during normal startup.",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--stop-pid",
        type=int,
        default=None,
        metavar="PID",
        help="Explicitly stop one verified colab-mcp process in this profile and exit.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        default=False,
        help="Explicitly stop verified peers in this profile before starting.",
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("COLAB_MCP_PROFILE", "default"),
        help="Process group used by --replace/--kill-stale/--stop-pid (default: %(default)s).",
    )
    parser.add_argument(
        "--all-profiles",
        action="store_true",
        default=False,
        help="With an explicit stop/kill action, include every profile. Never implied by normal startup.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http", "sse"),
        default="stdio",
        help=(
            "MCP transport. stdio is the default for client-launched servers; "
            "use streamable-http for one shared local daemon."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP transport bind address (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--allow-insecure-non-loopback",
        action="store_true",
        default=False,
        help=(
            "Explicitly allow an unauthenticated HTTP transport to bind outside "
            "loopback. This can expose notebook tools and bearer credentials."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="PORT",
        help="HTTP transport port (FastMCP default when omitted); ignored for stdio.",
    )
    parser.add_argument(
        "--path",
        default=None,
        metavar="PATH",
        help="HTTP transport endpoint path (FastMCP default /mcp when omitted).",
    )
    args = parser.parse_args(v)
    if (
        args.transport != "stdio"
        and not _is_loopback_host(args.host)
        and not args.allow_insecure_non_loopback
    ):
        parser.error(
            "non-loopback HTTP binding is unauthenticated and disabled by default; "
            "use --host 127.0.0.1 or explicitly acknowledge the risk with "
            "--allow-insecure-non-loopback"
        )
    return args


def _is_loopback_host(host: str) -> bool:
    """Return whether an HTTP bind host is unambiguously loopback-only."""
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _print_running_servers() -> None:
    entries = process_registry.list_running(profile=_process_profile)
    if not entries:
        print("No colab-mcp servers currently registered as running.")
        return
    print(f"Found {len(entries)} running colab-mcp server(s):")
    import datetime as _dt
    for e in entries:
        started = _dt.datetime.fromtimestamp(e.started_at).strftime("%Y-%m-%d %H:%M:%S")
        port = str(e.port) if e.port else "?"
        host = e.host or "unknown"
        print(
            f"  pid={e.pid:<6}  port={port:<6}  host={host}  "
            f"profile={e.profile}  started={started}"
        )


async def _run_mcp_transport(args) -> None:
    """Run the configured MCP transport without changing stdio defaults."""
    if args.transport == "stdio":
        await mcp.run_async()
        return

    kwargs = {
        "transport": args.transport,
        "host": args.host,
    }
    if args.port is not None:
        kwargs["port"] = args.port
    if args.path is not None:
        kwargs["path"] = args.path
    await mcp.run_async(**kwargs)


async def main_async():
    global _proxy_client, _session_mcp, _colab_client, _direct_runtime_manager
    global _process_profile, _process_entry
    args = parse_args(sys.argv[1:])
    _process_profile = args.profile
    init_logger(args.log)

    # Diagnostic / cleanup flags exit early.
    if args.list_running:
        _print_running_servers()
        return
    if args.stop_pid is not None:
        stopped = process_registry.stop(
            args.stop_pid,
            profile=None if args.all_profiles else args.profile,
        )
        if stopped is None:
            print(
                f"Refused to stop pid={args.stop_pid}: it is not a verified "
                f"colab-mcp server in profile '{args.profile}'."
            )
        else:
            print(f"Stopped colab-mcp pid={stopped.pid} port={stopped.port or '?'}.")
        return
    if args.kill_stale:
        removed = process_registry.cleanup_stale(
            kill=True,
            profile=None if args.all_profiles else args.profile,
        )
        if not removed:
            print("No stale colab-mcp servers found.")
        else:
            print(f"Terminated {len(removed)} stale colab-mcp server(s):")
            for e in removed:
                print(f"  pid={e.pid} port={e.port or '?'} profile={e.profile}")
        return

    # Normal startup never kills a live peer. Replacement is explicit and
    # profile-scoped, so Claude and Codex can coexist without killing one
    # another accidentally.
    if args.replace:
        replaced = process_registry.replace_existing(profile=args.profile)
        logging.info(
            "Explicitly replaced %d verified colab-mcp peer(s) in profile=%s",
            len(replaced),
            args.profile,
        )

    # Prune dead/PID-reused entries before binding a port. This does not signal
    # any process and is safe for normal startup.
    dead = process_registry.prune_dead()
    if dead:
        logging.info(f"Pruned {dead} stale entries from process registry")

    failed = process_registry.cleanup_unhealthy(
        profile=args.profile,
        transport=args.transport,
    )
    if failed:
        logging.info(
            "Removed %d failed %s peer(s) in profile=%s",
            len(failed),
            args.transport,
            args.profile,
        )

    if args.enable_proxy:
        logging.info("enabling session proxy tools")
        _session_mcp = ColabSessionProxy()
        await _session_mcp.start_proxy_server()
        _proxy_client = _session_mcp.proxy_client
        # Register ourselves now that we know the port.
        try:
            _process_entry = process_registry.register(
                port=_session_mcp.wss.port,
                host=_session_mcp.wss.host,
                profile=args.profile,
            )
            logging.info(
                "Registered colab-mcp pid=%s port=%s profile=%s",
                _process_entry.pid,
                _process_entry.port,
                _process_entry.profile,
            )
        except Exception as exc:
            logging.warning(f"Could not register process: {exc}")

    if args.client_oauth_config:
        try:
            from colab_mcp.auth import get_credentials
            from colab_mcp.client import ColabClient, Prod
            logging.info("initializing Colab API client with OAuth")
            session = get_credentials(args.client_oauth_config)
            _colab_client = ColabClient(Prod(), session)
            _direct_runtime_manager = DirectRuntimeManager(_colab_client)
            logging.info("Colab API client ready")
        except Exception as e:
            logging.warning(f"Failed to initialize Colab API client: {e}")

    try:
        await _run_mcp_transport(args)

    finally:
        await _execution_registry.close()
        if _direct_runtime_manager is not None:
            await _direct_runtime_manager.close()
        if args.enable_proxy and _session_mcp:
            await _session_mcp.cleanup()
        # Always unregister so a clean shutdown doesn't leave a stale entry.
        try:
            if _process_entry is None:
                process_registry.unregister()
            else:
                process_registry.unregister(
                    started_at=_process_entry.started_at,
                    instance_id=_process_entry.instance_id,
                )
        except Exception as exc:
            logging.warning(f"Could not unregister process: {exc}")


def main() -> None:
    asyncio.run(main_async())
