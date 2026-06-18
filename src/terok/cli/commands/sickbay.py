# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Health check and reconciliation command (DS9-themed diagnostic bay).

Runs a series of checks and reports their status.  With ``--fix``,
auto-remediates issues like unfired post_stop hooks.

Scoping:
- ``terok sickbay`` — all projects
- ``terok sickbay <project>`` — single project
- ``terok sickbay <project> <task>`` — single task
- ``terok sickbay --system`` — host-wide checks only (shield, vault,
  recovery, ssh signer, selinux, default agents); skips the slow
  per-container walk.  Mutually exclusive with a project/task scope.

``--fix`` is orthogonal to scope and auto-remediates (e.g. unfired
post_stop hooks); it has no effect under ``--system``, which runs no
container-level checks.  Exit codes are unchanged by either flag.

Exit codes:
- 0: all checks passed
- 1: warnings present
- 2: errors present
"""

from __future__ import annotations

import argparse
import sys
from contextlib import suppress
from pathlib import Path

from terok.lib.api.setup import (
    SERVICES_TCP_OPTOUT_YAML,
    check_environment,
    resolve_container_state_dir,
    systemd_creds_has_tpm2,
)
from terok.lib.api.vault import VaultStatusSnapshot

from ...lib.core import runtime as _rt
from ...lib.core.config import get_services_mode, global_config_path
from ...lib.core.project_model import ProjectConfig, is_valid_project_name
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
    # dest=project_name / task_id matches the rest of the CLI so the shared
    # completers work; metavar keeps the display ``<project>``/``<task>``.
    from ._completers import add_project_name, add_task_id

    add_project_name(p, nargs="?", metavar="project", help="Scope to a single project")
    add_task_id(p, nargs="?", metavar="task", help="Scope to a single task")
    p.add_argument("--fix", action="store_true", help="Auto-remediate issues")
    p.add_argument(
        "--system",
        action="store_true",
        help="Only host-wide checks; skip the per-container walk (fast)",
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the sickbay command.  Returns True if handled."""
    if args.cmd != "sickbay":
        return False
    project_name = getattr(args, "project_name", None)
    task_id = getattr(args, "task_id", None)
    system_only = getattr(args, "system", False)
    # --system runs only the host-wide checks, so a project/task scope is a
    # contradiction in terms — reject it rather than silently ignoring one.
    if system_only and (project_name or task_id):
        sys.exit("error: --system runs host-wide checks only; drop the project/task argument")
    if project_name and task_id:
        task_id = resolve_task_id(project_name, task_id)
    _cmd_sickbay(
        project_name=project_name,
        task_id=task_id,
        fix=getattr(args, "fix", False),
        system_only=system_only,
    )
    return True


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
    """Check vault store state — locked, populated, plaintext-on-disk.

    Every container's supervisor embeds its own vault proxy, so the
    host-side check reduces to DB-side facts — does the resolver chain
    unlock the store, what's in it, and is the passphrase exposed on
    disk?  Per-container proxy
    health is reported by
    [`ContainerDoctor`][terok.lib.orchestration.container_doctor.ContainerDoctor]
    against each running task individually.
    """
    label = "Vault"
    try:
        status = VaultStatusSnapshot.load()
    except Exception as exc:  # noqa: BLE001
        return ("warn", label, f"check failed — {exc}")

    if status.db_error is not None:
        return ("warn", label, f"DB error — {status.db_error}")

    if status.locked:
        # The reason separates "no passphrase" / "wrong passphrase" /
        # "broken tier" — three different remedies behind one word.
        reason = f" ({status.lock_reason})" if status.lock_reason else ""
        return (
            "warn",
            label,
            f"locked{reason} — run 'terok vault unlock' to make stored credentials available",
        )

    creds = len(status.credentials_stored or ())
    parts: list[str] = []
    tier = _passphrase_tier_label(status.passphrase_source)
    if tier:
        parts.append(tier)
    parts.append(f"{creds} credential(s) stored")
    if status.plaintext_passphrase_path is not None:
        parts.append("plaintext passphrase on disk")
    detail = ", ".join(parts)
    if status.plaintext_passphrase_path is not None:
        return ("warn", label, detail)
    return ("ok", label, detail)


