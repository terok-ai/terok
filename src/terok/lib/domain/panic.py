# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Destructive emergency shutdown, independent of task metadata and telemetry.

Container kills, network quarantine, supervisor kills, vault hard-lock and the
restart interlock are independent best-effort actions. No failed or hung action
may postpone another. Containers remain on disk; recovery requires the escrowed
vault passphrase and an explicit clearing of the panic interlock.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from terok.lib.integrations.sandbox import PodmanRuntime, RuntimeContainer

from ..core.paths import core_state_dir

_LOCK_FILENAME = "panic.lock"
_PANIC_TIMEOUT_S = 15
_RUNTIME_TIMEOUT_S = 10
_DOSSIER_ANNOTATION = "dossier.meta_path"
_SHIELD_ANNOTATION = "terok.shield.state_dir"
_DOSSIER_SUFFIX = "_dossier.json"


@dataclass
class PanicResult:
    """Completed actions and explicit failures, including deadline expiry."""

    shields_raised: list[str] = field(default_factory=list)
    shield_errors: list[tuple[str, str]] = field(default_factory=list)
    supervisors_killed: list[str] = field(default_factory=list)
    supervisor_errors: list[tuple[str, str]] = field(default_factory=list)
    vault_stopped: bool = False
    vault_error: str | None = None
    containers_stopped: list[str] = field(default_factory=list)
    container_stop_errors: list[tuple[str, str]] = field(default_factory=list)
    discovery_error: str | None = None
    lock_error: str | None = None
    total_running: int = 0

    @property
    def has_errors(self) -> bool:
        """Return whether any action failed or did not finish in time."""
        return bool(
            self.shield_errors
            or self.supervisor_errors
            or self.vault_error
            or self.container_stop_errors
            or self.discovery_error
            or self.lock_error
        )


def execute_panic() -> PanicResult:
    """Immediately attempt every shutdown action, within one overall deadline.

    Only host-created runtime annotations identify managed containers. Neither
    task status, dossier contents, project configuration nor supervisor sidecar
    files are prerequisites for killing a container. Workers are daemon threads:
    a wedged library call cannot hold this command or Python shutdown hostage.
    Only this collecting thread changes the returned result.
    """
    result = PanicResult()
    runtime = PodmanRuntime()
    deadline = time.monotonic() + _PANIC_TIMEOUT_S
    completed: Queue[tuple[str, str, Any, str | None]] = Queue()
    pending: set[tuple[str, str]] = set()

    def start(kind: str, label: str, action: Callable[[], Any]) -> None:
        """Start one independent action and deliver its outcome to the collector."""
        pending.add((kind, label))

        def run() -> None:
            try:
                completed.put((kind, label, action(), None))
            except (Exception, SystemExit) as exc:
                completed.put((kind, label, None, str(exc)))

        try:
            threading.Thread(target=run, daemon=True, name=f"panic-{kind}-{label}").start()
        except (RuntimeError, OSError) as exc:
            pending.remove((kind, label))
            _record_failure(result, kind, label, str(exc))

    start("lock", "", _write_panic_lock)
    start("supervisors", "", _kill_supervisors)
    start("vault", "", _stop_vault)
    start("discovery", "", lambda: _discover_targets(runtime))
    while pending and time.monotonic() < deadline:
        try:
            kind, label, value, error = completed.get(timeout=max(0, deadline - time.monotonic()))
        except Empty:
            break
        pending.remove((kind, label))
        if error is not None:
            _record_failure(result, kind, label, error)
        elif kind == "discovery":
            result.total_running = len(value)
            for target in value:
                if time.monotonic() >= deadline:
                    _record_failure(result, "kill", target.name, "deadline expired before kill")
                    continue
                start(
                    "kill",
                    target.name,
                    partial(runtime.force_stop, target.id, timeout=_RUNTIME_TIMEOUT_S),
                )
                if _SHIELD_ANNOTATION in target.annotations:
                    start("shield", target.name, partial(_raise_shield, target))
        elif kind == "supervisors":
            for container_id, error in value:
                if error:
                    result.supervisor_errors.append((container_id, error))
                else:
                    result.supervisors_killed.append(container_id)
        elif kind == "vault":
            result.vault_stopped = True
        elif kind == "kill":
            result.containers_stopped.append(label)
        elif kind == "shield":
            result.shields_raised.append(label)
    for kind, label in sorted(pending):
        _record_failure(result, kind, label, f"timed out after {_PANIC_TIMEOUT_S}s")
    return result


