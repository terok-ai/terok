# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Emergency panic — immediately cut all resource access across all projects.

Three-step sequence: quarantine → kill supervisors → (optionally) kill
the containers themselves.  Quarantine alone is not enough: each
container's supervisor talks to its container over a bind-mounted
unix socket, and nft sees no traffic there.  Killing the host-side
supervisor process is what actually denies the container any more
vault / clearance / signer cycles.

Phase 1 (always): in parallel,

  * **Quarantine** every running container's shield (nft blackhole).
  * **Kill every supervisor** via the per-container PID files under
    ``state_root()/pids/`` — SIGKILL, no graceful TERM, because panic
    targets misbehaving containers and the SIGTERM grace period
    would just hand the supervisor more time to answer socket calls.
    The gate runs inside each container's supervisor, so this
    same kill stops the gate too — there is no separate host gate
    daemon to stop.
  * **Wipe the session-unlock tmpfs file** so a follow-up
    ``task restart`` after panic cannot bring up a supervisor that
    auto-unlocks from the same passphrase the operator just panic'd
    over.  Persistent tiers (keyring, sealed systemd-creds,
    ``credentials.passphrase``) are intentionally untouched —
    clearing them is destructive and the operator opts in via
    ``terok-sandbox vault lock --forget``.

Phase 2 (optional, ``stop_containers=True``): SIGKILL each container
via ``podman stop --time 0``.  Containers are *not* removed.

Token revocation is deliberately excluded — it is irreversible and
shields + dead supervisors already cut access.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from terok.lib.integrations.sandbox import PodmanRuntime

from ..core.config import get_shield_bypass_firewall_no_protection
from ..core.paths import core_state_dir
from ..core.projects import list_projects
from ..orchestration.tasks import (
    container_name,
    get_all_task_states,
    get_tasks,
)

logger = logging.getLogger(__name__)

_LOCK_FILENAME = "panic.lock"
_PHASE2_TIMEOUT_S = 15

# (project_name, task_id, mode, cname, task_dir)
type _Target = tuple[str, str, str, str, Path]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class PanicResult:
    """Outcome of an [`execute_panic`][terok.lib.domain.panic.execute_panic] invocation."""

    shields_raised: list[str] = field(default_factory=list)
    shield_errors: list[tuple[str, str]] = field(default_factory=list)
    supervisors_killed: list[str] = field(default_factory=list)
    supervisor_errors: list[tuple[str, str]] = field(default_factory=list)
    vault_stopped: bool = False
    vault_error: str | None = None
    containers_stopped: list[str] = field(default_factory=list)
    container_stop_errors: list[tuple[str, str]] = field(default_factory=list)
    shield_bypassed: bool = False
    total_running: int = 0

    @property
    def has_errors(self) -> bool:
        """Return whether any operation failed."""
        return bool(
            self.shield_errors
            or self.supervisor_errors
            or self.vault_error
            or self.container_stop_errors
        )


def execute_panic(
    *,
    stop_containers: bool = False,
) -> PanicResult:
    """Execute the full panic sequence.

    Discovers every running container, then (in parallel) raises shields,
    kills the per-container supervisors (which also stops each container's
    embedded gate), and destroys every stored copy of the vault
    passphrase (hard-lock).  If *stop_containers*,
    also SIGKILLs the containers afterwards (they are not removed).
    """
    result = PanicResult()
    targets = _discover_targets()
    result.total_running = len(targets)
    result.shield_bypassed = get_shield_bypass_firewall_no_protection()

    _phase1_lockdown(result, targets)
    _write_panic_lock()

    # Phase 2: optional container kill
    if stop_containers and targets:
        result.containers_stopped, result.container_stop_errors = _stop_containers(targets)

    return result


def panic_stop_containers() -> tuple[list[str], list[tuple[str, str]]]:
    """Discover and SIGKILL all running containers (Phase 2 standalone)."""
    return _stop_containers(_discover_targets())


def is_panicked() -> bool:
    """Return whether the panic lock file exists."""
    return (core_state_dir() / _LOCK_FILENAME).is_file()


def clear_panic_lock() -> None:
    """Remove the panic lock file if it exists."""
    (core_state_dir() / _LOCK_FILENAME).unlink(missing_ok=True)


