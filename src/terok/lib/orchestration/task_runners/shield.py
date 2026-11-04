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

import subprocess  # noqa: S404 - wrapping ``podman inspect``; argv is built from fixed verbs + caller-vetted container name  # nosec B404
import sys
from contextlib import suppress
from typing import TYPE_CHECKING

from terok_util import require_host_tool

from terok.lib.integrations.sandbox import ShieldManager, ShieldState

from ...core import runtime as _rt
from ...core.config import (
    SHIELD_SECURITY_HINT,
    get_shield_disable_firewall_no_protection,
    make_sandbox_config,
)
from ...core.task_display import dns_tier_label
from ...util.logging_utils import timed_phase

if TYPE_CHECKING:
    from pathlib import Path

    from ...core.project_model import ProjectConfig

_DESIRED_SHIELD_STATE_FILENAME = "shield_desired_state"
_VALID_SHIELD_STATES = frozenset({"up", "down", "disengaged"})


def task_shield_manager(task_dir: Path) -> ShieldManager:
    """The [`ShieldManager`][terok_sandbox.ShieldManager] for the task in *task_dir*, under terok's config.

    The one place terok builds a manager to act on a task's shield on an
    operator's behalf — open or close its window, allow a host, break glass,
    read its harvest — so every surface reaches the same answer.  What makes
    it matter is the kill switch, ``shield.disable_firewall_no_protection``:
    sandbox deliberately reads it from no config file (a user-writable layer
    must not be able to switch the firewall off), so it reaches the manager
    only through the config terok passes.  A bare ``ShieldManager(task_dir)``
    never sees it, and would widen egress on a container started before the
    switch was thrown, which the switch exists to forbid.  Built from
    [`make_sandbox_config`][terok.lib.core.config.make_sandbox_config], which
    also carries the audit flag, the dnsmasq path and the window duration.
    """
    return ShieldManager(task_dir, make_sandbox_config())


def resolve_container_uuid(cname: str) -> str:
    """Return the full podman UUID for *cname*.

    Shield tells the container's clearance hub about each posture change
    through the per-container ingester at
    ``$XDG_RUNTIME_DIR/terok/events/<short id>/ingester.sock`` — the
    UUID's first 12 characters, never the operator-facing podman name —
    so every [`ShieldManager.up`][terok_sandbox.ShieldManager.up] /
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
        out = subprocess.check_output(  # noqa: S603 - argv is fixed verbs + caller-vetted name  # nosec B603 B607
            ["podman", "container", "inspect", "-f", "{{.Id}}", "--", cname],
            executable=require_host_tool("podman"),
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


def _restore_shield_state(cname: str, task_dir: Path) -> ShieldState | None:
    """Restore the persisted shield state on container restart (``retain`` policy).

    Returns the posture the container is left in: UP when nothing is to be
    restored (the OCI hook boots every container UP), the restored posture
    otherwise, and ``None`` when the restore failed and the posture is not
    known.
    """
    desired = _read_desired_shield_state(task_dir)
    if desired not in {"down", "disengaged"}:
        return ShieldState.UP
    disengaged = desired == "disengaged"
    try:
        container_id = resolve_container_uuid(cname)
        ShieldManager(task_dir).down(cname, container_id, disengaged=disengaged)
    except Exception as exc:
        import warnings

        warnings.warn(f"shield restore: {exc}", stacklevel=2)
        return None
    return ShieldState.DISENGAGED if disengaged else ShieldState.DOWN


def _shield_down_on_creation(cname: str, task_dir: Path) -> ShieldState | None:
    """Take the shield down after fresh container creation and persist the state.

    Records the ``down`` intent *before* attempting the transition so
    that a transient failure (UUID race, shield socket hiccup) still
    captures the operator's ``shield.down_on_task_run`` request — the
    next ``retain`` restart will re-attempt the transition instead of
    silently leaving the shield UP.  Returns DOWN, or ``None`` when the
    transition failed and the posture is not known.
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

        warnings.warn(f"shield down: {exc}", stacklevel=2)
        return None
    return ShieldState.DOWN


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
                runtime=_rt.launch_runtime_arg(project),
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

    posture: ShieldState | None
    with timed_phase(f"shield[{cname}]: apply policy"):
        try:
            if is_restart:
                policy = project.shield_on_task_restart
                if policy == "retain":
                    posture = _restore_shield_state(cname, task_dir)
                elif policy == "up":
                    posture = ShieldState.UP  # already UP from OCI hook
                else:
                    raise ValueError(
                        f"Unknown shield.on_task_restart value: {policy!r} "
                        "(expected 'retain' or 'up')"
                    )
            elif project.shield_down_on_task_run:
                posture = _shield_down_on_creation(cname, task_dir)
            else:
                _write_desired_shield_state(task_dir, "up")
                posture = ShieldState.UP
        except Exception:
            # Any shield-application failure leaves a live, half-protected
            # container.  Tear it down before surfacing the original error.
            _stop_container_best_effort(project, cname)
            raise

    _warn_on_dns_tier_limits(task_dir, posture)


def _warn_on_dns_tier_limits(task_dir: Path, posture: ShieldState | None) -> None:
    """Warn on stderr about what the task's DNS tier leaves to chance, in *posture*.

    Two warnings, both cheap file reads of what the OCI hook recorded at
    ``pre_start`` — the same signals the TUI's task pane shows.  The tier
    line names a degraded tier and its hint.  The pinned names follow:
    every name the tier resolved once at authoring and enforces by those
    addresses only, worded for the posture the policy just left the task in
    (*posture*): all of them while UP, only the denied ones while DOWN —
    where a vault host's rotated address escapes the deny, the louder
    security line — and nothing DISENGAGED, which enforces nothing.
    ``None`` (a transition that failed) gets the launch wording, which
    names both ways a pinned deny can be passed.  Break-glass overrides are
    pinned on every tier, the live one included, so the names can come with
    no tier line.

    Shield records the same lines in its log and audit note, which a CLI
    user does not see; ``terok shield up`` / ``down`` print them through
    shield's own verbs.  Advisory only: a failure to read either is
    swallowed, because the launch already succeeded and shield breakage
    loud enough to matter has already warned on its own path.
    """
    try:
        manager = ShieldManager(task_dir)
    except Exception:  # an advisory read: no manager, no warning
        return
    tier = None
    with suppress(Exception):
        tier = manager.dns_tier
    if tier is not None and not tier.live:
        print(f"Warning: DNS {dns_tier_label(tier)} — {tier.hint}", file=sys.stderr)
    lines: list[str] = []
    with suppress(Exception):
        risk = manager.rotation_risk
        if risk is not None:
            lines = list(risk.lines(posture))
    for line in lines:
        print(line, file=sys.stderr)


__all__ = [
    "_apply_shield_policy",
    "_refresh_shield_tiers",
    "resolve_container_uuid",
    "task_shield_manager",
]
