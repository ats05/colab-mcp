# Copyright 2026 Atsushi Onozawa.
# Licensed under the Apache License, Version 2.0.

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest


def test_shared_transport_arguments_are_explicit_and_local_by_default():
    from colab_mcp import parse_args

    args = parse_args(
        [
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
            "--path",
            "/mcp",
        ]
    )

    assert args.transport == "streamable-http"
    assert args.host == "127.0.0.1"
    assert args.port == 8765
    assert args.path == "/mcp"
    assert args.allow_insecure_non_loopback is False


def test_stdio_remains_the_default_transport():
    from colab_mcp import parse_args

    args = parse_args([])

    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port is None
    assert args.path is None
    assert args.allow_insecure_non_loopback is False


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])
def test_http_transport_rejects_non_loopback_host_without_explicit_ack(host):
    from colab_mcp import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--transport", "streamable-http", "--host", host])


def test_http_transport_allows_non_loopback_host_with_explicit_ack():
    from colab_mcp import parse_args

    args = parse_args(
        [
            "--transport",
            "streamable-http",
            "--host",
            "0.0.0.0",
            "--allow-insecure-non-loopback",
        ]
    )

    assert args.host == "0.0.0.0"
    assert args.allow_insecure_non_loopback is True


@pytest.mark.parametrize("host", ["localhost", "127.0.0.2", "::1", "[::1]"])
def test_http_transport_accepts_loopback_variants(host):
    from colab_mcp import parse_args

    args = parse_args(["--transport", "streamable-http", "--host", host])

    assert args.host == host


@pytest.mark.asyncio
async def test_shared_transport_uses_fastmcp_streamable_http_options(monkeypatch):
    import colab_mcp

    run_async = AsyncMock()
    monkeypatch.setattr(colab_mcp.mcp, "run_async", run_async)
    args = SimpleNamespace(
        transport="streamable-http",
        host="127.0.0.1",
        port=8765,
        path="/mcp",
    )

    await colab_mcp._run_mcp_transport(args)

    run_async.assert_awaited_once_with(
        transport="streamable-http",
        host="127.0.0.1",
        port=8765,
        path="/mcp",
    )


def test_fastmcp_shared_endpoint_is_stateful_streamable_http():
    from colab_mcp import mcp

    app = mcp.http_app(
        path="/mcp",
        transport="streamable-http",
        stateless_http=False,
    )

    assert app.state.path == "/mcp"
    assert len(app.routes) == 1
    assert app.routes[0].path == "/mcp"


@pytest.mark.asyncio
async def test_shared_endpoint_accepts_two_mcp_clients():
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    from colab_mcp import mcp

    app = mcp.http_app(
        path="/mcp",
        transport="streamable-http",
        stateless_http=False,
    )

    def httpx_client_factory(headers=None, timeout=None, auth=None, **kwargs):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
            timeout=timeout,
            auth=auth,
            **kwargs,
        )

    first_transport = StreamableHttpTransport(
        "http://testserver/mcp",
        httpx_client_factory=httpx_client_factory,
    )
    second_transport = StreamableHttpTransport(
        "http://testserver/mcp",
        httpx_client_factory=httpx_client_factory,
    )

    async with app.router.lifespan_context(app):
        async with Client(first_transport) as first, Client(second_transport) as second:
            first_tools = {tool.name for tool in await first.list_tools()}
            second_tools = {tool.name for tool in await second.list_tools()}

    assert first_tools == second_tools
    assert "open_colab_browser_connection" in first_tools
    assert "get_cells" in first_tools