def format_panic_report(result: PanicResult) -> str:
    """Format a human-readable summary of the panic result."""
    sup = f"Supervisors killed: {len(result.supervisors_killed)}"
    if result.supervisor_errors:
        sup += f" ({len(result.supervisor_errors)} failed)"
    lines = [
        f"Containers found: {result.total_running}",
        _format_shield_status(result),
        sup,
        f"Vault: {'passphrase destroyed (re-supply to unlock)' if result.vault_stopped else 'FAILED'}",
    ]

    if result.containers_stopped:
        lines.append(f"Containers killed: {len(result.containers_stopped)}")

    if result.has_errors:
        lines += ["", "Errors:", *_format_errors(result)]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _phase1_lockdown(result: PanicResult, targets: list[_Target]) -> None:
    """Run Phase 1: shields + supervisor-kill + vault stop in parallel."""
    with ThreadPoolExecutor(max_workers=max(len(targets) + 3, 4)) as pool:
        # Future → (kind, label); label is the container name for shields, "" otherwise.
        futs: dict[Future[Any], tuple[str, str]] = {}

        if not result.shield_bypassed:
            for t in targets:
                futs[pool.submit(_raise_shield, t)] = ("shield", t[3])

        futs[pool.submit(_kill_supervisors)] = ("supervisors", "")
        futs[pool.submit(_stop_vault)] = ("vault", "")

        for fut in as_completed(futs):
            kind, label = futs[fut]
            _collect_phase1_result(result, kind, label, fut)


def _collect_phase1_result(result: PanicResult, kind: str, label: str, fut: Future[Any]) -> None:
    """Collect a single Phase 1 future result into the PanicResult."""
    try:
        res = fut.result(timeout=60)
    except Exception as exc:
        _record_phase1_failure(result, kind, label, str(exc))
        return
    _record_phase1_success(result, kind, res)


def _record_phase1_failure(result: PanicResult, kind: str, label: str, err: str) -> None:
    """Treat an unhandled exception as failure for this kind."""
    if kind == "shield":
        result.shield_errors.append((label, err))
    elif kind == "supervisors":
        result.supervisor_errors.append(("*", err))
    else:  # vault
        result.vault_stopped = False
        result.vault_error = err


def _record_phase1_success(result: PanicResult, kind: str, res: Any) -> None:
    """Fold a completed Phase 1 future's payload into the PanicResult."""
    if kind == "shield":
        cname, err = res
        if err:
            result.shield_errors.append((cname, err))
        else:
            result.shields_raised.append(cname)
    elif kind == "supervisors":
        for container_id, err in res:
            if err:
                result.supervisor_errors.append((container_id, err))
            else:
                result.supervisors_killed.append(container_id)
    else:  # vault
        stopped, err = res
        result.vault_stopped = bool(stopped)
        result.vault_error = err


def _format_shield_status(result: PanicResult) -> str:
    """Format the shield status line for the panic report."""
    if result.shield_bypassed:
        return "Shields: BYPASSED (firewall protection disabled)"
    s = f"Shields raised: {len(result.shields_raised)}"
    if result.shield_errors:
        s += f" ({len(result.shield_errors)} failed)"
    return s


def _format_errors(result: PanicResult) -> list[str]:
    """Collect all error lines for the panic report."""
    lines = [f"  shield {cname}: {err}" for cname, err in result.shield_errors]
    lines += [f"  supervisor {cid}: {err}" for cid, err in result.supervisor_errors]
    if result.vault_error:
        lines.append(f"  vault: {result.vault_error}")
    lines += [f"  kill {cname}: {err}" for cname, err in result.container_stop_errors]
    return lines


def _discover_targets() -> list[_Target]:
    """Find every running or paused container across all projects."""
    targets: list[_Target] = []
    for cfg in list_projects():
        try:
            tasks = get_tasks(cfg.name)
            if not tasks:
                continue
            states = get_all_task_states(cfg.name, tasks)
        except Exception:
            logger.debug("panic: failed to list tasks for %s", cfg.name, exc_info=True)
            continue
        targets.extend(
            (
                cfg.name,
                t.task_id,
                t.mode,
                container_name(cfg.name, t.mode, t.task_id),
                cfg.tasks_root / str(t.task_id),
            )
            for t in tasks
            if t.mode and states.get(t.task_id) in ("running", "paused")
        )
    return targets


