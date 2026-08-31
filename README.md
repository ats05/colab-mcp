# Colab MCP

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13+-blue.svg)](pyproject.toml)
[![MCP](https://img.shields.io/badge/protocol-MCP-purple.svg)](https://modelcontextprotocol.io)
[![Stars](https://img.shields.io/github/stars/ats05/colab-mcp?style=social)](https://github.com/ats05/colab-mcp)

An MCP server for controlling Google Colab from AI coding agents. It connects
an MCP client to a Google Colab notebook through a local server and a browser
tab, so an agent can inspect and edit cells and run code in the notebook.

> This is a private standalone mirror based on
> [`googlecolab/colab-mcp`](https://github.com/googlecolab/colab-mcp). GitHub
> fork synchronization is not enabled; upstream updates are manual. See the
> clone examples below for authenticated access.

## 日本語ガイド

### これは何か

`colab-mcp`は、Claude Code、Codex、CursorなどのAIコーディングエージェントからGoogle Colabを操作するためのMCPサーバーです。基本構成は、エージェント → ローカルのMCPサーバー（通常は`stdio`、共有時はStreamable HTTP）→ ブラウザのColabタブ → Colabのノートブック／ランタイム、という流れです。ノートブックの読み書きとコード実行には、Colabのブラウザ接続が必要です。GPUランタイムの変更だけは、任意のOAuth設定で利用できます。

### 基本機能

- 任意の既存HTTPS Colab URL、またはデフォルトのscratch notebookに接続
- コード／Markdownセルの管理と、コードセルの実行
- 長時間セル向けのバックグラウンド実行開始・状態取得・一覧表示
- OAuthを設定した場合のT4、L4、A100、NONEランタイムの割り当て

現在のMCPツールは13個です。接続（`open_colab_browser_connection`、`get_colab_connection_info`）、セル操作7個、長時間実行3個、ランタイム変更（`change_runtime`）で構成されます。セル操作では`add_code_cell`または`get_cells`で得た`cellId`を使います。

### 公式版との比較・このミラーの改善

公式の`googlecolab/colab-mcp`を基準に、このミラーでは次を改善・追加しています。

- 接続前から全ツールを登録し、`notifications/tools/list_changed`に依存せずクライアントが発見できるようにした
- 既存ノートのURL指定、古い接続情報を除いたURL生成、`open_new_tab=false`による既存タブ利用を追加
- registryとOSプロセス表を照合し、PID・起動時刻・コマンド・profileを検証したうえで、明示的な診断／停止だけを行う
- IPv4固定（`127.0.0.1`）とChromeのPrivate Network Access（PNA）用ヘッダーで、ローカルWebSocket接続の失敗要因を修正
- 長時間セル用の`start_code_cell`、`get_code_execution`、`list_code_executions`を追加
- OAuthによるGPUランタイム変更と、複数クライアントで任意に共有できるStreamable HTTP daemonを追加
- OAuthトークンをローカルにキャッシュして更新し、WindowsのOAuthコールバックポートを`8085`に変更、Colab API初期化の不足引数も修正

### 基本利用（単独クライアント）

private mirrorを取得できる状態にしてから、`uv`とMCPクライアントを設定します。HTTPSでは次のようにGitHub CLIで認証します。個人アクセストークンをURLやシェル履歴に直接書かないでください。

```bash
gh auth login && gh auth setup-git
git clone https://github.com/ats05/colab-mcp.git
cd colab-mcp
```

`.mcp.json`に次を追加すると、クライアントが単独のstdioサーバーを起動します。

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

クライアントを再起動し、次を呼び出します。`notebook_url`を省略するとscratch notebookが開きます。既存ノートを使う場合はHTTPSのColab URLを渡します。

```text
open_colab_browser_connection(
  notebook_url="https://colab.research.google.com/drive/<id>"
)
get_cells()
add_code_cell(code="print('hello')")
run_code_cell(cellId="<cellId>")
```

### 複数エージェントからの共有操作（任意）

同じColabタブと実行状態をClaude Code／Codexなどで共有したい場合だけ、通常のターミナルでdaemonを1つ起動します。

```bash
uv run --directory /path/to/colab-mcp colab-mcp \
  --transport streamable-http --host 127.0.0.1 --port 8765
```

各クライアントを同じendpointへ登録します。

```bash
claude mcp add --transport http --scope user colab-shared \
  http://127.0.0.1:8765/mcp
codex mcp add colab-shared --url http://127.0.0.1:8765/mcp
```

最初のクライアントだけで、対象の既存タブ向けに一度呼び出します。

```text
open_colab_browser_connection(
  notebook_url="https://colab.research.google.com/drive/<id>",
  open_new_tab=false
)
```

返された完全なURLを既存タブのアドレスバーへ貼り付け、2つ目以降のクライアントでは`open_colab_browser_connection`を呼び直さず`get_cells`などを使います。`execution_id`も同じdaemonを使うクライアント間では引き継げますが、別々のstdioプロセスでは共有できません。完全な接続URLはbearer tokenを含むため、公開チャット、issue、ログ、設定、履歴、commitなど第三者が見られる場所へ転載しないでください。endpointは例のように`127.0.0.1`へ限定します。

詳細なOAuth設定、CLI、トラブルシューティングは以下を参照してください。

## What is Colab MCP?

`colab-mcp` is an [MCP](https://modelcontextprotocol.io) server. It exposes
Google Colab notebook operations to an MCP client such as Claude Code, Codex,
Cursor, or another AI coding agent. The default transport is `stdio`, where the
client launches one local process. A local Streamable HTTP transport is also
available when several clients should share one process.

The browser is the bridge to Colab: the local server opens (or prepares) a
Colab URL, the user loads it in a browser tab, and the server forwards MCP
tool calls over the browser's WebSocket connection. The notebook operations
therefore require an active browser connection. OAuth is optional and only
needed for programmatic runtime/GPU assignment.

## Basic capabilities

The server can target the default scratch notebook or any existing HTTPS
notebook URL on `colab.research.google.com` / `colab.google.com`. Once connected,
an agent can manage code and Markdown cells, and run code cells.
Long-running code can be started in the background and polled separately.

There are 13 registered tools. They are available to the MCP client at startup,
even before a browser has connected; notebook calls return an explicit
`COLAB_NOT_CONNECTED` message until a connection is established.

| Tool | Requires Browser | Requires OAuth | Description |
|------|:---:|:---:|-------------|
| `open_colab_browser_connection` | Yes | | Connect to a Colab notebook; set `open_new_tab=false` to prepare a URL for an existing tab |
| `get_colab_connection_info` | | | Return the current token, port, and complete URL for manual handoff |
| `add_code_cell` | Yes | | Add a code cell |
| `add_text_cell` | Yes | | Add a Markdown cell |
| `get_cells` | Yes | | Read cells, IDs, contents, and outputs |
| `run_code_cell` | Yes | | Execute a cell by `cellId` |
| `update_cell` | Yes | | Edit a cell by `cellId` |
| `delete_cell` | Yes | | Delete a cell by `cellId` |
| `move_cell` | Yes | | Move a cell by `cellId` |
| `start_code_cell` | Yes | | Start a cell in the background and return an `execution_id` |
| `get_code_execution` | | | Poll a background execution's status/result |
| `list_code_executions` | | | List retained background executions |
| `change_runtime` | | Yes | Assign a `T4`, `L4`, `A100`, or `NONE` runtime |

> **Note:** `execute_cell` was renamed to `run_code_cell` in 2026-06-16 to
> match the browser-side handler. Pass a `cellId` from `add_code_cell` or
> `get_cells`; the old `cellIndex` fallback was removed.

## Improvements over the official `googlecolab/colab-mcp`

This mirror keeps the browser-based MCP design and adds the following fixes and
optional capabilities:

| Area | Official baseline | This mirror |
|------|-------------------|-------------|
| Tool discovery | Notebook tools appear after browser connection and may require `notifications/tools/list_changed` | All 13 tools are registered at process startup |
| Existing notebooks and tabs | Connection flow primarily opens a new URL/tab | Accepts an existing HTTPS `notebook_url`; `open_new_tab=false` prepares a URL for an existing tab |
| Process lifecycle | A stale process can leave a tab pointed at a dead port | Registry + OS process scan; PID, start time, command, and profile are checked before explicit stop/cleanup |
| Local browser connection | `localhost` dual-stack binding and missing PNA response headers can make Chrome fail to reach the server | IPv4-only `127.0.0.1` binding plus PNA/CORS headers on preflight and WebSocket upgrade |
| Long-running cells | No local background execution API | `start_code_cell`, `get_code_execution`, and `list_code_executions` with bounded, process-local tracking |
| Runtime/GPU control | The upstream runtime flag/API is not available in the baseline | Optional OAuth-backed `change_runtime` for T4, L4, A100, or NONE |
| OAuth token handling | Not provided by the upstream baseline | Cached locally and refreshed as needed |
| Windows OAuth callback | The default callback port can be blocked | Uses port `8085` |
| Colab API initialization | Missing required environment argument in the baseline | Supplies the required `Prod()` environment |
| Multiple clients | Default client-launched processes are isolated | Optional Streamable HTTP daemon lets multiple MCP clients share one browser connection and execution registry |

The process-safety actions are explicit: normal startup does not terminate a
peer. See [CLI Reference](#cli-reference) and
[Troubleshooting](#troubleshooting) for the diagnostic commands and browser
permission details.

## Quick Start (single client, without OAuth)

This is the normal setup for one MCP client. It provides the notebook tools;
`change_runtime` is optional and requires the OAuth setup described below.

### 1. Install uv

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Mac/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Important:** Do NOT use `pip install uv` — that version lacks required features.

### 2. Clone this private mirror

Authenticate to GitHub before cloning. For HTTPS, configure GitHub CLI first:

```bash
gh auth login && gh auth setup-git
git clone https://github.com/ats05/colab-mcp.git
cd colab-mcp
```

SSH is also supported when an SSH key/agent is configured:

```bash
git clone git@github.com:ats05/colab-mcp.git
cd colab-mcp
```

Do not put a personal access token in a clone URL, shell history, MCP config,
or the repository.

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

This starts one local MCP process for this client. For multiple clients that
should share one browser connection, use the optional shared-daemon setup
below.

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

`start_code_cell` returns immediately with `status: "running"`; poll with
`get_code_execution` until the status is `completed` or `failed`. The local
registry retains bounded status/result history and expires terminal records.
The returned `execution_id` belongs to the MCP process: both clients inherit
the ID when they use one shared daemon, but separate stdio processes cannot
reuse it. A process handoff may leave an already accepted Colab cell running,
but the browser/MCP bridge cannot guarantee remote continuation or completion
after disconnect. Reconnect to the same `notebook_url` and use `get_cells` as
the source of truth for the notebook's execution/output state.

## Optional/Advanced: Multiple agents and a shared daemon

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

Without a local clone, `uvx` can fetch the current `main` branch directly. The Git URL still
requires private-repository authentication through your SSH agent or a
configured Git credential helper:

```bash
uvx --from "git+ssh://git@github.com/ats05/colab-mcp.git" \
  colab-mcp --transport streamable-http --host 127.0.0.1 --port 8765
```

Claude Code CLI:

```bash
claude mcp add --transport http --scope user colab-shared \
  http://127.0.0.1:8765/mcp
```

Codex CLI:

```bash
codex mcp add colab-shared --url http://127.0.0.1:8765/mcp
```

Equivalent Claude Code `.mcp.json`:

```json
{
  "mcpServers": {
    "colab-shared": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

Equivalent Codex `config.toml`:

```toml
[mcp_servers."colab-shared"]
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

The complete URL returned for a manual connection contains a bearer token. Do
not paste it into a public chat or issue, logs, configuration, shell history,
commit, or another place where a third party can see it.

The HTTP endpoint is intentionally bound to `127.0.0.1` in the example. Do
not expose an unauthenticated shared daemon on a LAN or public interface.

---

## Optional: OAuth + GPU control

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

The following is the optional stdio configuration. For the recommended shared
daemon, keep both clients pointed at the HTTP URL from Quick Start and add
`--client-oauth-config /path/to/colab-oauth.json` to the daemon command instead.

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

Once installed from the private mirror clone (`uv run`) or with the private Git
source form (`uvx --from "git+ssh://git@github.com/ats05/colab-mcp.git"`), the `colab-mcp` command supports these flags:

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

## Separate processes and handoff details

Each stdio configuration starts its own process, token, port, and execution
registry. Passing the same `notebook_url` preserves the notebook target, but it
does not guarantee that a new process can reuse the old browser tab, and the
old `execution_id` is not available in the new process. Use the shared daemon
above for same-tab and execution-ID sharing. After switching clients, call
`get_cells` to verify the notebook state; a long cell's continuation remains
unverified until its returned cell state/output confirms what happened.

For manual recovery, `get_colab_connection_info` returns `token`, `port`, and
the complete URL separately. The current Colab UI may expose a connection
dialog or command palette for those individual values; this project does not
assume an undocumented token/port encoding. Do not use `--replace` or
`--kill-stale` in an automatically launched MCP command: both are explicit
maintenance actions.

Profile separation is a safety guard for independent processes, not a shared
daemon. The two processes can be inspected from a regular shell with
`colab-mcp --list-running --profile claude` and
`colab-mcp --list-running --profile codex`. A process is stopped only when its
command line and start time still match the recorded PID.

## Troubleshooting

### Tools don't appear after setup
- Make sure you're using this private mirror, not the official repo
- Do not define the same MCP endpoint twice for one client (for example, in both its global and project config). Claude and Codex may each have one shared-daemon entry.
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
Already fixed in this mirror (changed to port 8085). If you still hit it, edit `src/colab_mcp/auth.py` and change `OAUTH_SERVER_PORT` to any open port.

### OAuth says "Access denied"
Add your Google email as a test user in Cloud Console > OAuth consent screen > Test users.

### Browser opens but connection times out
Make sure you have a Colab notebook open in the browser tab that opened. Click "Connect" if prompted.

### Chrome reused an old Colab tab pointing at a dead port

Chrome dedupes tabs by URL canonical (ignoring the `#fragment`), so when an old Colab tab is still open with a fragment pointing at a previous server's port, calling `open_colab_browser_connection` again may silently focus the old tab instead of opening a fresh one. The old tab shows "Disconnected from the local Colab MCP server" and the new server times out.

This mirror keeps the current port as a query param (`?p=<port>`) for the
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

If you saw this message on the official `googlecolab/colab-mcp` and assumed it was an orphaned-server issue, the **actual root cause** is different — and is fixed in this mirror.

With `host="localhost"` + `port=0`, the `websockets` library binds **two sockets on different ephemeral ports** (one for IPv6 `::1` and one for IPv4 `127.0.0.1`), then reports only one of them as the "server port". The Colab tab opens `ws://localhost:<reported-port>`, Chrome resolves `localhost` to either address family, and connects to a port with **no listener** in 50% of cases. The TCP connection drops with `stream ends after 0 bytes` server-side, the Colab tab shows "Disconnected from the local Colab MCP server" instantly, and the user waits 60s for a generic timeout.

This mirror forces IPv4-only (`host="127.0.0.1"`) so there is exactly one socket on exactly one port, and asserts this invariant at startup (raising `RuntimeError` if a future change re-introduces the dual-bind). See [`websocket_server.py`](src/colab_mcp/websocket_server.py) and the tests `test_single_socket_single_port` / `test_default_host_is_ipv4`.

### Orphaned colab-mcp processes (separate issue)

If a Colab tab in your browser shows **"Disconnected from the local Colab MCP server"** and re-clicking *Connect* doesn't help, the cause is almost always one or more **orphaned colab-mcp processes** from previous Claude Code sessions. Each instance picks a random ephemeral port, but your Colab tab only remembers the port from the URL fragment used when it first opened — when that server dies (or you spawn a new Claude Code session with a new server on a different port), the tab keeps trying to reach a dead address.

This mirror ships with built-in diagnostics. Run any of these from a **regular shell** (not from inside Claude Code, which is itself running an MCP instance):

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

This private mirror is based on [`googlecolab/colab-mcp`](https://github.com/googlecolab/colab-mcp) with these changes:

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

- **[#67 → answered](https://github.com/googlecolab/colab-mcp/discussions/67)** — invisible-tools fix (this mirror's pre-registration approach was accepted by the upstream community as the working solution).
- **[#69](https://github.com/googlecolab/colab-mcp/discussions/69)** — follow-up on `get_cells` and the remaining missing stubs — addressed in this mirror on 2026-06-16.
- **[#84](https://github.com/googlecolab/colab-mcp/discussions/84)** — "Disconnected from the local Colab MCP server" — addressed via the stale-server registry + `--kill-stale` CLI.

---

## License

Apache 2.0 (same as upstream)

---

⭐ **If this mirror saved you time, a star helps others find it.**
