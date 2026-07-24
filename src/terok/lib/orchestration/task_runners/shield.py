# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Per-task shield (egress firewall) policy.

``_apply_shield_policy`` is the entry point every runner calls after a
container starts — it honours ``shield.drop_on_task_run`` on creation,
``shield.on_task_restart`` on restart, and always reapplies the
roster-driven auth-protect denies that survive ``shield down``.

The shield's hub socket is keyed on the **container UUID**, not the
operator-facing name —
[`resolve_container_uuid`][terok.lib.orchestration.task_runners.shield.resolve_container_uuid]
threads that ID through every
[`ShieldManager.up`][terok_sandbox.ShieldManager.up] /
[`ShieldManager.down`][terok_sandbox.ShieldManager.down] call so
verdicts reach the right hub.
"""

from __future__ import annotations

import subprocess  # noqa: S404 — wrapping ``podman inspect``; argv is built from fixed verbs + caller-vetted container name  # nosec B404
from contextlib import suppress
from typing import TYPE_CHECKING

from terok.lib.integrations.sandbox import ShieldManager

from ...core import runtime as _rt
from ...core.config import SHIELD_SECURITY_HINT, get_shield_bypass_firewall_no_protection
from ...util.logging_utils import timed_phase

if TYPE_CHECKING:
    from pathlib import Path

    from ...core.project_model import ProjectConfig

_DESIRED_SHIELD_STATE_FILENAME = "shield_desired_state"
_VALID_SHIELD_STATES = frozenset({"up", "down", "disengaged"})


def resolve_container_uuid(cname: str) -> str:
    """Return the full podman UUID for *cname*.

    Shield's per-container hub socket lives at
    ``$XDG_RUNTIME_DIR/terok/clearance/<container_id>.sock`` (keyed on
    the UUID, not the operator-facing podman name), so every
    [`ShieldManager.up`][terok_sandbox.ShieldManager.up] /
    [`ShieldManager.down`][terok_sandbox.ShieldManager.down] call must
    carry both: the name for audit log readability, the UUID for hub
    routing.

    Raises [`RuntimeError`][RuntimeError] when the container can't be
    inspected — the caller's intent is "do something to this running
    container's shield", and a missing container makes that intent
    unfulfillable.  Callers that tolerate the missing case (e.g. best-
    effort post-stop reconciliation) wrap the call in their own
    ``try`` block.
    """
    try:
        out = subprocess.check_output(  # noqa: S603 — argv is fixed verbs + caller-vetted name  # nosec B603 B607
            ["podman", "container", "inspect", "-f", "{{.Id}}", "--", cname],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5.0,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"podman inspect failed for container {cname!r}: {exc}") from exc
    if not out:
        raise RuntimeError(f"podman inspect returned empty Id for container {cname!r}")
    return out


def _read_desired_shield_state(task_dir: Path) -> str | None:
    """Read the persisted shield state from the task directory.

    Returns ``None`` only when the file is absent — a corrupted value
    (truncated mid-write, partial filesystem failure) raises ``ValueError``
    so the caller surfaces an actionable error rather than silently
    flipping the operator's persisted policy back to the OCI-hook default.
    """
    path = task_dir / _DESIRED_SHIELD_STATE_FILENAME
    if not path.is_file():
        return None
    value = path.read_text().strip()
    if value not in _VALID_SHIELD_STATES:
        raise ValueError(f"corrupt shield-state file {path}: {value!r}")
    return value


def _write_desired_shield_state(task_dir: Path, state: str) -> None:
    """Persist the desired shield state to the task directory."""
    (task_dir / _DESIRED_SHIELD_STATE_FILENAME).write_text(f"{state}\n")


def _restore_shield_state(cname: str, task_dir: Path) -> None:
    """Restore the persisted shield state on container restart (``retain`` policy)."""
    desired = _read_desired_shield_state(task_dir)
    if desired not in {"down", "disengaged"}:
        return
    try:
        container_id = resolve_container_uuid(cname)
        ShieldManager(task_dir).down(cname, container_id, allow_all=(desired == "disengaged"))
    except Exception as exc:
        import warnings

        warnings.warn(f"shield restore: {exc}", stacklevel=2)


def _drop_shield_on_creation(cname: str, task_dir: Path) -> None:
    """Drop the shield after fresh container creation and persist the state.

    Records the ``down`` intent *before* attempting the drop so that a
    transient drop failure (UUID race, shield socket hiccup) still
    captures the operator's ``shield.drop_on_task_run`` request — the
    next ``retain`` restart will re-attempt the drop instead of
    silently leaving the shield UP.
    """
    _write_desired_shield_state(task_dir, "down")
    try:
        container_id = resolve_container_uuid(cname)
        ShieldManager(task_dir).down(cname, container_id)
        audit_path = task_dir / "shield" / "audit.jsonl"
        print(f"Shield is down. Audit log: {audit_path}")
        print(SHIELD_SECURITY_HINT)
    except Exception as exc:
        import warnings

        warnings.warn(f"shield drop: {exc}", stacklevel=2)


def _stop_container_best_effort(project: ProjectConfig, cname: str) -> None:
    """Stop *cname*, swallowing every error.

    Used on the post-start shield-failure path: the container is already
    live, so the stop is pure cleanup.  Any failure here (podman missing,
    container already gone, runtime hiccup) must not mask the original
    shield error the caller is about to re-raise.
    """
    with suppress(Exception):
        _rt.resolve_runtime(project).container(cname).stop(timeout=project.shutdown_timeout)


def _apply_shield_policy(
    project: ProjectConfig, cname: str, task_dir: Path, *, is_restart: bool
) -> None:
    """Apply shield policy after container start (creation or restart).

    On fresh creation, honours ``shield.drop_on_task_run``.  On restart,
    honours ``shield.on_task_restart`` (``retain`` restores the last known
    state, ``up`` leaves the deny-all ruleset from the OCI hook).

    Callers invoke this *after* the container is already running and
    *before* task metadata is written, so a raise here would otherwise
    strand a live, only-partially-protected and untracked container.  On
    any failure we best-effort stop the container before re-raising — a
    half-protected container is worse than no container.
    """
    if get_shield_bypass_firewall_no_protection():
        return

    with timed_phase(f"shield[{cname}]: apply policy"):
        try:
            if is_restart:
                policy = project.shield_on_task_restart
                if policy == "retain":
                    _restore_shield_state(cname, task_dir)
                elif policy == "up":
                    pass  # already UP from OCI hook
                else:
                    raise ValueError(
                        f"Unknown shield.on_task_restart value: {policy!r} "
                        "(expected 'retain' or 'up')"
                    )
            elif project.shield_drop_on_task_run:
                _drop_shield_on_creation(cname, task_dir)
            else:
                _write_desired_shield_state(task_dir, "up")
        except Exception:
            # Any shield-application failure leaves a live, half-protected
            # container.  Tear it down before surfacing the original error.
            _stop_container_best_effort(project, cname)
            raise


__all__ = [
    "_apply_shield_policy",
    "resolve_container_uuid",
]
