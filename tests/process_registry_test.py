# Copyright 2026 Atsushi Onozawa.
# Licensed under the Apache License, Version 2.0.

from unittest.mock import patch

from colab_mcp import process_registry


def test_windows_process_info_does_not_share_the_parent_console():
    completed = type(
        "Completed",
        (),
        {
            "stdout": (
                "123|2026-09-06T03:04:05+00:00|"
                "python -m colab_mcp --profile claude"
            )
        },
    )()
    with (
        patch.object(process_registry.sys, "platform", "win32"),
        patch.object(
            process_registry.subprocess, "run", return_value=completed
        ) as run,
    ):
        info = process_registry._windows_process_info(123)

    assert info is not None
    assert run.call_args.kwargs["creationflags"] == 0x08000000


def test_windows_process_iteration_does_not_share_the_parent_console():
    completed = type(
        "Completed",
        (),
        {
            "stdout": (
                "123|2026-09-06T03:04:05+00:00|"
                "python -m colab_mcp --profile claude\n"
            )
        },
    )()
    with (
        patch.object(process_registry.sys, "platform", "win32"),
        patch.object(
            process_registry.subprocess, "run", return_value=completed
        ) as run,
    ):
        infos = list(process_registry._iter_windows_process_info())

    assert len(infos) == 1
    assert run.call_args.kwargs["creationflags"] == 0x08000000


def test_posix_process_info_does_not_receive_windows_creation_flags():
    completed = type("Completed", (), {"stdout": ""})()
    with patch.object(
        process_registry.subprocess, "run", return_value=completed
    ) as run:
        assert process_registry._ps_process_info(123) is None

    assert "creationflags" not in run.call_args.kwargs


def test_command_detection_accepts_supported_launch_forms():
    assert not process_registry.is_colab_mcp_command("uv run colab-mcp")
    assert process_registry.is_colab_mcp_command("/tmp/venv/bin/colab-mcp")
    assert process_registry.is_colab_mcp_command("python /tmp/venv/bin/colab-mcp")
    assert process_registry.is_colab_mcp_command("python -m colab_mcp --profile codex")
    assert not process_registry.is_colab_mcp_command("python -m unrelated")


def test_pid_reuse_is_rejected_by_start_time_and_command():
    entry = process_registry.ServerEntry(
        pid=123,
        port=456,
        started_at=100.0,
        command="python -m colab_mcp --profile claude",
        profile="claude",
    )
    reused = process_registry.ProcessInfo(
        pid=123,
        started_at=1000.0,
        command="python -m unrelated",
    )
    assert process_registry._verified_info(entry, reused) is None


def test_profile_is_checked_before_a_process_can_be_replaced():
    entry = process_registry.ServerEntry(
        pid=123,
        port=456,
        started_at=100.0,
        command="python -m colab_mcp --profile claude",
        profile="claude",
    )
    live = process_registry.ProcessInfo(
        pid=123,
        started_at=100.0,
        command="python -m colab_mcp --profile claude",
    )
    with patch.object(process_registry, "_process_info", return_value=live):
        assert process_registry.list_running(profile="codex", include_unregistered=False) == []
        assert process_registry._verified_info(entry, live) is live
