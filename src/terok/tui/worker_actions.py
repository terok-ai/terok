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


def generate(project_id: str) -> None:
    """Generate Dockerfiles for *project_id*."""
    from terok.lib.api import generate_dockerfiles

    generate_dockerfiles(project_id)


def build(project_id: str) -> None:
    """Build the L2 project images for *project_id* (reuses cached L0/L1)."""
    from terok.lib.api import build_images

    build_images(project_id)


def build_agents(project_id: str) -> None:
    """Rebuild *project_id* from L0 with a fresh agent set."""
    from terok.lib.api import build_images

    build_images(project_id, refresh_agents=True)


def build_full(project_id: str) -> None:
    """Rebuild *project_id* from L0 with no cache."""
    from terok.lib.api import build_images

    build_images(project_id, full_rebuild=True)


def init_ssh(project_id: str) -> None:
    """Mint a vault-backed SSH keypair for *project_id* and print its summary."""
    from terok.lib.api import get_project, summarize_ssh_init

    summarize_ssh_init(get_project(project_id).provision_ssh_key())


# Full project setup ("Full setup" project-screen action) is *not* a
# worker_actions entrypoint: it needs the interactive deploy-key
# registration pause, which a stdin-less child process cannot do.  It
# reuses the wizard's InitProgressScreen instead — see
# ProjectActionsMixin._action_project_init.


# ── Authentication ────────────────────────────────────────────────────


def auth(provider: str, project_id: str | None) -> None:
    """Run the auth flow for *provider*; *project_id* ``None`` means host-wide."""
    from terok.lib.api import authenticate

    authenticate(provider, project_id)


# ── Gate sync ─────────────────────────────────────────────────────────


def _lookup_vault_pub_line(scope: str) -> str | None:
    """Return *scope*'s most-recent public key line, or ``None`` if unassigned."""
    from terok.lib.api import vault_db
    from terok.lib.api.setup import public_line_of

    with vault_db() as db:
        records = db.load_ssh_keys_for_scope(scope)
    return public_line_of(records[-1]) if records else None


def _print_sync_gate_ssh_help(project_id: str) -> None:
    """Print SSH-specific troubleshooting for a gate-sync failure.

    Best-effort: a project that cannot be loaded just means no hint —
    ``load_project`` here always succeeds in practice (``sync_gate``
    loaded it moments earlier), so a failure is genuinely exceptional
    and any ``SystemExit`` is left to propagate rather than swallowed.
    """
    from terok.lib.api import load_project
    from terok.lib.api.setup import is_ssh_url

    try:
        project = load_project(project_id)
    except Exception:
        return
    if not is_ssh_url(project.upstream_url):
        return

    print("\nHint: this project uses an SSH upstream.")
    print("Gate sync failures are often a missing SSH key registration on the remote.")
    pub_line = _lookup_vault_pub_line(project.id)
    if pub_line is not None:
        print("Public key (register as a deploy key on the remote):")
        print(f"  {pub_line}")
    else:
        print(f"No SSH key assigned to project (scope) {project.id!r} in the vault.")
        print(f"Run 'terok project ssh-init {project_id}' to generate one,")
        print("then register the printed public key as a deploy key upstream.")


def sync_gate(project_id: str) -> None:
    """Sync (creating if absent) the git gate for *project_id* from upstream."""
    from terok.lib.api import load_project, make_git_gate

    print(f"Syncing gate for {project_id}...")
    try:
        result = make_git_gate(load_project(project_id)).sync()
    except SystemExit as exc:
        _print_sync_gate_ssh_help(project_id)
        raise SystemExit(f"Gate sync failed: {exc}") from exc
    if result["success"]:
        print(
            "Gate created and synced from upstream."
            if result["created"]
            else "Gate synced from upstream."
        )
        return
    _print_sync_gate_ssh_help(project_id)
    raise SystemExit(f"Gate sync failed: {', '.join(result['errors'])}")


# ── Gate server ───────────────────────────────────────────────────────


def gate_install() -> None:
    """Install the gate server's systemd socket units."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.api.gate import GateServerManager

    GateServerManager(make_sandbox_config()).install_systemd_units()


def gate_uninstall() -> None:
    """Uninstall the gate server's systemd units."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.api.gate import GateServerManager

    GateServerManager(make_sandbox_config()).uninstall_systemd_units()


