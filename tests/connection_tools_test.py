import asyncio
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, parse_qsl, urlparse

import pytest


@pytest.fixture
def proxy_client():
    proxy = Mock()
    proxy.wss.token = "current-token"
    proxy.wss.port = 54321
    proxy.is_connected.return_value = False
    proxy.await_proxy_connection = AsyncMock()
    proxy.await_tools_ready = AsyncMock(return_value=["get_cells"])
    return proxy


@pytest.mark.asyncio
async def test_open_connection_uses_existing_notebook_url_and_fresh_credentials(proxy_client):
    import colab_mcp

    # The connection lock rechecks state after reserving the single browser
    # open, then the await path checks it once more after the mock connects.
    proxy_client.is_connected.side_effect = [False, False, True]
    with patch.object(colab_mcp, "_proxy_client", proxy_client), patch(
        "colab_mcp.webbrowser.open_new"
    ) as open_new, patch.object(colab_mcp.process_registry, "list_running", return_value=[]):
        result = await colab_mcp.open_colab_browser_connection.fn(
            "https://colab.research.google.com/drive/notebook?authuser=2#old"
        )

    assert "Connection successful" in result
    opened_url = open_new.call_args.args[0]
    parsed = urlparse(opened_url)
    assert parse_qs(parsed.query)["authuser"] == ["2"]
    assert parse_qs(parsed.query)["p"] == ["54321"]
    assert parse_qsl(parsed.fragment) == [
        ("mcpProxyToken", "current-token"),
        ("mcpProxyPort", "54321"),
    ]
    assert "old" not in opened_url


@pytest.mark.asyncio
async def test_open_connection_never_opens_a_second_tab_for_one_process(
    proxy_client, monkeypatch
):
    import colab_mcp

    monkeypatch.setattr(colab_mcp, "_connection_attempt_url", None)
    proxy_client.is_connected.side_effect = [False, False, True]
    with patch.object(colab_mcp, "_proxy_client", proxy_client), patch(
        "colab_mcp.webbrowser.open_new"
    ) as open_new:
        first = await colab_mcp.open_colab_browser_connection.fn(
            "https://colab.research.google.com/drive/notebook"
        )
        proxy_client.is_connected.side_effect = None
        proxy_client.is_connected.return_value = False
        second = await colab_mcp.open_colab_browser_connection.fn(
            "https://colab.research.google.com/drive/notebook"
        )

    assert "Connection successful" in first
    assert "No second tab was opened" in second
    open_new.assert_called_once()


@pytest.mark.asyncio
async def test_open_connection_can_prepare_url_for_existing_tab(proxy_client, monkeypatch):
    import colab_mcp

    monkeypatch.setattr(colab_mcp, "_connection_attempt_url", None)
    with patch.object(colab_mcp, "_proxy_client", proxy_client), patch(
        "colab_mcp.webbrowser.open_new"
    ) as open_new:
        result = await colab_mcp.open_colab_browser_connection.fn(
            "https://colab.research.google.com/drive/notebook",
            False,
        )

    assert "Connection URL prepared" in result
    assert "current-token" in result
    assert "port=54321" in result
    assert "WARNING" in result
    open_new.assert_not_called()


@pytest.mark.asyncio
async def test_connection_info_contains_separate_token_port_and_url(proxy_client):
    import colab_mcp

    colab_mcp._last_notebook_url = "https://colab.research.google.com/drive/notebook"
    with patch.object(colab_mcp, "_proxy_client", proxy_client):
        info = await colab_mcp.get_colab_connection_info.fn()
        again = await colab_mcp.get_colab_connection_info.fn()

    assert info["token"] == "current-token"
    assert info["port"] == 54321
    assert "current-token" in info["url"]
    assert info["paste_ready"] == {"token": "current-token", "port": 54321}
    assert again["url"] == info["url"]


@pytest.mark.asyncio
async def test_invalid_connection_url_does_not_open_browser(proxy_client):
    import colab_mcp

    with patch.object(colab_mcp, "_proxy_client", proxy_client), patch(
        "colab_mcp.webbrowser.open_new"
    ) as open_new:
        result = await colab_mcp.open_colab_browser_connection.fn("https://example.com/x")

    assert "Invalid notebook URL" in result
    open_new.assert_not_called()


@pytest.mark.asyncio
async def test_run_code_cell_returns_immediately_and_can_be_polled(proxy_client, monkeypatch):
    import colab_mcp
    from colab_mcp.execution import CodeExecutionRegistry

    registry = CodeExecutionRegistry(max_entries=4, ttl_seconds=60)
    monkeypatch.setattr(colab_mcp, "_execution_registry", registry)
    release_execution = asyncio.Event()

    async def wait_for_release(*_args, **_kwargs):
        await release_execution.wait()
        return "finished"

    with patch.object(colab_mcp, "_proxy_client", proxy_client), patch.object(
        colab_mcp, "_forward_or_stub", new=AsyncMock(side_effect=wait_for_release)
    ):
        proxy_client.is_connected.return_value = True
        started = await colab_mcp.run_code_cell.fn("cell-1")
        assert started["execution_id"]
        assert started["status"] == "running"
        await asyncio.sleep(0)
        assert (
            await colab_mcp.get_code_execution.fn(started["execution_id"])
        )["status"] == "running"
        release_execution.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert (
            await colab_mcp.get_code_execution.fn(started["execution_id"])
        )["status"] == "completed"
    await registry.close()


@pytest.mark.asyncio
async def test_run_code_cell_blocking_uses_browser_handler():
    import colab_mcp

    with patch.object(
        colab_mcp,
        "_forward_or_stub",
        new=AsyncMock(return_value="finished"),
    ) as forward:
        result = await colab_mcp.run_code_cell_blocking.fn("cell-1")

    assert result == "finished"
    forward.assert_awaited_once_with("run_code_cell", {"cellId": "cell-1"})
