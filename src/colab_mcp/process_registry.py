# Copyright 2026 Sebastian Gil (fork).
# Added to this fork by Sebastian Gil Pinzon, 2026.
# Modified by Atsushi Onozawa, 2026.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Safe discovery and lifecycle management for local colab-mcp processes.

The registry is useful for diagnostics, but it is not an authority for killing
processes: a registry file can outlive a crash, and operating systems can reuse
PIDs. Every destructive operation therefore verifies the live process command
line and start time first. Discovery also scans the OS process table, so a
server started by another MCP client (and not yet registered) is visible.

Normal startup never terminates a peer. ``replace_existing`` and
``cleanup_stale(kill=True)`` are explicit operations and can be scoped to a
profile. A profile lets Claude Code and Codex use separate process groups;
neither client can accidentally replace the other group's server.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import datetime as _datetime
import json
import logging
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from typing import Callable, Iterable, List
import uuid


logger = logging.getLogger(__name__)


START_TIME_TOLERANCE_SECONDS = 10.0
_DIAGNOSTIC_FLAGS = {"--list-running", "--kill-stale", "--stop-pid"}


@dataclass(frozen=True)
class ProcessInfo:
    """Live identity data obtained from the operating system."""

    pid: int
    started_at: float
    command: str


@dataclass
class ServerEntry:
    pid: int
    port: int
    started_at: float  # epoch seconds
    host: str = "127.0.0.1"
    profile: str = "default"
    # These fields are deliberately non-secret. The MCP token is never stored.
    command: str = ""
    instance_id: str = ""


def _registry_dir() -> Path:
    """Cross-platform location for the registry file."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "colab-mcp"
    return Path(os.path.expanduser("~")) / ".colab-mcp"


def _registry_path() -> Path:
    return _registry_dir() / "registry.json"


def _parse_entry(raw: object) -> ServerEntry | None:
    if not isinstance(raw, dict):
        return None
    try:
        # Explicit fields keep old registries readable and ignore unknown
        # fields from future versions rather than failing the whole file.
        return ServerEntry(
            pid=int(raw["pid"]),
            port=int(raw["port"]),
            started_at=float(raw["started_at"]),
            host=str(raw.get("host") or "127.0.0.1"),
            profile=str(raw.get("profile") or "default"),
            command=str(raw.get("command") or ""),
            instance_id=str(raw.get("instance_id") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _load_registry() -> List[ServerEntry]:
    p = _registry_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        raw_entries = data.get("servers", []) if isinstance(data, dict) else []
        return [entry for raw in raw_entries if (entry := _parse_entry(raw)) is not None]
    except (OSError, json.JSONDecodeError, TypeError, AttributeError) as exc:
        logger.warning("Registry at %s is unreadable (%s); ignoring it.", p, exc)
        return []


@contextmanager
def _registry_lock():
    """Serialize registry read/modify/write operations where possible."""
    p = _registry_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        lock_file = p.with_name(p.name + ".lock")
        handle = lock_file.open("a+")
    except OSError:
        # A read-only home should not stop the MCP server from starting. The
        # save operation still reports its own error to the caller, but the
        # context manager itself has exactly one yield.
        yield
        return

    locked = False
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            locked = True
        else:
            # Atomic replace still prevents partial JSON on Windows. A
            # best-effort lock is preferable when msvcrt is available.
            try:
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
            except (ImportError, OSError):
                pass
        yield
    finally:
        if locked and os.name == "posix":
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif locked:
            try:
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except (ImportError, OSError):
                pass
        handle.close()


def _save_registry_unlocked(entries: List[ServerEntry]) -> None:
    try:
        d = _registry_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = _registry_path()
        payload = {"version": 2, "servers": [asdict(e) for e in entries]}
        # Write beside the destination and atomically replace it so another
        # process never observes half-written JSON after a crash.
        fd, temporary = tempfile.mkstemp(prefix="registry.", suffix=".tmp", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, p)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    except OSError as exc:
        # Registry persistence is a diagnostic aid, not a prerequisite for the
        # MCP bridge. Keep startup/cleanup usable from read-only homes and make
        # the limitation visible without exposing any token.
        logger.warning("Could not persist process registry: %s", exc)


def _save_registry(entries: List[ServerEntry]) -> None:
    with _registry_lock():
        _save_registry_unlocked(entries)


def _update_registry(mutator: Callable[[List[ServerEntry]], List[ServerEntry]]) -> List[ServerEntry]:
    with _registry_lock():
        entries = _load_registry()
        updated = mutator(entries)
        _save_registry_unlocked(updated)
        return updated


def _is_process_alive(pid: int) -> bool:
    """Cross-platform PID existence check using stdlib only."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # POSIX permission errors mean the process exists. On Windows an
        # inaccessible PID is safer to treat as not ours.
        return os.name != "nt"
    except OSError:
        return False


