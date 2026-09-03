# Copyright 2026 Sebastian Gil Pinzon.
# Modified by Atsushi Onozawa, 2026.
# Licensed under the Apache License, Version 2.0.

"""Manual browser test — open the Colab URL and wait for a connection.

Used to diagnose browser-specific connection bugs (e.g., Chrome blocking
localhost WebSockets while Edge / Firefox / brave do not).

Usage:
    uv run python scripts/manual_browser_test.py

The script starts the colab-mcp WebSocket server (same code as the real MCP
server), opens the connection URL in the default browser, and then waits up to
5 minutes for a connection. The bearer token and token-bearing URL are never
printed. The script reports whether the connection succeeded or timed out.
"""

import asyncio
import sys
import webbrowser

from colab_mcp.websocket_server import ColabWebSocketServer
from colab_mcp.connection import build_connection_info


WAIT_SECONDS = 300


async def main():
    async with ColabWebSocketServer() as wss:
        connection = build_connection_info(
            None,
            token=wss.token,
            port=wss.port,
        )
        print("=" * 78)
        print("Server is running on:")
        print(f"  ws://127.0.0.1:{wss.port}")
        print("Opening a token-bearing connection URL in the default browser.")
        print("The credential is intentionally not printed to stdout.")
        print()
        print(f"Waiting up to {WAIT_SECONDS}s for a browser to connect...")
        print("=" * 78)
        sys.stdout.flush()
        webbrowser.open_new(connection.url)

        try:
            await asyncio.wait_for(wss.connection_live.wait(), timeout=WAIT_SECONDS)
            print()
            print(">>> CONNECTED — browser successfully established WebSocket")
            print(">>> Keeping server alive for 60 more seconds so the tab stays connected.")
            print(">>> You should see the Colab toast change from 'Disconnected' to 'Connected'.")
            print()
            # Hold the connection so the user can verify in the browser that the
            # connect persists. Without this, exiting the `async with` block
            # closes the server and the browser sees "Disconnected" immediately.
            await asyncio.sleep(60)
        except asyncio.TimeoutError:
            print()
            print(f">>> TIMEOUT after {WAIT_SECONDS}s — no browser connected.")
            print(">>> If you pasted the URL and saw 'Disconnected', the browser engine ")
            print(">>> is rejecting the WebSocket connection (not the server).")
            print()
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
