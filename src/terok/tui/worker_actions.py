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
    from terok.lib.api import provision_ssh_key, summarize_ssh_init

    summarize_ssh_init(provision_ssh_key(project_id))


def project_init(project_id: str) -> None:
    """Full project setup for *project_id*: ssh-init, generate, build, gate-sync.

    Unlike the CLI's ``terok project init``, this does **not** pause for
    interactive deploy-key registration — a child process has no stdin.
    The freshly minted public key is printed by the ssh-init step; if
    the gate sync below then fails because it is not yet registered
    upstream, the log says so and the operator re-runs gate-sync from
    the project screen once the key is in place.
    """
    from terok.lib.api import (
        build_images,
        generate_dockerfiles,
        load_project,
        make_git_gate,
        provision_ssh_key,
        summarize_ssh_init,
    )

    print(f"=== Full Setup for {project_id} ===\n")
    print("Step 1/4: Initializing SSH...")
    summarize_ssh_init(provision_ssh_key(project_id))
    print(
        "\nIf this project uses an SSH upstream, register the public key shown "
        "above as a deploy key before the gate sync below — otherwise re-run "
        "gate-sync from the project screen once it is registered."
    )
    print("\nStep 2/4: Generating Dockerfiles...")
    generate_dockerfiles(project_id)
    print("\nStep 3/4: Building images...")
    build_images(project_id)
    print("\nStep 4/4: Syncing git gate...")
    result = make_git_gate(load_project(project_id)).sync()
    if result["success"]:
        print(f"\nGate ready at {result['path']}")
    else:
        print(f"\nGate sync warnings: {', '.join(result['errors'])}")
    print("\n=== Full Setup complete! ===")


# ── Authentication ────────────────────────────────────────────────────


def auth(provider: str, project_id: str | None) -> None:
    """Run the auth flow for *provider*; *project_id* ``None`` means host-wide."""
    from terok.lib.api import authenticate

    authenticate(provider, project_id)


# ── Gate sync ─────────────────────────────────────────────────────────


def _lookup_vault_pub_line(scope: str) -> str | None:
    """Return *scope*'s most-recent public key line, or ``None`` if unassigned."""
    from terok.lib.api import vault_db
    from terok.lib.integrations.sandbox import public_line_of

    with vault_db() as db:
        records = db.load_ssh_keys_for_scope(scope)
    return public_line_of(records[-1]) if records else None


def _print_sync_gate_ssh_help(project_id: str) -> None:
    """Print SSH-specific troubleshooting for a gate-sync failure."""
    from terok.lib.api import load_project
    from terok.lib.integrations.sandbox import is_ssh_url

    try:
        project = load_project(project_id)
    except (Exception, SystemExit):
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
    from terok.lib.integrations.sandbox import GateServerManager

    GateServerManager(make_sandbox_config()).install_systemd_units()


def gate_uninstall() -> None:
    """Uninstall the gate server's systemd units."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.integrations.sandbox import GateServerManager

    GateServerManager(make_sandbox_config()).uninstall_systemd_units()


def gate_start() -> None:
    """Start the gate server daemon."""
    from terok.lib.integrations.sandbox import start_daemon

    start_daemon()


def gate_stop() -> None:
    """Stop the gate server daemon."""
    from terok.lib.integrations.sandbox import stop_daemon

    stop_daemon()


# ── Shield ────────────────────────────────────────────────────────────


def shield_setup(root: bool) -> None:
    """Install shield git hooks — *root* selects the root-scoped install."""
    from terok.lib.integrations.sandbox import setup_hooks_direct

    setup_hooks_direct(root=root)


# ── Vault ─────────────────────────────────────────────────────────────


def vault_install() -> None:
    """Generate vault routes and install its systemd socket units."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.integrations.executor import ensure_vault_routes
    from terok.lib.integrations.sandbox import VaultManager

    cfg = make_sandbox_config()
    ensure_vault_routes(cfg=cfg)
    VaultManager(cfg).install_systemd_units()


def vault_uninstall() -> None:
    """Uninstall the vault's systemd units."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.integrations.sandbox import VaultManager

    VaultManager(make_sandbox_config()).uninstall_systemd_units()


def vault_start() -> None:
    """Generate vault routes and start the vault daemon."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.integrations.executor import ensure_vault_routes
    from terok.lib.integrations.sandbox import start_vault

    ensure_vault_routes(cfg=make_sandbox_config())
    start_vault(cfg=make_sandbox_config())


def vault_stop() -> None:
    """Stop the vault daemon."""
    from terok.lib.integrations.sandbox import stop_vault

    stop_vault()


def vault_lock() -> None:
    """Clear the session-tier passphrase file and stop the vault daemon."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.integrations.sandbox import stop_vault

    make_sandbox_config().vault_passphrase_file.unlink(missing_ok=True)
    stop_vault()


def vault_seal() -> None:
    """Seal the resolved passphrase into a systemd-creds credential (``--key=auto``)."""
    from terok.lib.api import make_sandbox_config
    from terok.lib.integrations.sandbox import _handle_vault_seal

    _handle_vault_seal(cfg=make_sandbox_config(), key="auto")


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