def _parse_ps_start(value: str) -> float | None:
    value = value.strip()
    for fmt in ("%a %b %d %H:%M:%S %Y", "%c"):
        try:
            return _datetime.datetime.strptime(value, fmt).timestamp()
        except ValueError:
            continue
    return None


def _linux_process_info(pid: int) -> ProcessInfo | None:
    proc = Path("/proc") / str(pid)
    try:
        command_bytes = (proc / "cmdline").read_bytes()
        command = " ".join(
            part
            for part in command_bytes.decode(errors="replace").split("\0")
            if part
        )
        if not command:
            command = (proc / "comm").read_text(encoding="utf-8", errors="replace").strip()

        stat = (proc / "stat").read_text(encoding="utf-8")
        closing_paren = stat.rfind(")")
        if closing_paren < 0:
            return None
        # ``starttime`` is field 22 in procfs. After the comm field, the list
        # starts at field 3, therefore index 19.
        fields_after_comm = stat[closing_paren + 2 :].split()
        start_ticks = int(fields_after_comm[19])
        clock_ticks = os.sysconf("SC_CLK_TCK")
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        started_at = time.time() - uptime + start_ticks / clock_ticks
        return ProcessInfo(pid=pid, started_at=started_at, command=command)
    except (OSError, ValueError, IndexError, UnicodeError):
        return None


def _ps_process_info(pid: int) -> ProcessInfo | None:
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart=,command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = completed.stdout.strip()
    if not line:
        return None
    match = re.match(r"^(.{24})\s+(.*)$", line)
    if not match:
        return None
    started_at = _parse_ps_start(match.group(1))
    if started_at is None:
        return None
    return ProcessInfo(pid=pid, started_at=started_at, command=match.group(2).strip())


def _windows_process_info(pid: int) -> ProcessInfo | None:
    # PowerShell exposes both fields tasklist omits. The PID is an integer, so
    # no user input is interpolated into the command expression.
    script = (
        "$p=Get-CimInstance Win32_Process -Filter 'ProcessId = %d'; "
        "if ($p) { Write-Output ($p.ProcessId.ToString() + '|' + "
        "$p.CreationDate.ToUniversalTime().ToString('o') + '|' + $p.CommandLine) }"
    ) % pid
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    parts = completed.stdout.strip().split("|", 2)
    if len(parts) != 3:
        return None
    try:
        started_at = _datetime.datetime.fromisoformat(parts[1].replace("Z", "+00:00")).timestamp()
        return ProcessInfo(int(parts[0]), started_at, parts[2].strip())
    except (ValueError, OverflowError):
        return None


def _iter_windows_process_info() -> Iterable[ProcessInfo]:
    """Read the Windows process table with one PowerShell invocation."""
    script = (
        "Get-CimInstance Win32_Process | ForEach-Object { "
        "Write-Output ($_.ProcessId.ToString() + '|' + "
        "$_.CreationDate.ToUniversalTime().ToString('o') + '|' + $_.CommandLine) }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return
    for line in completed.stdout.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) != 3:
            continue
        try:
            started_at = _datetime.datetime.fromisoformat(
                parts[1].replace("Z", "+00:00")
            ).timestamp()
            yield ProcessInfo(int(parts[0]), started_at, parts[2].strip())
        except (ValueError, OverflowError):
            continue


def _process_info(pid: int) -> ProcessInfo | None:
    """Read start time and command line for one PID."""
    if pid <= 0:
        return None
    if sys.platform.startswith("linux") and Path("/proc").exists():
        return _linux_process_info(pid)
    if sys.platform == "win32":
        return _windows_process_info(pid)
    return _ps_process_info(pid)


def _iter_process_info() -> Iterable[ProcessInfo]:
    """Enumerate processes without an optional psutil dependency."""
    if sys.platform.startswith("linux") and Path("/proc").exists():
        try:
            pids = sorted(int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit())
        except OSError:
            pids = []
        for pid in pids:
            info = _process_info(pid)
            if info is not None:
                yield info
        return

    if sys.platform == "win32":
        # Do not launch PowerShell once per PID. One process-table query is
        # both faster and less surprising on a developer workstation.
        yield from _iter_windows_process_info()
        return

    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,lstart=,command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return
    for line in completed.stdout.splitlines():
        match = re.match(r"^\s*(\d+)\s+(.{24})\s+(.*)$", line)
        if not match:
            continue
        started_at = _parse_ps_start(match.group(2))
        if started_at is not None:
            yield ProcessInfo(int(match.group(1)), started_at, match.group(3).strip())