def _check_vault_shadow(*, fix: bool) -> list[_CheckResult]:
    """Detect — and with ``--fix``, clear — a session file that shadows a durable tier.

    A session-unlock file holding the *same* passphrase a durable tier
    (systemd-creds / keyring / config) already resolves is redundant
    residue — typically a stray ``vault unlock`` on a host that already
    auto-unlocks (the #1070 footgun).  ``--fix`` removes it; a
    *different*-key session file is a deliberate override / re-key and is
    reported but never auto-removed.  Resolves a tier only when a shadow
    is actually present, so it's a cheap no-op on the common path.
    """
    from terok.lib.api.vault import clear_redundant_session_file, session_shadow_state
    from terok.lib.core.config import make_sandbox_config

    label = "Vault shadow"
    try:
        cfg = make_sandbox_config()
        shadow = session_shadow_state(cfg)
    except Exception as exc:  # noqa: BLE001 — a diagnostic must never crash sickbay
        return [("warn", label, f"check failed — {exc}")]
    if shadow is None:
        return []
    src = shadow.durable_source
    if shadow.redundant is True:
        if not fix:
            return [
                (
                    "warn",
                    label,
                    f"session-file duplicates {src} (same passphrase) — --fix removes it",
                )
            ]
        removed = clear_redundant_session_file(cfg)
        if removed:
            return [("ok", label, f"removed redundant session copy of {removed}")]
        return [("ok", label, "redundant session copy already gone")]
    if shadow.redundant is False:
        return [
            (
                "warn",
                label,
                f"session-file shadows {src} with a DIFFERENT passphrase"
                " — deliberate override or stale unlock",
            )
        ]
    return [("warn", label, f"session-file shadows {src}, which could not be read to compare")]


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
    if not is_valid_project_name(pid) or not is_task_id(tid):
        return None
    return meta_path(tasks_meta_dir(pid), tid)


def _check_task_hook(
    pid: str, tid: str, project: ProjectConfig, *, fix: bool
) -> _CheckResult | None:
    """Check a single task for unfired post_stop hook.  Returns None if ok."""
    if not is_valid_project_name(pid) or not is_task_id(tid):
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
            project_name=pid,
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
    if not is_valid_project_name(pid) or not is_task_id(tid):
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
    project_name: str | None, task_id: str | None, *, fix: bool
) -> list[_CheckResult]:
    """Check for stopped tasks with unfired post_stop hooks."""
    results: list[_CheckResult] = []

    if project_name:
        projects = [(project_name, load_project(project_name))]
    else:
        projects = [(p.name, p) for p in list_projects()]

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


def _check_shield_annotations(project_name: str | None, task_id: str | None) -> list[_CheckResult]:
    """Check that every running task's container carries the expected shield annotation."""
    results: list[_CheckResult] = []

    if project_name:
        projects = [(project_name, load_project(project_name))]
    else:
        projects = [(p.name, p) for p in list_projects()]

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
    """Strip C0/C1 control characters from a project name for safe terminal output."""
    import unicodedata

    return "".join(
        " " if ch in "\n\r\t" else f"\\x{ord(ch):02x}" if unicodedata.category(ch)[0] == "C" else ch
        for ch in value
    )


