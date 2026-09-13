# Copyright 2026 Atsushi Onozawa.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Direct, browser-independent access to a Colab Jupyter kernel.

The hosted Colab runtime proxy exposes a Jupyter-compatible endpoint.  This
module deliberately keeps that transport separate from the browser MCP bridge:
the browser is useful for notebook UI operations, but it is not required for
executing Python or receiving incremental kernel output.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, Callable


logger = logging.getLogger(__name__)

OutputCallback = Callable[[dict[str, Any]], None]


class DirectRuntimeError(RuntimeError):
    """Raised when a direct Colab runtime cannot be selected or connected."""


@dataclass(frozen=True)
class RuntimeTarget:
    """The short-lived proxy credentials needed by a Jupyter kernel client."""

    endpoint: str
    url: str
    token: str


class DirectColabRuntime:
    """A small async wrapper around ``jupyter-kernel-client``.

    ``jupyter-kernel-client`` is synchronous and invokes its output hook from
    the reader thread.  All blocking work therefore runs in a worker thread;
    callers receive each normalized output record through ``output_callback``.
    """

    def __init__(self, target: RuntimeTarget):
        self.target = target
        self._kernel_client: Any | None = None
        self._connect_lock = asyncio.Lock()
        self._execute_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        client = self._kernel_client
        if client is None:
            return False
        try:
            return bool(getattr(client, "is_alive", lambda: True)())
        except Exception:
            return False

    def _build_kernel_client(self) -> Any:
        try:
            import jupyter_kernel_client
        except ImportError as exc:  # pragma: no cover - dependency is packaged
            raise DirectRuntimeError(
                "Direct Colab execution requires jupyter-kernel-client."
            ) from exc

        client_kwargs: dict[str, Any] = {
            "subprotocol": jupyter_kernel_client.JupyterSubprotocol.DEFAULT,
            "extra_params": {"colab-runtime-proxy-token": self.target.token},
        }
        headers = {
            "X-Colab-Client-Agent": "colab-mcp",
            "X-Colab-Runtime-Proxy-Token": self.target.token,
        }
        client_type = getattr(jupyter_kernel_client, "ColabKernelClient", None)
        if client_type is not None:
            client = client_type(
                server_url=self.target.url,
                proxy_token=self.target.token,
                client_kwargs=client_kwargs,
                headers=headers,
            )
        else:
            client = jupyter_kernel_client.KernelClient(
                server_url=self.target.url,
                token=self.target.token,
                client_kwargs=client_kwargs,
                headers=headers,
            )

        # Closing the MCP runtime client must not shut down a user-owned Colab
        # kernel.  The official Colab CLI uses the same ownership boundary.
        client._own_kernel = False
        client.start()
        return client

    async def connect(self) -> None:
        async with self._connect_lock:
            if self._kernel_client is not None:
                return
            self._kernel_client = await asyncio.to_thread(self._build_kernel_client)

    def _execute_sync(
        self,
        code: str,
        output_callback: OutputCallback,
        timeout: float,
    ) -> list[dict[str, Any]]:
        client = self._kernel_client
        if client is None:
            raise DirectRuntimeError("The direct Colab runtime is not connected.")

        # The library's default output hook coalesces display updates and
        # returns a final list.  Wrap it so callers see each newly completed
        # output record as soon as it arrives on IOPub.
        from jupyter_kernel_client.client import output_hook as default_output_hook

        outputs: list[dict[str, Any]] = []

        def on_output(message: dict[str, Any]) -> None:
            new_indexes = default_output_hook(outputs, message)
            for index in sorted(new_indexes):
                if index < len(outputs):
                    output_callback(dict(outputs[index]))

        reply = client.execute_interactive(
            code,
            output_hook=on_output,
            allow_stdin=False,
            timeout=timeout,
        )
        reply_content = reply.get("content", {}) if reply else {"status": "error"}
        if reply_content.get("status") == "error" and not any(
            item.get("output_type") == "error" for item in outputs
        ):
            error = {
                "output_type": "error",
                "ename": reply_content.get("ename", "Error"),
                "evalue": reply_content.get("evalue", "Unknown error"),
                "traceback": reply_content.get("traceback", []),
            }
            outputs.append(error)
            output_callback(error)
        return outputs

    async def execute(
        self,
        code: str,
        output_callback: OutputCallback,
        *,
        timeout: float = 24 * 60 * 60,
    ) -> list[dict[str, Any]]:
        await self.connect()

        def callback_from_thread(output: dict[str, Any]) -> None:
            # ``CodeExecutionRegistry.publish`` is thread-safe, but callers
            # may use a coroutine-backed callback in tests or future clients.
            try:
                output_callback(output)
            except Exception:
                logger.exception("Direct runtime output callback failed")

        async with self._execute_lock:
            return await asyncio.to_thread(
                self._execute_sync,
                code,
                callback_from_thread,
                timeout,
            )

    async def close(self) -> None:
        client = self._kernel_client
        self._kernel_client = None
        if client is None:
            return

        def stop() -> None:
            try:
                manager_client = client._manager.client
                manager_client.stop_channels()
                if manager_client.kernel_socket:
                    manager_client.kernel_socket.close()
            except Exception:  # pragma: no cover - cleanup must be best effort
                logger.debug("Error closing direct Colab runtime", exc_info=True)

        await asyncio.to_thread(stop)


class DirectRuntimeManager:
    """Select and own one direct runtime from the OAuth-backed Colab client."""

    def __init__(self, colab_client: Any):
        self.colab_client = colab_client
        self.runtime: DirectColabRuntime | None = None
        self.endpoint: str | None = None

    async def _select_target(self, endpoint: str = "") -> RuntimeTarget:
        assignments = await asyncio.to_thread(self.colab_client.list_assignments)
        assignments = assignments or []
        if endpoint:
            matches = [item for item in assignments if item.endpoint == endpoint]
            if not matches:
                raise DirectRuntimeError(
                    f"No active Colab runtime was found for endpoint '{endpoint}'."
                )
        elif len(assignments) == 1:
            matches = assignments
        elif not assignments:
            raise DirectRuntimeError(
                "No active Colab runtime is available. Start one in Colab or "
                "call change_runtime first."
            )
        else:
            choices = ", ".join(item.endpoint for item in assignments)
            raise DirectRuntimeError(
                "Multiple active Colab runtimes were found. Retry with "
                f"runtime_endpoint set to one of: {choices}."
            )

        info = matches[0].runtime_proxy_info
        return RuntimeTarget(
            endpoint=matches[0].endpoint,
            url=info.url,
            token=info.token,
        )

    async def connect(self, endpoint: str = "") -> RuntimeTarget:
        target = await self._select_target(endpoint)
        if (
            self.runtime is not None
            and self.endpoint == target.endpoint
            and self.runtime.target.url == target.url
            and self.runtime.target.token == target.token
        ):
            return target
        await self.close()
        runtime = DirectColabRuntime(target)
        await runtime.connect()
        self.runtime = runtime
        self.endpoint = target.endpoint
        return target

    async def attach_target(self, target: RuntimeTarget) -> None:
        await self.close()
        runtime = DirectColabRuntime(target)
        await runtime.connect()
        self.runtime = runtime
        self.endpoint = target.endpoint

    async def close(self) -> None:
        if self.runtime is not None:
            await self.runtime.close()
        self.runtime = None
        self.endpoint = None

    @property
    def connected(self) -> bool:
        return self.runtime is not None and self.runtime.connected