def _command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=(os.name != "nt"))
    except ValueError:
        return command.split()


def is_colab_mcp_command(command: str) -> bool:
    """Return whether a command line unambiguously launches colab-mcp."""
    tokens = _command_tokens(command)
    lowered = [token.lower() for token in tokens]
    if not lowered:
        return False
    # ``uv run colab-mcp`` / ``uvx colab-mcp`` leaves a launcher process in
    # the table alongside the actual console-script child. Treating both as
    # servers would make replace/stop ambiguous and could signal the launcher
    # after the child already exited. The child has the console script as its
    # executable (or uses ``python -m``), handled below.
    first_basename = Path(lowered[0].replace("\\", "/")).name
    if first_basename in {"uv", "uvx", "bash", "sh", "zsh", "fish"}:
        return False
    for index, token in enumerate(lowered):
        basename = Path(token.replace("\\", "/")).name
        if basename in {"colab-mcp", "colab_mcp"}:
            # A script path passed to an interpreter is a real server. A bare
            # token after an ``uv`` launcher was excluded above.
            if index == 0 or (
                index == 1
                and first_basename in {"python", "python3", "pypy", "pypy3"}
            ):
                return True
        if token == "-m" and index + 1 < len(lowered) and lowered[index + 1] in {
            "colab_mcp",
            "colab_mcp.__main__",
        }:
            return True
    return False


def _is_server_command(command: str) -> bool:
    if not is_colab_mcp_command(command):
        return False
    return not any(flag in _command_tokens(command) for flag in _DIAGNOSTIC_FLAGS)


def profile_from_command(command: str) -> str:
    """Extract a non-secret ``--profile`` value, defaulting to ``default``."""
    tokens = _command_tokens(command)
    for index, token in enumerate(tokens):
        if token == "--profile" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("--profile="):
            return token.split("=", 1)[1]
    return "default"


def _commands_match(expected: str, actual: str) -> bool:
    if not expected or not actual:
        return True
    expected_tokens = _command_tokens(expected)
    actual_tokens = _command_tokens(actual)
    if os.name == "nt":
        expected_tokens = [token.lower() for token in expected_tokens]
        actual_tokens = [token.lower() for token in actual_tokens]
    return expected_tokens == actual_tokens


def _verified_info(entry: ServerEntry, info: ProcessInfo | None = None) -> ProcessInfo | None:
    """Verify an entry still refers to the same colab-mcp process."""
    info = info or _process_info(entry.pid)
    if info is None or not _is_server_command(info.command):
        return None
    if entry.started_at and info.started_at:
        if abs(entry.started_at - info.started_at) > START_TIME_TOLERANCE_SECONDS:
            return None
    if entry.command and not _commands_match(entry.command, info.command):
        return None
    if entry.profile and profile_from_command(info.command) != (entry.profile or "default"):
        return None
    return info


def _entry_from_info(info: ProcessInfo) -> ServerEntry:
    return ServerEntry(
        pid=info.pid,
        port=0,
        started_at=info.started_at,
        host="",
        profile=profile_from_command(info.command),
        command=info.command,
    )


def _current_command() -> str:
    info = _process_info(os.getpid())
    if info is not None and info.command:
        return info.command
    return shlex.join([sys.executable, *sys.argv])


def _entry_matches_profile(entry: ServerEntry, profile: str | None) -> bool:
    return profile is None or (entry.profile or "default") == profile


def list_running(
    *, profile: str | None = None, include_unregistered: bool = True
) -> List[ServerEntry]:
    """List verified servers from both the registry and the OS process table."""
    entries: list[ServerEntry] = []
    known_pids: set[int] = set()
    for entry in _load_registry():
        info = _verified_info(entry)
        if info is None or not _entry_matches_profile(entry, profile):
            continue
        entries.append(entry)
        known_pids.add(entry.pid)

    if include_unregistered:
        for info in _iter_process_info():
            if info.pid == os.getpid() or info.pid in known_pids:
                continue
            if not _is_server_command(info.command):
                continue
            entry = _entry_from_info(info)
            if _entry_matches_profile(entry, profile):
                entries.append(entry)
                known_pids.add(info.pid)

    return sorted(entries, key=lambda entry: (entry.profile, entry.pid))


def prune_dead() -> int:
    """Remove dead, PID-reused, or no-longer-colab-mcp registry entries."""
    entries = _load_registry()
    alive = [entry for entry in entries if _verified_info(entry) is not None]
    dead_count = len(entries) - len(alive)
    if dead_count:
        _save_registry(alive)
        logger.info("Pruned %d invalid colab-mcp entries from registry", dead_count)
    return dead_count


