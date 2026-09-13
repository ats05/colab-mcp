# Copyright 2026 Atsushi Onozawa.
# Licensed under the Apache License, Version 2.0.

from types import SimpleNamespace

import pytest

from colab_mcp.runtime import (
    DirectColabRuntime,
    DirectRuntimeError,
    DirectRuntimeManager,
    RuntimeTarget,
)


class FakeKernelClient:
    def execute_interactive(self, code, *, output_hook, allow_stdin, timeout):
        assert code == "print('hello')"
        assert allow_stdin is False
        assert timeout > 0
        output_hook(
            {"output_type": "stream", "name": "stdout", "text": "hello\n"}
        )
        return {"content": {"status": "ok"}}


@pytest.mark.asyncio
async def test_direct_runtime_forwards_incremental_kernel_outputs(monkeypatch):
    import jupyter_kernel_client.client

    monkeypatch.setattr(
        jupyter_kernel_client.client,
        "output_hook",
        lambda outputs, message: (outputs.append(message) or [len(outputs) - 1]),
    )
    runtime = DirectColabRuntime(
        RuntimeTarget(endpoint="ep", url="https://runtime", token="token")
    )
    runtime._kernel_client = FakeKernelClient()
    received = []

    outputs = await runtime.execute("print('hello')", received.append)

    assert outputs == [
        {"output_type": "stream", "name": "stdout", "text": "hello\n"}
    ]
    assert received == outputs


@pytest.mark.asyncio
async def test_direct_runtime_manager_selects_single_active_assignment(monkeypatch):
    assignment = SimpleNamespace(
        endpoint="endpoint-1",
        runtime_proxy_info=SimpleNamespace(url="https://runtime", token="token"),
    )

    class FakeColabClient:
        def list_assignments(self):
            return [assignment]

    connected = []

    async def fake_connect(self):
        connected.append(self.target)

    monkeypatch.setattr(DirectColabRuntime, "connect", fake_connect)
    manager = DirectRuntimeManager(FakeColabClient())

    target = await manager.connect()

    assert target == RuntimeTarget("endpoint-1", "https://runtime", "token")
    assert manager.endpoint == "endpoint-1"
    assert connected == [target]


@pytest.mark.asyncio
async def test_direct_runtime_manager_requires_endpoint_when_multiple_are_active():
    assignments = [
        SimpleNamespace(
            endpoint="endpoint-1",
            runtime_proxy_info=SimpleNamespace(url="https://one", token="one"),
        ),
        SimpleNamespace(
            endpoint="endpoint-2",
            runtime_proxy_info=SimpleNamespace(url="https://two", token="two"),
        ),
    ]

    class FakeColabClient:
        def list_assignments(self):
            return assignments

    manager = DirectRuntimeManager(FakeColabClient())

    with pytest.raises(DirectRuntimeError, match="Multiple active Colab runtimes"):
        await manager.connect()
