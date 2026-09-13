# Copyright 2026 Atsushi Onozawa.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Bounded background execution tracking for long-running Colab cells."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
import threading
import time
from typing import Any, Awaitable, Callable
import uuid


ExecutionPublisher = Callable[[dict], None]
ExecutionRunner = Callable[[ExecutionPublisher], Awaitable[str]]
MAX_RETAINED_EVENTS = 256


@dataclass
class CodeExecution:
    execution_id: str
    cell_id: str
    status: str = "running"
    result: str | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    events: list[dict[str, Any]] = field(default_factory=list, repr=False)
    next_event_id: int = field(default=1, repr=False)
    events_changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    event_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "execution_id": self.execution_id,
            "cell_id": self.cell_id,
            "status": self.status,
            "started_at": self.started_at,
        }
        if self.finished_at is not None:
            value["finished_at"] = self.finished_at
        if self.result is not None:
            value["result"] = self.result
        if self.error is not None:
            value["error"] = self.error
        value["next_cursor"] = self.next_event_id - 1
        return value


class CodeExecutionRegistry:
    """Track local background tasks without leaking them indefinitely.

    The registry never claims that cancelling a local asyncio task cancels the
    execution already submitted to Colab. There is deliberately no public
    cancel operation. On server shutdown tasks are cancelled only to release
    local resources; Colab may continue and its output remains recoverable via
    ``get_cells`` after a later handoff/reconnect.
    """

    def __init__(self, *, max_entries: int = 64, ttl_seconds: float = 24 * 60 * 60):
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, CodeExecution] = OrderedDict()
        self._closed = False

    def _prune(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        expired = [
            execution_id
            for execution_id, execution in self._entries.items()
            if execution.finished_at is not None
            and now - execution.finished_at >= self.ttl_seconds
        ]
        for execution_id in expired:
            self._entries.pop(execution_id, None)

        # Keep memory bounded even if a client starts cells continuously. A
        # running task is never evicted: callers must be able to inspect its
        # eventual failure/completion and shutdown must still be able to await
        # it. Terminal entries are the only safe eviction candidates.
        while len(self._entries) > self.max_entries:
            terminal_id = next(
                (
                    execution_id
                    for execution_id, execution in self._entries.items()
                    if execution.status != "running"
                ),
                None,
            )
            if terminal_id is None:
                break
            self._entries.pop(terminal_id, None)

    async def start(self, cell_id: str, runner: ExecutionRunner) -> dict[str, object]:
        if self._closed:
            raise RuntimeError("execution registry is closed")
        self._prune()
        if len(self._entries) >= self.max_entries and all(
            execution.status == "running" for execution in self._entries.values()
        ):
            raise RuntimeError(
                f"background execution limit reached ({self.max_entries} running cell(s)); "
                "wait for one to complete before starting another"
            )
        if len(self._entries) >= self.max_entries:
            # A terminal item may have been retained at a newer insertion
            # position; evict the oldest terminal item before adding.
            terminal_id = next(
                (
                    execution_id
                    for execution_id, execution in self._entries.items()
                    if execution.status != "running"
                ),
                None,
            )
            if terminal_id is not None:
                self._entries.pop(terminal_id, None)
            else:  # defensive; the all-running branch above should catch this
                raise RuntimeError("background execution limit reached")
        execution = CodeExecution(
            execution_id=uuid.uuid4().hex,
            cell_id=cell_id,
            loop=asyncio.get_running_loop(),
        )
        self._entries[execution.execution_id] = execution
        execution.task = asyncio.create_task(self._run(execution, runner))
        # A done callback consumes any unexpected task exception. _run catches
        # runner errors itself, but this protects the server if its own code
        # changes later.
        execution.task.add_done_callback(self._consume_task_exception)
        self._prune()
        return execution.as_dict()

    async def _run(self, execution: CodeExecution, runner: ExecutionRunner) -> None:
        try:
            execution.result = await runner(
                lambda event: self.publish(execution.execution_id, event)
            )
            execution.status = "completed"
        except asyncio.CancelledError:
            execution.status = "failed"
            execution.error = "Local execution task stopped during MCP server shutdown."
            raise
        except Exception as exc:  # noqa: BLE001 - state must be observable by caller
            execution.status = "failed"
            execution.error = str(exc) or exc.__class__.__name__
        finally:
            execution.finished_at = time.time()
            execution.events_changed.set()

    def publish(self, execution_id: str, event: dict[str, Any]) -> None:
        """Append one output event from the kernel reader thread.

        The direct Jupyter client reads IOPub on a worker thread, so this
        method schedules the bounded list update on the owning event loop when
        called from the kernel reader thread.
        """
        execution = self._entries.get(execution_id)
        if execution is None:
            return
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is execution.loop:
            self._append_event(execution, event)
            execution.events_changed.set()
        elif execution.loop is not None:
            self._append_event(execution, event)
            execution.loop.call_soon_threadsafe(execution.events_changed.set)

    @staticmethod
    def _append_event(execution: CodeExecution, event: dict[str, Any]) -> None:
        with execution.event_lock:
            item = dict(event)
            item["event_id"] = execution.next_event_id
            execution.next_event_id += 1
            execution.events.append(item)
            if len(execution.events) > MAX_RETAINED_EVENTS:
                del execution.events[: len(execution.events) - MAX_RETAINED_EVENTS]

    @staticmethod
    def _consume_task_exception(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            # ``_run`` handles expected exceptions. Calling result here makes
            # an unexpected programming error visible without an unhandled
            # task warning.
            try:
                task.result()
            except Exception:
                pass

    def get(self, execution_id: str) -> dict[str, object]:
        self._prune()
        execution = self._entries.get(execution_id)
        if execution is None:
            return {
                "execution_id": execution_id,
                "status": "unknown",
                "error": "Execution ID is unknown or has expired from the local registry.",
            }
        return execution.as_dict()

    async def get_events(
        self,
        execution_id: str,
        *,
        cursor: int = 0,
        wait_seconds: float = 0.0,
    ) -> dict[str, object]:
        """Return output events after ``cursor``, optionally long-polling."""
        if cursor < 0:
            raise ValueError("cursor must be non-negative")
        if wait_seconds < 0:
            raise ValueError("wait_seconds must be non-negative")
        self._prune()
        execution = self._entries.get(execution_id)
        if execution is None:
            return {
                "execution_id": execution_id,
                "status": "unknown",
                "events": [],
                "next_cursor": cursor,
                "done": True,
                "error": "Execution ID is unknown or has expired from the local registry.",
            }

        deadline = time.monotonic() + wait_seconds
        while True:
            with execution.event_lock:
                events = [
                    event for event in execution.events if event["event_id"] > cursor
                ]
                events_retained = list(execution.events)
            if events or execution.status != "running" or wait_seconds == 0:
                value = execution.as_dict()
                value["events"] = events
                value["done"] = execution.status != "running"
                if events_retained and cursor < events_retained[0]["event_id"] - 1:
                    value["events_truncated"] = True
                    value["oldest_cursor"] = events_retained[0]["event_id"] - 1
                return value
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                value = execution.as_dict()
                value["events"] = []
                value["done"] = execution.status != "running"
                return value
            execution.events_changed.clear()
            # Re-check after clearing to avoid losing an event that arrived
            # between the first inspection and clear().
            with execution.event_lock:
                has_new_events = any(
                    event["event_id"] > cursor for event in execution.events
                )
            if has_new_events:
                continue
            try:
                await asyncio.wait_for(execution.events_changed.wait(), remaining)
            except asyncio.TimeoutError:
                value = execution.as_dict()
                value["events"] = []
                value["done"] = execution.status != "running"
                return value

    def list(self) -> list[dict[str, object]]:
        self._prune()
        return [execution.as_dict() for execution in self._entries.values()]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = [
            execution.task
            for execution in self._entries.values()
            if execution.task is not None and not execution.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._entries.clear()
