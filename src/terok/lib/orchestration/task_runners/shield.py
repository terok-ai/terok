# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Per-task shield (egress firewall) policy.

``_apply_shield_policy`` is the entry point every runner calls after a
container starts — it honours ``shield.down_on_task_run`` on creation and
``shield.on_task_restart`` on restart.  ``_refresh_shield_tiers`` is the
restart path's pre-start companion: it recomputes the container's policy
bundle from the *current* roster and project config so a resumed container
enforces today's tiers, not the ones frozen at creation.

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
from ...core.config import SHIELD_SECURITY_HINT, get_shield_disable_firewall_no_protection
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
        ShieldManager(task_dir).down(cname, container_id, disengaged=(desired == "disengaged"))
    except Exception as exc:
        import warnings

        warnings.warn(f"shield restore: {exc}", stacklevel=2)


def _shield_down_on_creation(cname: str, task_dir: Path) -> None:
    """Take the shield down after fresh container creation and persist the state.

    Records the ``down`` intent *before* attempting the transition so
    that a transient failure (UUID race, shield socket hiccup) still
    captures the operator's ``shield.down_on_task_run`` request — the
    next ``retain`` restart will re-attempt the transition instead of
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


def _refresh_shield_tiers(project: ProjectConfig, cname: str, task_dir: Path) -> None:
    """Recompute a stopped container's shield policy bundle before resuming it.

    Re-derives the generated t20/t30 tiers from the *current* roster
    projection and the authored t40/t10 tiers from the *current* project
    config — the same inputs the creation path uses — and pushes them
    through [`Sandbox.shield_refresh`][terok_sandbox.Sandbox.shield_refresh],
    so a plain restart picks up roster/config changes instead of replaying
    the bundle frozen at creation.  Runs *before* anything is torn down;
    a failure aborts the restart with the container untouched.

    Skipped when the firewall kill-switch is active or the container was
    never shielded (no shield state under *task_dir* — e.g. created with
    the shield disabled).
    """
    if get_shield_disable_firewall_no_protection():
        return
    if not ShieldManager(task_dir).state_dir.is_dir():
        return
    from terok.lib.integrations.executor import AgentRoster

    from ...core.config import exposed_credential_providers
    from .container import _compose_shield_tiers, _sandbox

    egress = AgentRoster.shared().compose_egress(
        exposed_credential_providers=exposed_credential_providers()
    )
    project_allow, override = _compose_shield_tiers(project)
    with timed_phase(f"shield[{cname}]: refresh policy bundle"):
        try:
            _sandbox(project).shield_refresh(
                cname,
                task_dir,
                runtime=project.runtime,
                security_deny=egress.deny_to_vault,
                provider_allow=egress.provider_allow,
                project_allow=project_allow,
                override=override,
            )
        except (RuntimeError, ValueError) as exc:
            raise SystemExit(
                f"Shield policy refresh failed for {cname}: {exc}\n"
                f"The container was left untouched."
            ) from exc


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

    On fresh creation, honours ``shield.down_on_task_run``.  On restart,
    honours ``shield.on_task_restart`` (``retain`` restores the last known
    state, ``up`` leaves the deny-all ruleset from the OCI hook).

    Callers invoke this *after* the container is already running and
    *before* task metadata is written, so a raise here would otherwise
    strand a live, only-partially-protected and untracked container.  On
    any failure we best-effort stop the container before re-raising — a
    half-protected container is worse than no container.
    """
    if get_shield_disable_firewall_no_protection():
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
            elif project.shield_down_on_task_run:
                _shield_down_on_creation(cname, task_dir)
            else:
                _write_desired_shield_state(task_dir, "up")
        except Exception:
            # Any shield-application failure leaves a live, half-protected
            # container.  Tear it down before surfacing the original error.
            _stop_container_best_effort(project, cname)
            raise


__all__ = [
    "_apply_shield_policy",
    "_refresh_shield_tiers",
    "resolve_container_uuid",
]
