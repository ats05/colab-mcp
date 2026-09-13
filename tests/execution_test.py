# Copyright 2026 Atsushi Onozawa.
# Licensed under the Apache License, Version 2.0.

import asyncio

import pytest

from colab_mcp.execution import CodeExecutionRegistry


@pytest.mark.asyncio
async def test_background_execution_returns_id_then_completed_result():
    registry = CodeExecutionRegistry(max_entries=4, ttl_seconds=60)
    gate = asyncio.Event()

    async def runner(_publish):
        await gate.wait()
        return "cell output"

    started = await registry.start("cell-1", runner)
    assert started["status"] == "running"
    assert started["execution_id"]
    assert registry.get(started["execution_id"])["status"] == "running"

    gate.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    completed = registry.get(started["execution_id"])
    assert completed["status"] == "completed"
    assert completed["result"] == "cell output"
    await registry.close()


@pytest.mark.asyncio
async def test_background_execution_retains_incremental_output_events():
    registry = CodeExecutionRegistry(max_entries=4, ttl_seconds=60)
    release = asyncio.Event()

    async def runner(publish):
        publish({"output_type": "stream", "name": "stdout", "text": "first\n"})
        await release.wait()
        publish({"output_type": "stream", "name": "stdout", "text": "second\n"})
        return "done"

    started = await registry.start("cell-1", runner)
    await asyncio.sleep(0)
    first = await registry.get_events(started["execution_id"], cursor=0)
    assert [event["text"] for event in first["events"]] == ["first\n"]
    assert first["next_cursor"] == 1

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    second = await registry.get_events(started["execution_id"], cursor=1)
    assert [event["text"] for event in second["events"]] == ["second\n"]
    assert second["done"] is True
    await registry.close()


@pytest.mark.asyncio
async def test_background_execution_long_poll_wakes_on_output():
    registry = CodeExecutionRegistry(max_entries=4, ttl_seconds=60)
    release = asyncio.Event()

    async def runner(publish):
        await release.wait()
        publish({"output_type": "stream", "name": "stdout", "text": "ready\n"})
        return "done"

    started = await registry.start("cell-1", runner)
    pending = asyncio.create_task(
        registry.get_events(started["execution_id"], cursor=0, wait_seconds=1)
    )
    await asyncio.sleep(0)
    release.set()
    result = await pending
    assert result["events"][0]["text"] == "ready\n"
    await registry.close()


@pytest.mark.asyncio
async def test_background_execution_records_failure():
    registry = CodeExecutionRegistry(max_entries=4, ttl_seconds=60)

    async def runner(_publish):
        raise RuntimeError("browser disconnected")

    started = await registry.start("cell-1", runner)
    await asyncio.sleep(0)
    result = registry.get(started["execution_id"])
    assert result["status"] == "failed"
    assert result["error"] == "browser disconnected"
    await registry.close()


@pytest.mark.asyncio
async def test_running_tasks_are_not_evicted_and_limit_is_explicit():
    registry = CodeExecutionRegistry(max_entries=1, ttl_seconds=60)
    gate = asyncio.Event()

    async def runner(_publish):
        await gate.wait()
        return "done"

    first = await registry.start("cell-1", runner)
    with pytest.raises(RuntimeError, match="limit reached"):
        await registry.start("cell-2", runner)
    assert registry.get(first["execution_id"])["status"] == "running"
    gate.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await registry.close()


@pytest.mark.asyncio
async def test_close_cleans_up_local_tasks_without_public_cancel():
    registry = CodeExecutionRegistry(max_entries=2, ttl_seconds=60)
    gate = asyncio.Event()

    async def runner(_publish):
        await gate.wait()
        return "never reached"

    started = await registry.start("cell-1", runner)
    await registry.close()
    assert registry.list() == []
    assert registry.get(started["execution_id"])["status"] == "unknown"
