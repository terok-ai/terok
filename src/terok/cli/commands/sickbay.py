# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Health check and reconciliation command (DS9-themed diagnostic bay).

Runs a series of checks and reports their status.  With ``--fix``,
auto-remediates issues like unfired post_stop hooks.

Scoping:
- ``terok sickbay`` — all projects
- ``terok sickbay <project>`` — single project
- ``terok sickbay <project> <task>`` — single task

Exit codes:
- 0: all checks passed
- 1: warnings present
- 2: errors present
"""

from __future__ import annotations

import argparse
import shutil
import sys
from contextlib import suppress
from pathlib import Path

from terok.lib.api.clearance import (
    CLEARANCE_HUB_UNIT_NAME as _CLEARANCE_HUB_UNIT_NAME,
    CLEARANCE_NOTIFIER_UNIT_NAME as _CLEARANCE_NOTIFIER_UNIT_NAME,
    check_clearance_units_outdated as _clearance_check_units_outdated,
    read_installed_notifier_unit_version as _clearance_notifier_unit_version,
    read_installed_unit_version as _clearance_hub_unit_version,
)
from terok.lib.api.gate import GateServerManager
from terok.lib.api.setup import (
    SERVICES_TCP_OPTOUT_YAML,
    check_environment,
    resolve_container_state_dir,
    systemd_creds_has_tpm2,
)
from terok.lib.api.vault import VaultManager

from ...lib.core import runtime as _rt
from ...lib.core.config import get_services_mode, global_config_path, make_sandbox_config
from ...lib.core.project_model import ProjectConfig, is_valid_project_id
from ...lib.core.projects import list_projects, load_project
from ...lib.orchestration.container_doctor import ContainerDoctor
from ...lib.orchestration.hooks import run_hook
from ...lib.orchestration.tasks import (
    container_name,
    is_task_id,
    iter_task_ids,
    meta_path,
    read_task_meta,
    resolve_task_id,
    tasks_meta_dir,
)
from ...lib.util.check_reporter import CheckReporter

# Type alias for check results: (severity, label, detail)
_CheckResult = tuple[str, str, str]


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``sickbay`` subcommand."""
    p = subparsers.add_parser("sickbay", help="Run health checks and reconciliation")
    # dest=project_id / task_id matches the rest of the CLI so the shared
    # completers work; metavar keeps the display ``<project>``/``<task>``.
    from ._completers import add_project_id, add_task_id

    add_project_id(p, nargs="?", metavar="project", help="Scope to a single project")
    add_task_id(p, nargs="?", metavar="task", help="Scope to a single task")
    p.add_argument("--fix", action="store_true", help="Auto-remediate issues")


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the sickbay command.  Returns True if handled."""
    if args.cmd != "sickbay":
        return False
    project_id = getattr(args, "project_id", None)
    task_id = getattr(args, "task_id", None)
    if project_id and task_id:
        task_id = resolve_task_id(project_id, task_id)
    _cmd_sickbay(
        project_id=project_id,
        task_id=task_id,
        fix=getattr(args, "fix", False),
    )
    return True


def _check_gate_server() -> _CheckResult:
    """Check gate server status.

    Three "not running" paths get different messages because they need
    different fixes:

    * Operator-pending install → ``warn`` with a ``terok gate start``
      pointer.
    * Missing ``git`` on the host → ``warn`` naming the consequence
      (no git push channel), no remediation pointer — installing git
      is distro-specific and the operator's call.
    * No user systemd → ``warn`` naming the gap (gate's inetd-style
      architecture has no managed-daemon fallback yet, sandbox#…).

    The latter two used to collapse to the same generic "run
    'terok gate start'" line that would have failed to do anything;
    the contextual messages match the executor preflight's verdicts.
    """
    cfg = make_sandbox_config()
    gate = GateServerManager(cfg)
    status = gate.get_status()
    configured = get_services_mode()
    label = "Gate server"
    if status.running:
        outdated = gate.check_units_outdated()
        if outdated:
            return ("warn", label, f"{outdated} Run 'terok gate start' to update.")
        detail = f"{status.mode}, {status.transport or 'tcp'}"
        if configured != (status.transport or "tcp"):
            return (
                "warn",
                label,
                f"{detail} — config says services.mode: {configured}",
            )
        return ("ok", label, detail)
    if status.mode == "systemd":
        return ("error", label, "socket installed but not active")
    if not shutil.which("git"):
        return (
            "warn",
            label,
            "disabled — git not on PATH (no host-side git push channel)",
        )
    if not gate.is_systemd_available():
        return (
            "warn",
            label,
            "disabled — no user systemd (managed-daemon fallback not implemented yet)",
        )
    return ("warn", label, "not running — run 'terok gate start'")


def _check_shield() -> _CheckResult:
    """Check egress firewall (terok-shield) environment."""
    label = "Shield"
    try:
        ec = check_environment()
    except Exception as exc:  # noqa: BLE001
        return ("warn", label, f"check failed — {exc}")
    if ec.health == "bypass":
        return ("warn", label, "bypass_firewall_no_protection is active — egress disabled")
    if ec.health == "stale-hooks":
        return ("warn", label, "hooks outdated — run 'terok shield install-hooks --user'")
    if ec.health == "setup-needed":
        hint = (
            ec.setup_hint.splitlines()[0]
            if ec.setup_hint
            else "run 'terok shield install-hooks --user'"
        )
        return ("warn", label, f"{ec.issues[0] if ec.issues else 'setup needed'} — {hint}")
    if ec.health != "ok":
        return ("warn", label, f"unexpected health: {ec.health}")
    dns = getattr(ec, "dns_tier", "unknown")
    detail = f"active ({ec.hooks}, {dns} DNS)"
    # On a lower tier (dig/getent) surface shield's own reason: dnsmasq
    # missing and dnsmasq AppArmor-confined need different fixes, and
    # shield reports the precise one (with any docs pointer) in ec.issues.
    if dns != "dnsmasq":
        hint = (
            ec.issues[0] if ec.issues else "install dnsmasq for live IP rotation + domain updates"
        )
        detail += f" — {hint}"
    return ("ok", label, detail)


def _check_clearance_stack() -> _CheckResult:
    """Surface stale or half-installed clearance units — pipx upgrade hygiene.

    Delegates drift detection to the clearance package so sickbay
    tracks whatever new units terok-clearance ships next without
    knowing the triple's shape itself.  Hub and notifier versions
    surface side-by-side so an operator who edits the notifier-only
    profile (e.g. hardening tweaks) doesn't have to read the unit
    file to see whether their drift was picked up.
    """
    label = "Clearance stack"
    outdated = _clearance_check_units_outdated()
    if outdated:
        return ("warn", label, outdated)
    hub = _clearance_hub_unit_version()
    notifier = _clearance_notifier_unit_version()
    if hub is None and notifier is None:
        return ("ok", label, f"{_CLEARANCE_HUB_UNIT_NAME} not installed")
    parts: list[str] = []
    if hub is not None:
        parts.append(f"{_CLEARANCE_HUB_UNIT_NAME} v{hub}")
    if notifier is not None:
        parts.append(f"{_CLEARANCE_NOTIFIER_UNIT_NAME} v{notifier}")
    return ("ok", label, ", ".join(parts))


def _passphrase_tier_label(source: str | None) -> str | None:
    """Render the resolved passphrase tier for the sickbay vault detail line.

    Mirrors the wording ``terok vault status`` uses (``resolved via
    systemd-creds`` etc.); adds a ``+TPM2`` suffix when the tier is
    ``systemd-creds`` and the host actually has a TPM2 device — same
    signal ``systemd-creds`` ships under, surfaced where operators are
    already looking.  ``None`` collapses to an empty annotation
    (caller drops it from the detail string).
    """
    if not source:
        return None
    label = f"passphrase via {source}"
    if source == "systemd-creds":
        # ``systemd-creds has-tpm2`` is best-effort — a missing binary
        # or a hung probe must not break the sickbay row.  Suppress
        # rather than ``try/except: pass`` so static analysers see the
        # explicit "this is intentional" annotation.
        with suppress(Exception):
            if systemd_creds_has_tpm2():
                label = f"{label} (+TPM2)"
    return label


def _check_vault() -> _CheckResult:
    """Check vault status, surfacing the resolved passphrase tier."""
    label = "Vault"
    vault = VaultManager()
    try:
        status = vault.get_status()
    except Exception as exc:  # noqa: BLE001
        return ("warn", label, f"check failed — {exc}")
    if status.running:
        configured = get_services_mode()
        creds = len(status.credentials_stored) if status.credentials_stored else 0
        parts = [status.mode, status.transport or "tcp"]
        tier = _passphrase_tier_label(status.passphrase_source)
        if tier:
            parts.append(tier)
        parts.append(f"{creds} credential(s) stored")
        detail = ", ".join(parts)
        if configured != (status.transport or "tcp"):
            return (
                "warn",
                label,
                f"{detail} — config says services.mode: {configured}",
            )
        return ("ok", label, detail)
    if status.mode == "systemd":
        if vault.is_socket_active():
            return ("ok", label, "systemd, socket active — service starts on first connection")
        return (
            "error",
            label,
            "socket installed but not active — run 'terok vault start'",
        )
    if vault.is_systemd_available():
        return ("warn", label, "not running — run 'terok vault install'")
    return ("warn", label, "not running — run 'terok vault start'")


def _task_meta_path(pid: str, tid: str) -> Path | None:
    """Resolve a task's canonical metadata path, refusing traversal in *pid* / *tid*.

    Both IDs arrive from CLI positional args (``terok sickbay <project>
    <task>``).  A hostile value like ``../../etc/passwd`` would otherwise
    escape ``tasks_meta_dir`` via ``Path`` join; reject anything that
    doesn't match the established project/task-ID grammars.

    The returned path always points at the JSON canonical name; YAML
    files from earlier installs are migrated by ``read_task_meta`` on
    the read path.
    """
    if not is_valid_project_id(pid) or not is_task_id(tid):
        return None
    return meta_path(tasks_meta_dir(pid), tid)


def _check_task_hook(
    pid: str, tid: str, project: ProjectConfig, *, fix: bool
) -> _CheckResult | None:
    """Check a single task for unfired post_stop hook.  Returns None if ok."""
    if not is_valid_project_id(pid) or not is_task_id(tid):
        return None
    meta_dir = tasks_meta_dir(pid)
    try:
        meta = read_task_meta(meta_dir, tid)
    except Exception:
        return ("warn", f"Task {pid}/{tid}", f"bad metadata: {meta_path(meta_dir, tid)}")
    if meta is None:
        return None
    meta_file = meta_path(meta_dir, tid)

    mode = meta.get("mode")
    if not mode:
        return None

    cname = container_name(pid, mode, tid)
    if _rt.resolve_runtime(project).container(cname).state == "running":
        return None

    fired = meta.get("hooks_fired") or []
    if "post_stop" in fired:
        return None

    label = f"Task {pid}/{tid}"
    if not fix:
        return ("warn", label, "stopped without post_stop hook — run with --fix to reconcile")

    return _reconcile_post_stop(pid, tid, mode, cname, project, meta_file, label)


def _reconcile_post_stop(
    pid: str,
    tid: str,
    mode: str,
    cname: str,
    project: ProjectConfig,
    meta_path: Path,
    label: str,
) -> _CheckResult:
    """Run the missed post_stop hook and return the result."""
    try:
        run_hook(
            "post_stop",
            project.hook_post_stop,
            project_id=pid,
            task_id=tid,
            mode=mode,
            cname=cname,
            task_dir=project.tasks_root / tid,
            meta_path=meta_path,
        )
        return ("ok", label, "post_stop hook reconciled")
    except Exception as exc:
        return ("error", label, f"post_stop hook failed: {exc}")


def _check_task_shield_annotation(
    pid: str, tid: str, project: ProjectConfig
) -> _CheckResult | None:
    """Check that task_dir agrees with the container's ``terok.shield.state_dir``.

    Drift between the two sides sends a verdict dispatched from the hub or
    TUI (which only know the container name) to the wrong state dir, or to
    nothing at all.  Non-shielded containers and stopped ones are skipped.
    """
    if not is_valid_project_id(pid) or not is_task_id(tid):
        return None
    meta_dir = tasks_meta_dir(pid)
    try:
        meta = read_task_meta(meta_dir, tid)
    except Exception:  # noqa: BLE001
        return None
    if meta is None:
        return None
    mode = meta.get("mode")
    if not mode:
        return None
    cname = container_name(pid, mode, tid)
    if _rt.resolve_runtime(project).container(cname).state != "running":
        return None

    expected = (project.tasks_root / tid / "shield").resolve()
    if not expected.is_dir():
        return None  # task isn't shielded — nothing to compare against

    label = f"Task {pid}/{tid} shield"
    actual = resolve_container_state_dir(cname)
    if actual is None:
        return (
            "warn",
            label,
            f"{cname!r}: no terok.shield.state_dir annotation, expected {expected} "
            "— verdict dispatch will miss",
        )
    if actual.resolve() != expected:
        return (
            "warn",
            label,
            f"{cname!r}: annotation points at {actual}, expected {expected} "
            "— filesystem moved without re-running pre_start?",
        )
    return None


def _check_unfired_hooks(
    project_id: str | None, task_id: str | None, *, fix: bool
) -> list[_CheckResult]:
    """Check for stopped tasks with unfired post_stop hooks."""
    results: list[_CheckResult] = []

    if project_id:
        projects = [(project_id, load_project(project_id))]
    else:
        projects = [(p.id, p) for p in list_projects()]

    for pid, project in projects:
        if not project.hook_post_stop:
            continue
        meta_dir = tasks_meta_dir(pid)
        if not meta_dir.is_dir():
            continue

        task_ids = list(iter_task_ids(meta_dir)) if task_id is None else [task_id]
        for tid in task_ids:
            result = _check_task_hook(pid, tid, project, fix=fix)
            if result:
                results.append(result)

    return results


def _check_shield_annotations(project_id: str | None, task_id: str | None) -> list[_CheckResult]:
    """Check that every running task's container carries the expected shield annotation."""
    results: list[_CheckResult] = []

    if project_id:
        projects = [(project_id, load_project(project_id))]
    else:
        projects = [(p.id, p) for p in list_projects()]

    for pid, project in projects:
        meta_dir = tasks_meta_dir(pid)
        if not meta_dir.is_dir():
            continue
        task_ids = list(iter_task_ids(meta_dir)) if task_id is None else [task_id]
        for tid in task_ids:
            result = _check_task_shield_annotation(pid, tid, project)
            if result:
                results.append(result)

    return results


def _sanitize_id(value: str) -> str:
    """Strip C0/C1 control characters from a project ID for safe terminal output."""
    import unicodedata

    return "".join(
        " " if ch in "\n\r\t" else f"\\x{ord(ch):02x}" if unicodedata.category(ch)[0] == "C" else ch
        for ch in value
    )


def _abbreviate(ids: list[str], limit: int = 3) -> str:
    """Join project IDs with a '+N more' suffix when the list is long."""
    suffix = f" (+{len(ids) - limit} more)" if len(ids) > limit else ""
    return ", ".join(_sanitize_id(i) for i in ids[:limit]) + suffix


def _check_ssh_signer() -> _CheckResult:
    """Check SSH signer key registration against known projects."""
    from ...lib.api import vault_db

    label = "SSH signer"
    projects = list_projects()
    if not projects:
        return ("ok", label, "no projects configured")

    try:
        with vault_db() as db:
            assigned_scopes = set(db.list_scopes_with_ssh_keys())
    except Exception as exc:  # noqa: BLE001
        return ("warn", label, f"vault unreachable — {exc}")

    unregistered = [p.id for p in projects if p.id not in assigned_scopes]
    registered = len(projects) - len(unregistered)
    total = len(projects)

    if unregistered:
        return (
            "warn",
            label,
            f"{registered}/{total} project(s) have SSH keys — missing: "
            f"{_abbreviate(unregistered)}. Run 'terok project ssh-init <project>'",
        )
    return ("ok", label, f"{total}/{total} project(s) have SSH keys")


def _stream_containers(
    project_id: str | None,
    task_id: str | None,
    *,
    fix: bool,
    reporter: CheckReporter,
) -> None:
    """Stream in-container diagnostics through *reporter*, one task at a time.

    The per-task running-state check is handled inside
    [`ContainerDoctor.run`][terok.lib.orchestration.container_doctor.ContainerDoctor.run]
    — it emits an informational line for non-running containers, so we
    simply forward all tasks and let the orchestrator decide.
    """
    if project_id and task_id:
        ContainerDoctor(project_id, task_id).run(
            fix=fix,
            reporter=reporter,
            label_prefix=f"Task {project_id}/{task_id}: ",
        )
        return

    # Project or global scope — iterate all known tasks
    if project_id:
        projects = [(project_id, load_project(project_id))]
    else:
        projects = [(p.id, p) for p in list_projects()]

    for pid, _project in projects:
        meta_dir = tasks_meta_dir(pid)
        if not meta_dir.is_dir():
            continue
        for tid in iter_task_ids(meta_dir):
            ContainerDoctor(pid, tid).run(
                fix=fix,
                reporter=reporter,
                label_prefix=f"Task {pid}/{tid}: ",
            )


def _check_selinux_policy() -> _CheckResult:
    """Check SELinux policy prerequisites for socket-based services.

    The decision tree (tcp vs socket, enforcing vs permissive, policy
    installed, libselinux loadable) lives in
    [`terok_sandbox.check_selinux_status`][terok_sandbox.check_selinux_status] so sickbay and
    ``terok setup`` share one source of truth; this function only
    translates the structured result into sickbay's output shape.
    """
    from terok.lib.api.setup import (
        SelinuxStatus,
        check_selinux_status,
        selinux_install_command,
        selinux_install_script,
    )

    label = "SELinux policy"
    result = check_selinux_status(services_mode=get_services_mode())

    match result.status:
        case SelinuxStatus.NOT_APPLICABLE_TCP_MODE:
            return ("ok", label, "not needed (services.mode: tcp)")
        case SelinuxStatus.NOT_APPLICABLE_PERMISSIVE:
            return ("ok", label, "not needed (SELinux not enforcing)")
        case SelinuxStatus.POLICY_MISSING:
            install_cmd = selinux_install_command()
            opt_out = f"or opt out: {SERVICES_TCP_OPTOUT_YAML} in {global_config_path()}"
            if result.missing_policy_tools:
                tools = ", ".join(result.missing_policy_tools)
                return (
                    "warn",
                    label,
                    f"terok_socket_t NOT installed; policy tools missing ({tools}). "
                    "Fix (pick one): sudo dnf install selinux-policy-devel policycoreutils, "
                    f"then {install_cmd}; {opt_out}",
                )
            return (
                "warn",
                label,
                "terok_socket_t NOT installed — containers cannot connect to sockets. "
                f"Fix (pick one): {install_cmd}; {opt_out}",
            )
        case SelinuxStatus.LIBSELINUX_MISSING:
            return (
                "warn",
                label,
                "libselinux.so.1 not loadable — sockets will bind as unconfined_t "
                "and containers will be denied even with the policy installed. "
                "Fix: sudo dnf install libselinux",
            )
        case SelinuxStatus.OK:
            return (
                "ok",
                label,
                "terok_socket_t installed, binding functional "
                f"(installer: {selinux_install_script()})",
            )


def _check_vault_migration() -> _CheckResult:
    """Check for leftover pre-vault credentials directory."""
    label = "Vault migration"
    try:
        from terok.lib.api.setup import namespace_state_dir

        old_dir = namespace_state_dir("credentials")
        new_dir = namespace_state_dir("vault")
        if old_dir.is_dir() and not new_dir.is_dir():
            return (
                "warn",
                label,
                f"legacy credentials/ dir exists at {old_dir} — "
                "run 'python3 tools/terok-migrate-vault.py' to migrate to vault/",
            )
        if old_dir.is_dir() and new_dir.is_dir():
            return (
                "info",
                label,
                f"legacy credentials/ dir still present at {old_dir} — "
                "safe to remove after verifying vault/ works",
            )
    except Exception as exc:  # noqa: BLE001
        return ("warn", label, f"check failed — {exc}")
    return ("ok", label, "no legacy directory")


def _check_recovery_acknowledged() -> _CheckResult:
    """Warn / error when the operator hasn't confirmed they saved the recovery key.

    Sandbox-side check; the marker is a zero-byte sidecar per install,
    so this is host-level (one row at the top, not per-task — terok's
    container loop deliberately excludes it from the
    [`sandbox_doctor_checks`][terok_sandbox.doctor.sandbox_doctor_checks]
    bundle).

    Two severity bands when the marker is missing: an ``error`` when
    the resolver lands on the session-unlock tmpfs file (the
    passphrase is wiped on the next reboot and the vault becomes
    unrecoverable then), a ``warn`` for any durable tier (machine-
    bound; needs an off-host copy for hardware-failure DR).
    """
    label = "Recovery key acknowledged"
    try:
        from terok.lib.api.shield import RecoveryStatus

        status = RecoveryStatus.load()
    except Exception as exc:  # noqa: BLE001 — best-effort probe, never block sickbay
        return ("warn", label, f"check failed — {exc}")
    if status.acknowledged:
        return ("ok", label, "recovery key acknowledged")
    from terok.lib.api import bold

    reveal = bold("terok vault passphrase reveal")
    ack = bold("terok vault passphrase acknowledge")
    if status.urgent:
        return (
            "error",
            label,
            "vault recovery key UNCONFIRMED and the passphrase lives ONLY"
            " in the session-unlock tmpfs file — it will be wiped on the"
            " next reboot and your vault becomes UNRECOVERABLE then."
            f" Run {reveal} NOW and save the value off-host,"
            f" or {ack} if you already captured it.",
        )
    return (
        "warn",
        label,
        "vault recovery key unconfirmed — every keystore tier is"
        " machine-bound, so a hardware failure strands the vault."
        f" Run {reveal} to view and save the value off-host,"
        f" or {ack} if you already captured it.",
    )


def _check_default_agents() -> _CheckResult:
    """Warn when no global ``image.agents`` default is configured.

    Bare scope is fine on first run but noisy for fleets that want a
    deliberate roster across projects.
    """
    label = "Default agents"
    try:
        from terok.lib.integrations.executor import ExecutorConfigView

        current = ExecutorConfigView.image_agents()
    except Exception as exc:  # noqa: BLE001 — probe is best-effort; never crash sickbay
        return ("warn", label, f"check failed — {exc}")

    if current:
        return ("ok", label, f"image.agents = {current!r}")
    return (
        "warn",
        label,
        "no default selection — projects fall back to the bundled "
        "roster.  Run 'terok agents set' to pick a roster baked into "
        "L1 by default.",
    )


_GLOBAL_CHECKS = [
    ("Gate server", _check_gate_server),
    ("Shield", _check_shield),
    ("Vault", _check_vault),
    ("Vault migration", _check_vault_migration),
    ("Recovery key acknowledged", _check_recovery_acknowledged),
    ("SSH signer", _check_ssh_signer),
    ("SELinux policy", _check_selinux_policy),
    ("Clearance stack", _check_clearance_stack),
    ("Default agents", _check_default_agents),
]
"""Global checks paired with the label shown while they run.

The check functions return their own label inside the ``_CheckResult``
tuple, but we want to stream ``"  Gate server …… "`` *before* the
check runs — so the user sees progress even on slow probes.  The
label printed up front should match the one the check returns; if it
ever drifts, the streamed line and the final marker end up on different
rows and the output looks broken.
"""


def _cmd_sickbay(
    project_id: str | None = None,
    task_id: str | None = None,
    fix: bool = False,
) -> None:
    """Run health checks and report results, streaming progress line-by-line."""
    reporter = CheckReporter()

    if not task_id:
        for label, check in _GLOBAL_CHECKS:
            reporter.begin(label)
            status, _, detail = check()
            reporter.end(status, detail)
        # Visual separator between host-wide checks and per-project /
        # per-task rows that follow — same intent as ``terok setup``'s
        # blank line between stage groups.
        print()

    for status, label, detail in _check_unfired_hooks(project_id, task_id, fix=fix):
        reporter.emit(status, label, detail)
    for status, label, detail in _check_shield_annotations(project_id, task_id):
        reporter.emit(status, label, detail)

    _stream_containers(project_id, task_id, fix=fix, reporter=reporter)

    # Single-task summary: ``ok (consistent)`` iff every check for this
    # task came back clean.  Globals aren't run in the ``task_id`` scope,
    # so the reporter's worst-status at this point covers exactly the
    # three task-scoped check sets.
    if task_id and reporter.worst_status == "ok":
        reporter.emit("ok", f"Task {project_id}/{task_id}", "consistent")

    if reporter.worst_status == "error":
        sys.exit(2)
    elif reporter.worst_status == "warn":
        sys.exit(1)
