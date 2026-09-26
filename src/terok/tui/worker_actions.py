# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Child-process entrypoints for the TUI's dispatched (Type-1) actions.

Each function here is the callable a ConsoleLog dispatch references —
run in a fresh process by the ``_worker_entry`` module so its output
(and any ``podman`` / ``git`` it shells out to) is captured cleanly
instead of corrupting the Textual frame (issue #473).

The functions are intentionally thin: the real work lives in the
facade ([`terok.lib.api`][terok.lib.api]) and the sandbox
integrations.  These adapters exist so every TUI action maps to a
*single*, importable, JSON-positional-args-able call — which is what
``dispatch_console_action`` needs and what keeps the TUI decoupled
from facade keyword-argument signatures.

Imports are function-local: this module is loaded in the child process
on every dispatch, and a lazy import keeps that cheap when only one
action is being run.
"""

from __future__ import annotations

# ── Project infrastructure ────────────────────────────────────────────


def generate(project_name: str) -> None:
    """Generate Dockerfiles for *project_name*."""
    from terok.lib.api import generate_dockerfiles

    generate_dockerfiles(project_name)


def build(project_name: str) -> None:
    """Build the L2 project images for *project_name* (reuses cached L0/L1)."""
    from terok.lib.api import build_images

    build_images(project_name)


def build_agents(project_name: str) -> None:
    """Rebuild *project_name* from L1 with a fresh agent set."""
    from terok.lib.api import build_images

    build_images(project_name, refresh_agents=True)


def build_full(project_name: str) -> None:
    """Rebuild *project_name* from L0 with no cache."""
    from terok.lib.api import build_images

    build_images(project_name, full_rebuild=True)


def init_ssh(project_name: str, comment: str | None = None) -> None:
    """Initialize a vault-backed key with the chosen comment and print its summary."""
    from terok.lib.api import get_project, summarize_ssh_init

    summarize_ssh_init(get_project(project_name).provision_ssh_key(comment=comment))


# Full project setup ("Full setup" project-screen action) is *not* a
# worker_actions entrypoint: it needs the interactive deploy-key
# registration pause, which a stdin-less child process cannot do.  It
# reuses the wizard's InitProgressScreen instead — see
# ProjectActionsMixin._action_project_init.


# ── Gate sync ─────────────────────────────────────────────────────────


def _lookup_vault_pub_line(scope: str) -> str | None:
    """Return *scope*'s default public key line, or ``None`` if unassigned."""
    from terok.lib.api import vault_db
    from terok.lib.api.setup import public_line_of

    with vault_db() as db:
        records = db.load_ssh_keys_for_scope(scope)
    return public_line_of(records[0]) if records else None


def _print_sync_gate_ssh_help(project_name: str) -> None:
    """Print SSH-specific troubleshooting for a gate-sync failure.

    Best-effort: a project that cannot be loaded just means no hint —
    ``load_project`` here always succeeds in practice (``sync_gate``
    loaded it moments earlier), so a failure is genuinely exceptional
    and any ``SystemExit`` is left to propagate rather than swallowed.
    """
    from terok.lib.api import load_project
    from terok.lib.api.setup import is_ssh_url

    try:
        project = load_project(project_name)
    except Exception:
        return
    if not is_ssh_url(project.upstream_url):
        return

    print("\nHint: this project uses an SSH upstream.")
    print("Gate sync failures are often a missing SSH key registration on the remote.")
    pub_line = _lookup_vault_pub_line(project.name)
    if pub_line is not None:
        print("Public key (register as a deploy key on the remote):")
        print(f"  {pub_line}")
    else:
        print(f"No SSH key assigned to project (scope) {project.name!r} in the vault.")
        print(f"Run 'terok project ssh-init {project_name}' to generate one,")
        print("then register the printed public key as a deploy key upstream.")


def sync_gate(project_name: str) -> None:
    """Sync (creating if absent) the git gate for *project_name* from upstream.

    Prints the abridged per-branch report into the worker log — the sync
    itself is safe by construction (creates and fast-forwards only), and
    any pending destructive ops it lists are confirmed separately through
    the TUI modal that the sync action opens afterwards.
    """
    from terok.lib.api import load_project, make_git_gate
    from terok.lib.api.gate import summarize_gate_sync

    print(f"Syncing gate for {project_name}...")
    try:
        result = make_git_gate(load_project(project_name)).sync()
    except SystemExit as exc:
        _print_sync_gate_ssh_help(project_name)
        raise SystemExit(f"Gate sync failed: {exc}") from exc
    if result["success"]:
        print(
            "Gate created and synced from upstream."
            if result["created"]
            else "Gate synced from upstream."
        )
        for line in summarize_gate_sync(result):
            print(line)
        if result.get("cache_error"):
            print(f"Warning: clone cache refresh failed: {result['cache_error']}")
            print("New tasks fall back to a full clone until the next successful sync.")
        return
    _print_sync_gate_ssh_help(project_name)
    raise SystemExit(f"Gate sync failed: {', '.join(result['errors'])}")


def apply_gate_ops(project_name: str, ops: list[dict]) -> None:
    """Apply operator-confirmed destructive gate ops for *project_name*.

    *ops* is the JSON round-trip of the ``PendingOp`` dicts the operator
    saw in the confirmation modal.  Each op is compare-and-swap guarded
    inside the gate, so an agent push that happened since the modal was
    shown fails that op with a visible message instead of losing the push.
    """
    from typing import cast

    from terok.lib.api import load_project, make_git_gate
    from terok.lib.api.gate import PendingOp

    gate = make_git_gate(load_project(project_name))
    result = gate.apply_pending_ops(cast("list[PendingOp]", ops))
    for op in result["applied"]:
        backup = result["backups"].get(op["branch"])
        backup_note = f"  (old tip saved as {backup})" if backup else ""
        print(f"applied: {op['kind']} {op['branch']}{backup_note}")
    for error in result["errors"]:
        print(f"skipped: {error}")
    if not result["success"]:
        raise SystemExit("Some gate changes were not applied — see above.")


def restore_gate_backup(project_name: str, ref: str) -> None:
    """Restore a branch to backup *ref* for *project_name*.

    The gate backs up the tip being replaced before moving the branch, so
    the restore is itself reversible; a branch that moved since the
    backups were listed fails the compare-and-swap with a visible message
    rather than clobbering the newer tip.
    """
    from terok.lib.api import load_project, make_git_gate

    gate = make_git_gate(load_project(project_name))
    result = gate.restore_backup(ref)
    if result["error"] is not None:
        raise SystemExit(f"Restore failed: {result['error']}")
    print(f"Restored {result['branch']} to {(result['restored_sha'] or '')[:12]}.")
    if result["previous_backup_ref"] is not None:
        print(f"Previous tip saved as {result['previous_backup_ref']}")


def delete_gate_backup(project_name: str, ref: str) -> None:
    """Delete a single gate backup *ref* for *project_name*."""
    from terok.lib.api import load_project, make_git_gate

    gate = make_git_gate(load_project(project_name))
    error = gate.delete_backup(ref)
    if error is not None:
        raise SystemExit(f"Delete failed: {error}")
    print(f"Deleted {ref}.")


# ── Shield ────────────────────────────────────────────────────────────


def shield_setup() -> None:
    """Install shield OCI hooks into the canonical terok-owned dir."""
    from terok.lib.api.setup import ShieldHooks

    ShieldHooks.install()


# ── Vault ─────────────────────────────────────────────────────────────
#
# Every container's supervisor embeds its own vault proxy, so there's
# no host-side daemon to operate — no install / uninstall / start / stop
# verbs.  The vault actions here are passphrase management — lock the
# session tier, move it between tiers, seal it into systemd-creds — all
# DB-side, no IPC.


def vault_lock() -> None:
    """Lock the vault — clear every stored copy of the passphrase.

    Removes the session file *and* every durable tier (keyring, sealed
    systemd-creds, plaintext config): against a machine-bound tier a
    soft-lock would just auto-unlock on the next access.  Reversible only
    by re-supplying the passphrase — the action gates this behind a
    confirmation modal (see ``_action_vault_lock``), so this worker runs
    only after the operator has agreed.
    """
    from terok.lib.api import make_sandbox_config
    from terok.lib.api.vault import purge_passphrase_tiers

    purge_passphrase_tiers(make_sandbox_config())


def vault_seal() -> None:
    """Seal the resolved passphrase into a systemd-creds credential (``--key=auto``)."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.api.vault import handle_vault_seal

    handle_vault_seal(cfg=make_sandbox_config(), key="auto")


def vault_to_keyring() -> None:
    """Move the resolved passphrase from its current tier into the OS keyring."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.api.vault import handle_vault_to_keyring

    handle_vault_to_keyring(cfg=make_sandbox_config())


def vault_rekey_restart_tasks(tasks: list[list[str]]) -> None:
    """Restart the tasks that were stopped so the credentials DB could be re-encrypted.

    *tasks* rows are ``[project, task_id]`` pairs (JSON-positional shape).
    Each restart's ladder output streams into the console log; a failed
    task never blocks the rest.  Exits non-zero listing the casualties so
    the log view flags the run — the operator restarts those by hand.
    """
    from terok.lib.api.vault import RunningTask, restart_tasks_after_rekey

    failures = [
        (task, error)
        for task, error in restart_tasks_after_rekey(
            RunningTask(project, task_id) for project, task_id in tasks
        )
        if error is not None
    ]
    if failures:
        details = "\n".join(f"  ✗ {task}: {error}" for task, error in failures)
        raise SystemExit(
            f"some tasks could not be restarted:\n{details}\n"
            "bring each back manually with: terok task restart <project> <task>"
        )


def selinux_install_policy() -> None:
    """Run the bundled SELinux installer with ``sudo bash`` and stream the output.

    Delegates to the ``install_policy.sh`` script terok-sandbox ships
    in its resources — the same script ``terok setup selinux`` runs
    after its command-and-rules preview; this TUI path skips the
    preview because the modal that invokes it already asked.  Output
    (including any sudo prompt) lands in the captured-log view so the
    operator can authenticate inline.

    ``sudo`` and ``bash`` are looked up via [`find_host_tool`][terok_util.host_tools.find_host_tool]
    using the current absolute PATH entries. Failing either lookup turns into a clear
    [`SystemExit`][SystemExit] rather than a confusing ``FileNotFoundError``.
    """
    import subprocess  # noqa: S404 — running sudo to load a bundled SELinux policy is the whole point of this verb  # nosec B404

    from terok_util import find_host_tool

    from terok.lib.api.setup import selinux_install_script

    sudo = find_host_tool("sudo")
    bash = find_host_tool("bash")
    if sudo is None or bash is None:
        missing = "sudo" if sudo is None else "bash"
        raise SystemExit(
            f"selinux_install_policy: {missing} not on PATH — install it or run "
            "the bundled script manually."
        )
    # Stream stdout/stderr to the parent process so ConsoleLog captures
    # them line-by-line — same shape as every other dispatched action.
    subprocess.run(  # noqa: S603 — argv built from absolute paths + a bundled script  # nosec B603
        [sudo, bash, str(selinux_install_script())],
        check=True,
    )


def selinux_switch_to_tcp() -> None:
    """Flip ``services.mode`` to ``tcp`` in the user-scope config.yml.

    Writes only the ``services.mode`` field; preserves any other
    user-supplied config via terok-sandbox's round-trip YAML writer.
    The new value takes effect on the next setup run — which the
    caller launches immediately after this returns.  Per-project
    ``services.mode`` overrides in ``project.yml`` are deliberately
    untouched: a project pinning ``socket`` keeps it (and keeps
    needing the SELinux policy).
    """
    from terok.lib.api.setup import yaml_update_section
    from terok.lib.core.config import global_config_path

    user_config = global_config_path()
    user_config.parent.mkdir(parents=True, exist_ok=True)
    yaml_update_section(user_config, "services", {"mode": "tcp"})
    print(f"→ wrote services.mode=tcp to {user_config}")


# ── Task lifecycle ────────────────────────────────────────────────────


def task_restart(project_name: str, task_id: str) -> None:
    """Restart *task_id*'s container (stopping it first if running).

    Resumes the container in place, keeping it as-is even when the
    project image was rebuilt — a stale image is warned about, not
    upgraded.  Use [`task_recreate`][terok.tui.worker_actions.task_recreate]
    to pick up a rebuilt image.
    """
    from terok.lib.api import task_restart as _task_restart

    _task_restart(project_name, task_id)


def task_recreate(project_name: str, task_id: str) -> None:
    """Recreate + restart *task_id*'s container, picking up a rebuilt image.

    The recreate flavor of [`task_restart`][terok.tui.worker_actions.task_restart]:
    tears the container down and relaunches it into the same name and
    workspace, upgrading a task that a plain restart left on its old
    image.
    """
    from terok.lib.api import task_restart as _task_restart

    _task_restart(project_name, task_id, fresh=True)


def task_stop(project_name: str, task_id: str) -> None:
    """Stop *task_id*'s running container."""
    from terok.lib.api import task_stop as _task_stop

    _task_stop(project_name, task_id)


def start_cli_container(project_name: str, task_id: str) -> None:
    """Start the CLI container for the already-created task *task_id*."""
    from terok.lib.api import task_run_cli

    task_run_cli(project_name, task_id)


def start_toad_container(project_name: str, task_id: str) -> None:
    """Start the Toad container for the already-created task *task_id*."""
    from terok.lib.api import task_run_toad

    task_run_toad(project_name, task_id)