def _raise_shield(target: _Target) -> tuple[str, str | None]:
    """Block all traffic for one container (total blackout)."""
    from terok.lib.integrations.sandbox import ShieldManager

    _, _, _, cname, task_dir = target
    try:
        ShieldManager(task_dir).quarantine(cname)
        return cname, None
    except Exception as exc:
        return cname, str(exc)


def _kill_supervisors() -> list[tuple[str, str | None]]:
    """SIGKILL every host-side per-container supervisor process.

    The supervisor is the access point that survives nft quarantine —
    the container talks to it over a bind-mounted unix socket, which
    nft never sees.  Quarantine + supervisor-kill together deny both
    network egress and on-host IPC; without the supervisor-kill the
    container could keep talking to its vault / clearance / signer
    until the operator's optional ``stop_containers`` step.

    Returns one row per pid file ``(container_id, error_or_None)`` —
    same shape as terok-sandbox's library function so the caller can
    surface partial failures.
    """
    from terok.lib.integrations.sandbox import kill_all_supervisors

    return kill_all_supervisors()


def _stop_vault() -> tuple[bool, str | None]:
    """Hard-lock the vault: destroy every stored copy of the passphrase.

    Panic assumes the worst, so a half-measure won't do.  Wiping only
    the session file would leave a machine-bound tier (sealed
    systemd-creds, keyring, plaintext config) that auto-unlocks the vault
    on the *next* access — the lock would be theatre.  So panic evicts
    every tier via
    [`purge_passphrase_tiers`][terok_sandbox.purge_passphrase_tiers], with
    no confirmation: the operator was told to save the recovery passphrase
    off-host (the escrow-before-enable gate enforces it), so the vault is
    recoverable by re-supplying it — but until then it is genuinely shut.
    """
    from ..core.config import make_sandbox_config
    from ..integrations.sandbox import purge_passphrase_tiers

    try:
        purge_passphrase_tiers(make_sandbox_config())
        return True, None
    except Exception as exc:
        return False, str(exc)


def _stop_containers(targets: list[_Target]) -> tuple[list[str], list[tuple[str, str]]]:
    """SIGKILL each discovered container in parallel; do not remove them.

    Uses ``podman stop --time 0`` rather than a graceful stop: panic
    targets runaway containers, so the default 10 s SIGTERM window
    would just hand a misbehaving process more time to misbehave.

    Panic crosses projects (possibly under different runtimes); plain
    ``PodmanRuntime`` is correct here because the kill is a
    podman-level operation that works identically regardless of which
    OCI runtime booted any individual container.
    """
    if not targets:
        return [], []

    runtime = PodmanRuntime()
    results: dict[str, str | None] = {}

    def _worker(cname: str) -> None:
        results[cname] = _kill_container(runtime, cname)

    # Daemon threads so a wedged ``podman stop`` past the panic budget
    # doesn't pin Python's atexit, letting ``terok panic`` return to the
    # operator's shell within ``_PHASE2_TIMEOUT_S`` regardless of the
    # subprocess state.
    threads = [
        threading.Thread(target=_worker, args=(t[3],), daemon=True, name=f"panic-stop-{t[3]}")
        for t in targets
    ]
    for th in threads:
        th.start()
    deadline = time.monotonic() + _PHASE2_TIMEOUT_S
    for th in threads:
        th.join(timeout=max(0.0, deadline - time.monotonic()))

    stopped: list[str] = []
    errors: list[tuple[str, str]] = []
    for t in targets:
        cname = t[3]
        if cname not in results:
            # Worker thread didn't finish within the budget.
            errors.append((cname, f"timed out after {_PHASE2_TIMEOUT_S}s"))
        elif (err := results[cname]) is not None:
            errors.append((cname, err))
        else:
            stopped.append(cname)
    return stopped, errors


def _kill_container(runtime: PodmanRuntime, cname: str) -> str | None:
    """SIGKILL one container without removing it (``podman stop --time 0``)."""
    try:
        runtime.container(cname).stop(timeout=0)
        return None
    except Exception as exc:
        return str(exc)


def _write_panic_lock() -> None:
    """Write the panic lock file with current timestamp."""
    path = core_state_dir() / _LOCK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(UTC).isoformat() + "\n")