def gate_start() -> None:
    """Start the gate server daemon."""
    from terok.lib.api.gate import GateServerManager

    GateServerManager().start_daemon()


def gate_stop() -> None:
    """Stop the gate server daemon."""
    from terok.lib.api.gate import GateServerManager

    GateServerManager().stop_daemon()


# ── Shield ────────────────────────────────────────────────────────────


def shield_setup(root: bool) -> None:
    """Install shield git hooks — *root* selects the root-scoped install."""
    from terok.lib.api.setup import setup_hooks_direct

    setup_hooks_direct(root=root)


# ── Vault ─────────────────────────────────────────────────────────────


def vault_install() -> None:
    """Generate vault routes and install its systemd socket units."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.api.agents import ensure_vault_routes
    from terok.lib.api.vault import VaultManager

    cfg = make_sandbox_config()
    ensure_vault_routes(cfg=cfg)
    VaultManager(cfg).install_systemd_units()


def vault_uninstall() -> None:
    """Uninstall the vault's systemd units."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.api.vault import VaultManager

    VaultManager(make_sandbox_config()).uninstall_systemd_units()


def vault_start() -> None:
    """Generate vault routes and start the vault daemon."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.api.agents import ensure_vault_routes
    from terok.lib.api.vault import VaultManager

    cfg = make_sandbox_config()
    ensure_vault_routes(cfg=cfg)
    VaultManager(cfg).start_daemon()


def vault_stop() -> None:
    """Stop the vault daemon."""
    from terok.lib.api.vault import VaultManager

    VaultManager().stop_daemon()


def vault_lock() -> None:
    """Clear the session-tier passphrase file and stop the vault daemon."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.api.vault import VaultManager

    cfg = make_sandbox_config()
    cfg.vault_passphrase_file.unlink(missing_ok=True)
    VaultManager(cfg).stop_daemon()


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


def selinux_install_policy() -> None:
    """Run the bundled SELinux installer with ``sudo bash`` and stream the output.

    Delegates to the ``install_policy.sh`` script terok-sandbox ships
    in its resources — same one ``terok setup`` prints when the policy
    is missing.  Output (including any sudo prompt) lands in the
    captured-log view so the operator can authenticate inline.

    ``sudo`` and ``bash`` are looked up via [`shutil.which`][shutil.which]
    so the subprocess gets an absolute executable path — keeps
    bandit (B607 partial-path), SonarCloud, and a hostile ``PATH``
    all out of the picture.  Failing either lookup turns into a clear
    [`SystemExit`][SystemExit] rather than a confusing ``FileNotFoundError``.
    """
    import shutil
    import subprocess  # noqa: S404 — running sudo to load a bundled SELinux policy is the whole point of this verb  # nosec B404

    from terok.lib.api.setup import selinux_install_script

    sudo = shutil.which("sudo")
    bash = shutil.which("bash")
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
    caller launches immediately after this returns.
    """
    from terok.lib.api.setup import yaml_update_section
    from terok.lib.core.config import global_config_path

    user_config = global_config_path()
    user_config.parent.mkdir(parents=True, exist_ok=True)
    yaml_update_section(user_config, "services", {"mode": "tcp"})
    print(f"→ wrote services.mode=tcp to {user_config}")


# ── Task lifecycle ────────────────────────────────────────────────────


def task_restart(project_id: str, task_id: str) -> None:
    """Restart *task_id*'s container (stopping it first if running)."""
    from terok.lib.api import task_restart as _task_restart

    _task_restart(project_id, task_id)


def task_stop(project_id: str, task_id: str) -> None:
    """Stop *task_id*'s running container."""
    from terok.lib.api import task_stop as _task_stop

    _task_stop(project_id, task_id)


def start_cli_container(project_id: str, task_id: str) -> None:
    """Start the CLI container for the already-created task *task_id*."""
    from terok.lib.api import task_run_cli

    task_run_cli(project_id, task_id)


def start_toad_container(project_id: str, task_id: str) -> None:
    """Start the Toad container for the already-created task *task_id*."""
    from terok.lib.api import task_run_toad

    task_run_toad(project_id, task_id)