def _discover_targets(runtime: PodmanRuntime) -> list[RuntimeContainer]:
    """Find live managed containers without opening any task-owned files."""
    root = core_state_dir() / "projects"
    targets = []
    for container in runtime.inventory(timeout=_RUNTIME_TIMEOUT_S):
        if container.state not in {"running", "paused", "stopping", "restarting"}:
            continue
        path = Path(container.annotations.get(_DOSSIER_ANNOTATION, ""))
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            continue
        if (
            len(parts) == 3
            and parts[0] != ".."
            and parts[1] == "tasks"
            and parts[2].endswith(_DOSSIER_SUFFIX)
            and parts[2] != _DOSSIER_SUFFIX
        ):
            targets.append(container)
    return targets


def _raise_shield(target: RuntimeContainer) -> None:
    """Attempt network blackout independently of the immediate container kill."""
    from terok.lib.integrations.sandbox import ShieldManager

    state_dir = Path(target.annotations[_SHIELD_ANNOTATION])
    if not state_dir.is_absolute() or state_dir.name != "shield":
        raise ValueError(f"Invalid shield state directory: {state_dir}")
    ShieldManager(state_dir.parent).quarantine(target.id)


def _kill_supervisors() -> list[tuple[str, str | None]]:
    """Kill host-side vault, gate and signer services, including Unix-socket access."""
    from terok.lib.integrations.sandbox import kill_all_supervisors

    return kill_all_supervisors()


def _stop_vault() -> None:
    """Destroy all cached and persistent passphrase tiers without confirmation."""
    from ..core.config import make_sandbox_config
    from ..integrations.sandbox import purge_passphrase_tiers

    purge_passphrase_tiers(make_sandbox_config())


def _record_failure(result: PanicResult, kind: str, label: str, error: str) -> None:
    """Keep every independent failure visible in the final report."""
    match kind:
        case "shield":
            result.shield_errors.append((label, error))
        case "kill":
            result.container_stop_errors.append((label, error))
        case "supervisors":
            result.supervisor_errors.append(("*", error))
        case "vault":
            result.vault_error = error
        case "discovery":
            result.discovery_error = error
        case "lock":
            result.lock_error = error


def is_panicked() -> bool:
    """Return whether the restart interlock exists."""
    return (core_state_dir() / _LOCK_FILENAME).is_file()


def clear_panic_lock() -> None:
    """Remove the restart interlock without restarting containers or services."""
    (core_state_dir() / _LOCK_FILENAME).unlink(missing_ok=True)


def _write_panic_lock() -> None:
    """Prevent subsequent task starts until the operator explicitly clears panic."""
    path = core_state_dir() / _LOCK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(UTC).isoformat() + "\n")


def format_panic_report(result: PanicResult) -> str:
    """Summarize completed emergency actions without claiming failed actions worked."""
    lines = [
        f"Containers found: {result.total_running}",
        f"Containers killed: {len(result.containers_stopped)}",
        f"Shields raised: {len(result.shields_raised)}",
        f"Supervisors killed: {len(result.supervisors_killed)}",
        f"Vault: {'passphrase destroyed (re-supply to unlock)' if result.vault_stopped else 'FAILED'}",
    ]
    errors = [
        *(f"  shield {name}: {error}" for name, error in result.shield_errors),
        *(f"  supervisor {name}: {error}" for name, error in result.supervisor_errors),
        *(f"  kill {name}: {error}" for name, error in result.container_stop_errors),
    ]
    for action, error in (
        ("discovery", result.discovery_error),
        ("vault", result.vault_error),
        ("restart interlock", result.lock_error),
    ):
        if error:
            errors.append(f"  {action}: {error}")
    if errors:
        lines += ["", "Errors:", *errors]
    return "\n".join(lines)