def _terminate_verified(
    entry: ServerEntry, info: ProcessInfo, *, force: bool = False
) -> bool:
    """Terminate exactly the process represented by ``entry``."""
    if entry.pid == os.getpid():
        return False
    try:
        os.kill(
            entry.pid,
            signal.SIGKILL if force and os.name != "nt" else signal.SIGTERM,
        )
    except ProcessLookupError:
        return True
    except PermissionError:
        logger.warning("Permission denied while stopping pid=%s", entry.pid)
        return False
    except OSError as exc:
        logger.warning("Failed to signal pid=%s: %s", entry.pid, exc)
        return False

    # Never send a second signal to a PID that has already been reused.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        current = _process_info(entry.pid)
        if current is None:
            return True
        if _verified_info(entry, current) is None:
            # The original process exited and the PID now belongs to another
            # command. Treat the requested target as successfully gone.
            return True
        time.sleep(0.1)

    if not force:
        return _terminate_verified(entry, info, force=True)
    return _process_info(entry.pid) is None


def cleanup_stale(
    *,
    kill: bool = True,
    profile: str | None = "default",
    include_unregistered: bool = True,
) -> List[ServerEntry]:
    """Remove stale entries and optionally terminate verified peers.

    ``kill=True`` is intentionally only called by explicit CLI actions. The
    default profile scope protects a separately configured Claude/Codex
    profile; pass ``profile=None`` only for an explicit all-profile operation.
    """
    registry_entries = _load_registry()
    removed: list[ServerEntry] = []
    retained: list[ServerEntry] = []
    seen: set[int] = set()

    for entry in registry_entries:
        if not _entry_matches_profile(entry, profile):
            retained.append(entry)
            continue
        info = _verified_info(entry)
        if info is None:
            removed.append(entry)
            continue
        seen.add(entry.pid)
        if kill and _terminate_verified(entry, info):
            removed.append(entry)
        else:
            retained.append(entry)

    if include_unregistered and kill:
        for info in _iter_process_info():
            if (
                info.pid == os.getpid()
                or info.pid in seen
                or not _is_server_command(info.command)
            ):
                continue
            entry = _entry_from_info(info)
            if not _entry_matches_profile(entry, profile):
                continue
            if _terminate_verified(entry, info):
                removed.append(entry)

    _save_registry(retained)
    return removed


def replace_existing(profile: str = "default") -> List[ServerEntry]:
    """Explicitly stop verified peers in one profile before a new startup."""
    return cleanup_stale(kill=True, profile=profile, include_unregistered=True)


def stop(pid: int, *, profile: str | None = "default") -> ServerEntry | None:
    """Explicitly stop one verified process, returning its target entry."""
    candidates = [entry for entry in _load_registry() if entry.pid == pid]
    entry = candidates[0] if candidates else None
    info = _process_info(pid)
    if info is None or not _is_server_command(info.command):
        return None
    if entry is None:
        entry = _entry_from_info(info)
    if not _entry_matches_profile(entry, profile) or _verified_info(entry, info) is None:
        return None
    if not _terminate_verified(entry, info):
        return None
    _update_registry(lambda entries: [e for e in entries if e.pid != pid])
    return entry


def register(port: int, host: str = "127.0.0.1", profile: str = "default") -> ServerEntry:
    """Register this process without touching any peer process."""
    info = _process_info(os.getpid())
    command = info.command if info is not None else _current_command()
    started_at = info.started_at if info is not None else time.time()
    entry = ServerEntry(
        pid=os.getpid(),
        port=int(port),
        started_at=started_at,
        host=host,
        profile=profile or "default",
        command=command,
        instance_id=uuid.uuid4().hex,
    )

    _update_registry(
        lambda entries: [existing for existing in entries if existing.pid != entry.pid]
        + [entry]
    )
    return entry


def unregister(
    pid: int | None = None,
    *,
    started_at: float | None = None,
    instance_id: str | None = None,
) -> None:
    """Remove this process's entry without deleting a reused-PID entry."""
    pid = os.getpid() if pid is None else pid

    def remove_matching(entries: list[ServerEntry]) -> list[ServerEntry]:
        result = []
        for entry in entries:
            if entry.pid != pid:
                result.append(entry)
                continue
            if instance_id and entry.instance_id != instance_id:
                result.append(entry)
                continue
            if (
                started_at is not None
                and abs(entry.started_at - started_at) > START_TIME_TOLERANCE_SECONDS
            ):
                result.append(entry)
        return result

    _update_registry(remove_matching)
