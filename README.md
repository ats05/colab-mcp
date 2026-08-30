# Colab MCP (Enhanced Fork)

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)
[![MCP](https://img.shields.io/badge/protocol-MCP-purple.svg)](https://modelcontextprotocol.io)
[![Stars](https://img.shields.io/github/stars/ats05/colab-mcp?style=social)](https://github.com/ats05/colab-mcp)

An MCP server for controlling Google Colab from any AI coding agent. This fork fixes the bugs in the [official repo](https://github.com/googlecolab/colab-mcp) that block real day-to-day use and restores features Google removed upstream.

## Why This Fork?

Three concrete dolores that the official `googlecolab/colab-mcp` doesn't solve — and that this fork does:

1. **Invisible tools** ([#54](https://github.com/googlecolab/colab-mcp/discussions/54), [#67](https://github.com/googlecolab/colab-mcp/discussions/67), [#69](https://github.com/googlecolab/colab-mcp/discussions/69)) — the upstream server only advertises `open_colab_browser_connection` until a browser connects. This fork registers the complete surface up front, so Claude Code and Codex can discover and call notebook tools without relying on `notifications/tools/list_changed`.
2. **"Disconnected from the local Colab MCP server"** ([#84](https://github.com/googlecolab/colab-mcp/discussions/84)) — orphaned servers from prior Claude Code sessions hold ports that your browser tab still points at. Reconnecting from the tab silently fails.
3. **No programmatic GPU control** — Google [removed](https://github.com/googlecolab/colab-mcp/discussions/41) the `--enable-runtime` feature entirely. You can't assign T4 / L4 / A100 without clicking in the browser.

This fork fixes all three. All tools are registered at process startup (the
client does not need `notifications/tools/list_changed`), stale servers are
diagnosed from both the registry and the OS process table, and GPUs are
assignable from a single tool call.

> _Demo coming soon: `docs/demo.gif` (TODO — short asciinema of `change_runtime` → `add_code_cell` → `run_code_cell`)._

## What's Different

| Feature | Official | This Fork |
|---------|----------|-----------|
| Notebook tools visible at startup | No (needs browser + list_changed) | Yes (pre-registered, works with Claude Code and Codex) |
| `change_runtime` tool (GPU control) | Removed | Working via OAuth |
| OAuth token caching | N/A | Yes (authorize once, cached forever) |
| Windows compatibility | Port 53919 blocked | Fixed (port 8085) |
| ColabClient initialization | N/A | Fixed (Prod() env argument) |
| Stale-server detection / cleanup | None — silent "Disconnected" | Registry + OS process scan, PID/start-time/command validation, profile-scoped explicit actions |

## Available Tools

| Tool | Requires Browser | Requires OAuth | Description |
|------|:---:|:---:|-------------|
| `change_runtime` | | Yes | Assign GPU: T4, L4, A100, or NONE |
| `open_colab_browser_connection` | Yes | | Connect to a Colab notebook; set `open_new_tab=false` to prepare a URL for an existing tab |
| `get_colab_connection_info` | | | Return current token, port, and a complete connection URL for manual handoff |
| `add_code_cell` | Yes | | Add a code cell to the notebook |
| `add_text_cell` | Yes | | Add a markdown cell |
| `get_cells` | Yes | | Read current notebook state (cells, IDs, contents, outputs) |
| `run_code_cell` | Yes | | Execute a code cell by `cellId` |
| `update_cell` | Yes | | Edit an existing cell by `cellId` |
| `delete_cell` | Yes | | Delete a cell by `cellId` |
| `move_cell` | Yes | | Move a cell to a new position by `cellId` |
| `start_code_cell` | Yes | | Start `run_code_cell` in the background and return an `execution_id` |
| `get_code_execution` | | | Poll a background execution's status/result |
| `list_code_executions` | | | List retained background executions |

> **Note:** `execute_cell` was renamed to `run_code_cell` in 2026-06-16 to match the browser-side handler name. Pass a `cellId` (from `add_code_cell` or `get_cells`) — the old `cellIndex` fallback was removed.

## Quick Start (Without OAuth)

If you just want the notebook tools (no `change_runtime`):

### 1. Install uv

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Mac/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Important:** Do NOT use `pip install uv` — that version lacks required features.

### 2. Clone this repo

```bash
git clone https://github.com/ats05/colab-mcp.git
```

### 3. Configure your MCP client

Add to your `.mcp.json` (Claude Code, Cursor, etc.):

```json
{
  "mcpServers": {
    "colab-proxy-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/colab-mcp", "colab-mcp"],
      "timeout": 30000
    }
  }
}
```

### 4. Use it

1. Restart your editor / reload window
2. All tools should appear immediately; no `tools/list_changed` notification is required
3. Call `open_colab_browser_connection()` — the historical empty notebook opens in your browser
4. Use `add_code_cell`, `run_code_cell`, `get_cells`, etc. to control the notebook

To open an existing notebook, pass its HTTPS Colab URL:

```text
open_colab_browser_connection(notebook_url="https://colab.research.google.com/drive/<id>")
```

The query string keeps safe notebook parameters and adds a non-secret port and
nonce for the initial browser URL. The fragment is replaced with the current
token and port when a new browser connection is required, so an old cached
token/port pair is not reused. A shared daemon opens only one initial tab;
subsequent MCP clients reuse that daemon connection.

For training or other long cells, use the asynchronous tools:

```text
start_code_cell(cellId="train-cell")
get_code_execution(execution_id="<returned id>")
list_code_executions()
```

`start_code_cell` returns immediately with `status: "running"`. The local
registry retains bounded status/result history and expires terminal records;
there is intentionally no cancel tool because cancelling a local task cannot
guarantee that code already submitted to Colab stops. The returned
`execution_id` belongs only to this MCP process; it is not portable to the
other MCP process. A process handoff may leave an already accepted Colab
cell running, but the browser/MCP bridge cannot guarantee remote continuation
or completion after disconnect. Reconnect to the same `notebook_url` and use
`get_cells` as the source of truth for the notebook's execution/output state.
Durable progress tracking (for example, a cell heartbeat written to external
storage) is intentionally outside this local registry's scope.

### One shared daemon for Claude Code and Codex

For a handoff that must keep the *same browser tab* and the same Colab
WebSocket connection, run one long-lived Streamable HTTP daemon and point both
clients at its `/mcp` endpoint. FastMCP 2.14.5 keeps the HTTP sessions separate
while the notebook proxy and its single browser connection remain shared in
the daemon process.

Start it once in a regular terminal and leave it running:

```bash
uv run --directory /path/to/colab-mcp colab-mcp \
  --transport streamable-http --host 127.0.0.1 --port 8765
```

Claude Code (`.mcp.json`):

```json
{
  "mcpServers": {
    "colab-shared": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

Codex (`config.toml`):

```toml
[mcp_servers.colab_shared]
url = "http://127.0.0.1:8765/mcp"
```

In the first client only, call
`open_colab_browser_connection(notebook_url="https://colab.research.google.com/drive/<id>", open_new_tab=false)`.
The tool returns the complete credential-bearing URL without opening a
browser; paste it into the already-open target Colab tab's address bar. In the
other client, do not call the open tool again; call `get_cells` (or another
notebook tool) through the same HTTP URL. If both clients race to call the
open tool, the daemon serializes the initial browser open and refuses to
create a second tab. Use the default `open_new_tab=true` only when creating
the initial browser tab is desired.

Keep the daemon alive while switching clients. Its Colab token is generated in
memory and is not persisted. If the daemon itself is restarted, its token and
local WebSocket port change; preserving an already connected tab across that
restart is not guaranteed, so use the returned complete URL to reload the
existing tab or start a new initial connection.

The HTTP endpoint is intentionally bound to `127.0.0.1` in the example. Do
not expose an unauthenticated shared daemon on a LAN or public interface.

---

## Full Setup (With OAuth + GPU Control)

This enables the `change_runtime` tool, which lets your agent assign GPUs without you touching the browser.

### 1. Create OAuth Credentials

You need a Google Cloud project with OAuth configured. This is a one-time setup (~5 minutes):

1. **Create a GCP project** (or use an existing one):
   ```bash
   gcloud projects create colab-mcp-oauth --name="Colab MCP OAuth"
   ```

2. **Configure OAuth consent screen:**
   - Go to [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)
   - Select "External" > Create
   - App name: `Colab MCP`, add your email as support + developer contact
   - Save through all steps

3. **Add yourself as test user:**
   - On the consent screen page > "Test users" > Add your Google email

4. **Create OAuth client ID:**
   - Go to [Credentials](https://console.cloud.google.com/apis/credentials)
   - Create Credentials > OAuth client ID > Desktop app
   - Download the JSON file
   - Save it somewhere safe (e.g., `~/.config/colab-oauth.json`)

> **Note:** OAuth Client IDs can only be created via the Cloud Console web UI. There is no CLI or API for this.

### 2. Configure MCP with OAuth

```json
{
  "mcpServers": {
    "colab-proxy-mcp": {
      "command": "uv",
      "args": [
        "run", "--directory", "/path/to/colab-mcp",
        "colab-mcp",
        "--client-oauth-config", "/path/to/colab-oauth.json"
      ],
      "timeout": 30000
    }
  }
}
```

### 3. Authorize (first time only)

The first time the server starts, it opens your browser for Google OAuth consent. Sign in, click Allow, done. The token is cached at `~/.colab-mcp-auth-token.json` and auto-refreshes — you won't be asked again.

### 4. Use it

```
Agent: change_runtime(accelerator="T4")
> Runtime changed to T4. Endpoint: gpu-t4-s-xxx

Agent: open_colab_browser_connection()
> Connected. Available notebook tools: add_code_cell, add_text_cell, get_cells, run_code_cell, update_cell, delete_cell, move_cell

Agent: add_code_cell(code="!nvidia-smi")
> {"cellId": "abc123", ...}

Agent: run_code_cell(cellId="abc123")
> Tesla T4, 15GB memory...

Agent: get_cells()
> [{"cellId": "abc123", "code": "!nvidia-smi", "outputs": [...]}]
```

---

## CLI Reference

Once installed (via `uv run` or `uvx git+https://github.com/ats05/colab-mcp`), the `colab-mcp` command supports these flags:

| Flag | Description |
|------|-------------|
| _(none)_ | Start the MCP server (default — reads/writes JSON-RPC on stdin/stdout) |
| `-l DIR`, `--log DIR` | Write logs to `DIR`. Defaults to a temp dir under `%TEMP%` / `$TMPDIR` |
| `-p`, `--enable-proxy` | Enable the runtime proxy that exposes browser-based notebook tools. On by default |
| `--client-oauth-config PATH` | Path to OAuth client-secrets JSON. Enables the `change_runtime` tool for programmatic GPU assignment |
| `--list-running` | Print verified servers in the selected profile, including OS-discovered servers not in the registry |
| `--profile NAME` | Select an independent process group (default: `default`; useful for `claude` and `codex`) |
| `--replace` | Explicitly stop verified peers in this profile before starting. Never implied by normal startup |
| `--stop-pid PID` | Explicitly stop one verified server in this profile |
| `--kill-stale` | Explicitly stop verified servers in this profile, including ones outside the registry. Add `--all-profiles` only when intentionally stopping every profile |
| `--all-profiles` | Broaden an explicit `--stop-pid`/`--kill-stale` action; it has no effect on normal startup |
| `--transport` | `stdio` by default; use `streamable-http` for one shared local daemon |
| `--host` | HTTP bind address (default `127.0.0.1`; ignored for stdio) |
| `--port` | HTTP port (FastMCP default when omitted; ignored for stdio) |
| `--path` | HTTP endpoint path (default `/mcp`; ignored for stdio) |

The server maintains a tiny registry at `%LOCALAPPDATA%\colab-mcp\registry.json` (Windows) or `~/.colab-mcp/registry.json` (macOS/Linux). Each running instance writes `{pid, port, host, profile, started_at, command}` on startup and removes its own entry on clean shutdown. The MCP token is never stored. Stale/PID-reused entries are pruned automatically on startup. A normal server start never kills another Claude Code or Codex process.

## Claude Code ↔ Codex handoff

Each stdio MCP configuration normally starts its own process, with its own local
port and token. That is safe as long as only one browser tab is actively
connected to a given notebook at a time, but separate stdio processes cannot
guarantee reuse of one browser tab because the Colab token belongs to the
process. For same-tab handoff, use the shared Streamable HTTP daemon above.
Do not use `--replace` or `--kill-stale` in an automatically launched MCP
command: those are explicit maintenance actions.

For a handoff while preserving the Colab notebook/runtime:

1. In the current client, call `get_colab_connection_info` if you need the
   notebook URL/connection coordinates, and make sure the notebook is saved at
   its normal Colab URL (the scratch `empty.ipynb` is not a durable handoff
   target).
2. End the current Claude Code/Codex MCP session. This closes only that local
   bridge; it does not request a runtime shutdown from Colab.
3. For a same-tab handoff, keep the shared daemon running and switch the
   client to the same HTTP `/mcp` URL. The new client reuses the daemon's
   existing Colab connection; do not call `open_colab_browser_connection`
   again. If the tab needs a fresh endpoint, call the open tool once with
   `open_new_tab=false` and paste the returned complete URL into that same
   tab's address bar.
4. Call `get_cells` to verify that cells and outputs are present. Because both
   clients use the same daemon process, a still-retained background
   `execution_id` may also be reused with `get_code_execution` or
   `list_code_executions`. If a long cell was in flight, treat its continuation
   as unverified until the returned cell state/output confirms what happened.

When using separate stdio processes instead, passing the same `notebook_url`
preserves the notebook target but gives the new process a different token and
port. It cannot promise the old tab will switch endpoints. Use the complete
URL from `get_colab_connection_info` in the existing tab's address bar (or
start one new tab) and verify with `get_cells`. In this separate-process case,
the old `execution_id` is not available in the new local registry; the shared
daemon is the supported same-tab and execution-ID-sharing path.

If the browser does not connect automatically, call
`get_colab_connection_info` in the new client. It returns `token`, `port`, and
the complete URL separately. Paste the complete URL into the browser address
bar to open/reload the notebook. Colab's connection dialog and command-palette
wording varies by frontend version, so the individual values are provided for
manual entry when the current UI exposes separate fields; this project does
not assume an undocumented token/port encoding. The token-bearing URL is
never logged or written to the process registry.

Example profile-separated configuration (profile separation is a safety guard,
not a shared daemon):

```json
{
  "mcpServers": {
    "colab-claude": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/colab-mcp", "colab-mcp", "--profile", "claude"]
    },
    "colab-codex": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/colab-mcp", "colab-mcp", "--profile", "codex"]
    }
  }
}
```

The two processes can be listed together from a regular shell with
`colab-mcp --list-running --profile claude` and
`colab-mcp --list-running --profile codex`. A process is stopped only after
its command line and start time still match the recorded PID, so PID reuse or
an unrelated process cannot be signalled accidentally.

## Troubleshooting

### Tools don't appear after setup
- Make sure you're using this fork, not the official repo
- Do not define the same `colab-proxy-mcp` entry twice for one client (for example, in both its global and project config). Claude and Codex may each have one explicitly profile-separated entry.
- Restart your editor after changing `.mcp.json`

### `change_runtime` returns "Runtime API not initialized"
- Check that `--client-oauth-config` is in your `.mcp.json` args
- Check that the OAuth JSON file exists at the specified path
- Look at the server logs for the specific error:
  ```bash
  # Find the latest log
  ls -t $TMPDIR/colab-mcp-logs-*/colab-mcp.*.log | head -1 | xargs cat
  ```
- A healthy log shows: `INFO:Colab API client ready`
- If you see `WARNING:Failed to initialize Colab API client`, check the error message

### Windows: Port blocked error (WinError 10013)
Already fixed in this fork (changed to port 8085). If you still hit it, edit `src/colab_mcp/auth.py` and change `OAUTH_SERVER_PORT` to any open port.

### OAuth says "Access denied"
Add your Google email as a test user in Cloud Console > OAuth consent screen > Test users.

### Browser opens but connection times out
Make sure you have a Colab notebook open in the browser tab that opened. Click "Connect" if prompted.

### Chrome reused an old Colab tab pointing at a dead port

Chrome dedupes tabs by URL canonical (ignoring the `#fragment`), so when an old Colab tab is still open with a fragment pointing at a previous server's port, calling `open_colab_browser_connection` again may silently focus the old tab instead of opening a fresh one. The old tab shows "Disconnected from the local Colab MCP server" and the new server times out.

This fork keeps the current port as a query param (`?p=<port>`) for the
initial URL. In shared-daemon mode, only the first client opens that URL;
later clients reuse the daemon's already-connected tab. If you use separate
stdio processes and still hit the stale-tab case after upgrading:

1. Close every `colab.research.google.com` tab in your browser.
2. Retry `open_colab_browser_connection` in the new stdio process — it will
   open one fresh initial tab pointed at the live server. For a same-tab
   handoff, keep one Streamable HTTP daemon alive instead.

### Chrome silently blocks every connection attempt after one previous "Block"

If Chrome shows "Disconnected from the local Colab MCP server" on *every* attempt — including immediately after the page loads, with no permission prompt — and the server logs only `stream ends after 0 bytes` (TCP opens then closes without any HTTP request), the most likely cause is that you previously clicked **"Block"** on the Local Network Access prompt for `colab.research.google.com`. Chrome remembers that choice **per site** and never asks again — every WebSocket attempt is silently cancelled before the handshake. Edge / Firefox / other Chromium profiles are unaffected.

**Fix (Chrome):**
1. Open `chrome://settings/content/siteDetails?site=https%3A%2F%2Fcolab.research.google.com`
2. Find **"Access other devices on the network"** / **"Acceder a otros dispositivos en la red"** / Insecure content
3. Change from **Block** to **Ask**
4. Reload the Colab tab and accept the prompt when it appears.

Quickest reset (clears all Colab site permissions):
1. Open `https://colab.research.google.com`
2. Click the **lock icon** next to the URL
3. Click **"Reset permissions"** / **"Restablecer permisos"**
4. Reload and try again.

This was reproduced and root-caused with a manual E2E test (`scripts/manual_browser_test.py`): Edge connected on first attempt, Chrome timed out indefinitely until the per-site permission was reset.

### Chrome asks for "Permission to access other services and apps on this device" (or Colab says "Disconnected")

When the Colab tab loads, Chrome shows a permission prompt:

> **colab.research.google.com wants — Permission to access other services and apps on this device**

**Click _Allow_.** If you block it, the WebSocket connection from the Colab tab to your local `colab-mcp` server is blocked, the tab shows "Disconnected from the local Colab MCP server", and `open_colab_browser_connection` will time out.

This prompt is Chrome's **Local Network Access** policy: a public site (`https://colab.research.google.com`) is asking to talk to a resource on your local network (`ws://localhost:<port>` where `colab-mcp` is listening). Chrome blocks this by default and asks the user. The "other service" in the prompt is **your own `colab-mcp` server running on your machine** — not external access. The connection is scoped to a one-time token in the URL fragment (`#mcpProxyToken=...`), so even on the same machine other processes can't piggy-back on it.

Chrome remembers the choice per-site, so you only need to allow it once for `colab.research.google.com`.

### "Disconnected from the local Colab MCP server" — IPv4/IPv6 dual-stack bind (root cause)

If you saw this message on the official `googlecolab/colab-mcp` and assumed it was an orphaned-server issue, the **actual root cause** is different — and is fixed in this fork.

With `host="localhost"` + `port=0`, the `websockets` library binds **two sockets on different ephemeral ports** (one for IPv6 `::1` and one for IPv4 `127.0.0.1`), then reports only one of them as the "server port". The Colab tab opens `ws://localhost:<reported-port>`, Chrome resolves `localhost` to either address family, and connects to a port with **no listener** in 50% of cases. The TCP connection drops with `stream ends after 0 bytes` server-side, the Colab tab shows "Disconnected from the local Colab MCP server" instantly, and the user waits 60s for a generic timeout.

This fork forces IPv4-only (`host="127.0.0.1"`) so there is exactly one socket on exactly one port, and asserts this invariant at startup (raising `RuntimeError` if a future change re-introduces the dual-bind). See [`websocket_server.py`](src/colab_mcp/websocket_server.py) and the tests `test_single_socket_single_port` / `test_default_host_is_ipv4`.

### Orphaned colab-mcp processes (separate issue)

If a Colab tab in your browser shows **"Disconnected from the local Colab MCP server"** and re-clicking *Connect* doesn't help, the cause is almost always one or more **orphaned colab-mcp processes** from previous Claude Code sessions. Each instance picks a random ephemeral port, but your Colab tab only remembers the port from the URL fragment used when it first opened — when that server dies (or you spawn a new Claude Code session with a new server on a different port), the tab keeps trying to reach a dead address.

This fork ships with built-in diagnostics. Run any of these from a **regular shell** (not from inside Claude Code, which is itself running an MCP instance):

```bash
# Show every colab-mcp server currently registered as running
uv run --directory /path/to/colab-mcp colab-mcp --list-running

# Explicitly terminate verified servers in the selected profile, then exit
uv run --directory /path/to/colab-mcp colab-mcp --kill-stale --profile default
```

The server writes a small registry file at `%LOCALAPPDATA%\colab-mcp\registry.json` (Windows) or `~/.colab-mcp/registry.json` (macOS/Linux) listing pid + port for each running instance. On every startup it prunes dead/PID-reused entries automatically, and on clean shutdown it removes its own. The diagnostic list also scans for unregistered server processes. If `open_colab_browser_connection` times out, the error includes the ports + pids of peer servers in the same profile so you can identify which endpoint your browser tab is using.

After cleaning up, re-run `open_colab_browser_connection` from the new stdio
process — it will open one initial Colab tab pointed at the current server's
port + token. If another profile owns a live process, it is intentionally left
alone. A shared Streamable HTTP daemon avoids this replacement flow and keeps
one tab across Claude/Codex handoff.

Fixes [upstream issue #84](https://github.com/googlecolab/colab-mcp/discussions/84).

---

## Compatibility

Tested with:
- Claude Code (VS Code extension + CLI)
- Should work with any MCP client that supports the standard tool protocol (Cursor, Windsurf, Codex, etc.)

Supported platforms:
- Windows 10/11
- macOS
- Linux

---

## Changes from Upstream

This fork is based on [`googlecolab/colab-mcp`](https://github.com/googlecolab/colab-mcp) with these changes:

- **`f70c00d`** Register notebook tools directly on the FastMCP server at startup (fixes invisible tools)
- **`cae498b`** Add `change_runtime` tool with OAuth for programmatic GPU assignment
- **`440e3bc`** Fix `ColabClient` initialization (missing `Prod()` env arg) + change OAuth port to 8085 for Windows
- **`e66ee69`** Match real Colab API signatures (language param, cellId, run_code_cell)
- **stale-server detection** Process registry + `--list-running` / `--kill-stale` flags + clearer timeout diagnostics — fixes [upstream #84](https://github.com/googlecolab/colab-mcp/discussions/84) "Disconnected from the local Colab MCP server"
- **full 7-tool notebook surface** — pre-register `get_cells`, `delete_cell`, `move_cell` (previously missing) and rename `execute_cell` → `run_code_cell` to match the browser-side handler. Closes [upstream #69](https://github.com/googlecolab/colab-mcp/discussions/69).
- **notebook handoff** — `open_colab_browser_connection(notebook_url)` preserves existing notebook query parameters, replaces stale MCP fragment credentials, and adds non-secret cache-busting values; `get_colab_connection_info` exposes separate token/port fields without persisting them.
- **long-cell polling** — `start_code_cell` starts the existing browser-side `run_code_cell` call in a bounded local background registry; `get_code_execution` and `list_code_executions` expose status/result while `run_code_cell` remains synchronous for compatibility.
- **safe process lifecycle** — process-table discovery covers unregistered instances, validates start time and command line before signaling, and scopes explicit `--replace`/`--kill-stale`/`--stop-pid` actions by profile. Normal startup never kills a peer.
- **shared daemon transport** — an explicit FastMCP Streamable HTTP mode lets Claude Code and Codex use one long-lived MCP process, one Colab WebSocket, and one browser tab; stdio remains the default.

Google [does not accept external contributions](https://github.com/googlecolab/colab-mcp/blob/main/CONTRIBUTING.md) to the official repo, so these fixes live here.

## Verified fixes (accepted in upstream discussions)

- **[#67 → answered](https://github.com/googlecolab/colab-mcp/discussions/67)** — invisible-tools fix (this fork's pre-registration approach was accepted by the upstream community as the working solution).
- **[#69](https://github.com/googlecolab/colab-mcp/discussions/69)** — follow-up on `get_cells` and the remaining missing stubs — addressed in this fork on 2026-06-16.
- **[#84](https://github.com/googlecolab/colab-mcp/discussions/84)** — "Disconnected from the local Colab MCP server" — addressed via the stale-server registry + `--kill-stale` CLI.

---

## License

Apache 2.0 (same as upstream)

---

⭐ **If this fork saved you time, a star helps others find it.**