def _abbreviate(ids: list[str], limit: int = 3) -> str:
    """Join project names with a '+N more' suffix when the list is long."""
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

    unregistered = [p.name for p in projects if p.name not in assigned_scopes]
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
    project_name: str | None,
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
    if project_name and task_id:
        ContainerDoctor(project_name, task_id).run(
            fix=fix,
            reporter=reporter,
            label_prefix=f"Task {project_name}/{task_id}: ",
        )
        return

    # Project or global scope — iterate all known tasks
    if project_name:
        projects = [(project_name, load_project(project_name))]
    else:
        projects = [(p.name, p) for p in list_projects()]

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
        case SelinuxStatus.POLICY_OUTDATED:
            return (
                "warn",
                label,
                "terok_socket_t policy is outdated — it predates the per-container "
                "supervisor and lacks the rule it binds its sockets with. "
                f"Rebuild: {selinux_install_command()}",
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


def _check_stray_sidecars() -> _CheckResult:
    """Sweep supervisor sidecars stranded by out-of-band container removal.

    Sandbox-side, host-level check
    ([`make_stray_sidecar_check`][terok_sandbox.launch.make_stray_sidecar_check]):
    sidecars deliberately survive container stops so restarts come back
    supervised, and ``task delete`` sweeps them — a container removed
    behind terok's back (bare ``podman rm``) strands its file until
    this reconcile removes it.  The check acts on what it finds and
    reports what it swept.
    """
    label = "Stray sidecars"
    try:
        from terok.lib.integrations.sandbox import make_stray_sidecar_check

        from ...lib.core.config import make_sandbox_config

        verdict = make_stray_sidecar_check(make_sandbox_config()).evaluate(0, "", "")
    except Exception as exc:  # noqa: BLE001 — probe is best-effort; never crash sickbay
        return ("warn", label, f"check failed — {exc}")
    return (verdict.severity, label, verdict.detail)


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
    ("Shield", _check_shield),
    ("Vault", _check_vault),
    ("Recovery key acknowledged", _check_recovery_acknowledged),
    ("SSH signer", _check_ssh_signer),
    ("SELinux policy", _check_selinux_policy),
    ("Stray sidecars", _check_stray_sidecars),
    ("Default agents", _check_default_agents),
]
"""Global checks paired with the label shown while they run.

The check functions return their own label inside the ``_CheckResult``
tuple, but we want to stream ``"  Shield …… "`` *before* the
check runs — so the user sees progress even on slow probes.  The
label printed up front should match the one the check returns; if it
ever drifts, the streamed line and the final marker end up on different
rows and the output looks broken.
"""


def _cmd_sickbay(
    project_name: str | None = None,
    task_id: str | None = None,
    fix: bool = False,
    system_only: bool = False,
) -> None:
    """Run health checks and report results, streaming progress line-by-line."""
    reporter = CheckReporter()

    if not task_id:
        for label, check in _GLOBAL_CHECKS:
            reporter.begin(label)
            status, _, detail = check()
            reporter.end(status, detail)
        # Host-level remediation: a redundant session-file shadow of a
        # durable tier is cleared here under --fix (warn-only otherwise).
        # Runs in --system too — it's a host concern, not per-container.
        for status, label, detail in _check_vault_shadow(fix=fix):
            reporter.emit(status, label, detail)
        # Visual separator between host-wide checks and per-project /
        # per-task rows that follow — same intent as ``terok setup``'s
        # blank line between stage groups.  ``--system`` prints no such
        # rows, so the separator would just dangle.
        if not system_only:
            print()

    # The per-container walk resolves podman state for every task — the slow
    # part sickbay is known for.  ``--system`` runs only the host-wide checks
    # above and falls straight through to the exit-code summary.
    if not system_only:
        for status, label, detail in _check_unfired_hooks(project_name, task_id, fix=fix):
            reporter.emit(status, label, detail)
        for status, label, detail in _check_shield_annotations(project_name, task_id):
            reporter.emit(status, label, detail)

        _stream_containers(project_name, task_id, fix=fix, reporter=reporter)

        # Single-task summary: ``ok (consistent)`` iff every check for this
        # task came back clean.  Globals aren't run in the ``task_id`` scope,
        # so the reporter's worst-status at this point covers exactly the
        # three task-scoped check sets.
        if task_id and reporter.worst_status == "ok":
            reporter.emit("ok", f"Task {project_name}/{task_id}", "consistent")

    if reporter.worst_status == "error":
        sys.exit(2)
    elif reporter.worst_status == "warn":
        sys.exit(1)
