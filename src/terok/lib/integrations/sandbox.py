# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Adapter for the ``terok_sandbox`` wheel (and its re-exports of shield).

Re-exports every symbol terok consumes from terok-sandbox.  Callers
elsewhere in terok import from this module rather than from
``terok_sandbox`` directly — see the package docstring in
[`terok.lib.integrations`][terok.lib.integrations] for the rationale.

Most symbols come from the wheel's top-level API.  A handful are not
exposed there yet and are pulled from submodules below — including a
few sibling-private names (``_handle_vault_seal``, ``_installed_versions``,
``_read_stamp``).  Centralising those reaches here means a sibling
release that tidies its own API only breaks this one file.

terok-shield doesn't have its own adapter: the only domain-side access
to shield is through sandbox's re-exports (``make_shield``, ``up``,
``down``, ``quarantine``, ``status``).  The CLI command bridge in
``terok.cli.commands.shield`` that wraps shield's own CLI registry is
the lone exception and imports ``terok_shield.*`` directly under a
narrow allowed-importer rule.
"""

from typing import Any

from terok_sandbox import (  # noqa: F401 — re-exported public API
    GATE_COMMANDS,
    SERVICES_TCP_OPTOUT_YAML,
    SSH_COMMANDS,
    BestEffortLogger,
    CommandDef,
    ConfigScope,
    ConfigStack,
    ContainerRuntime,
    CredentialDB,
    EnvironmentCheck,
    ExecResult,
    GateAuthNotConfigured,
    GateServerManager,
    GateServerStatus,
    GateStalenessInfo,
    GitGate,
    Image,
    LifecycleHooks,
    NullRuntime,
    PodmanRuntime,
    RawSSHSection,
    Sandbox,
    SandboxConfig,
    SelinuxStatus,
    ServicesMode,
    SetupVerdict,
    Sharing,
    SSHInitResult,
    SSHManager,
    VaultManager,
    VaultStatus,
    VaultUnreachableError,
    VolumeSpec,
    bold,
    check_environment,
    check_selinux_status,
    check_units_outdated,
    claim_port,
    create_token,
    down,
    ensure_server_reachable,
    ensure_vault_reachable,
    gate_use_personal_ssh_default,
    get_gate_base_path,
    get_gate_server_port,
    get_server_status,
    get_ssh_signer_port,
    get_token_broker_port,
    get_vault_status,
    is_ssh_url,
    is_systemd_available,
    is_vault_socket_active,
    is_vault_systemd_available,
    make_shield,
    namespace_runtime_dir,
    namespace_state_dir,
    needs_setup,
    public_line_of,
    quarantine,
    red,
    release_port,
    resolve_container_state_dir,
    revoke_token_for_task,
    run_setup,
    sandbox_uninstall,
    selinux_install_command,
    selinux_install_script,
    setup_hooks_direct,
    shield_interactive_session,
    shield_watch_session,
    stage_line,
    stamp_path,
    start_daemon,
    start_vault,
    state,
    status,
    stop_daemon,
    stop_vault,
    up,
    yellow,
)
from terok_sandbox.commands import (  # noqa: F401 — re-exported public API
    CommandTree,
    _handle_vault_seal,
    _handle_vault_to_keyring,
)
from terok_sandbox.doctor import (  # noqa: F401 — re-exported public API
    CheckVerdict,
    DoctorCheck,
    sandbox_doctor_checks,
)
from terok_sandbox.setup_stamp import (  # noqa: F401 — re-exported public API
    _installed_versions,
    _read_stamp,
)
from terok_sandbox.vault.store.db import (  # noqa: F401 — re-exported public API
    NoPassphraseError,
    WrongPassphraseError,
)


def __getattr__(name: str) -> Any:
    """Lazily resolve heavyweight re-exports kept off the import hot path.

    ``terok_sandbox.vault.daemon.token_broker`` drags the whole ``aiohttp``
    dependency tree in with it, and its only consumer (``terok vault
    serve``) imports it from inside a dispatch function anyway.  Routing
    it through ``__getattr__`` keeps ``import terok.lib.integrations.sandbox``
    — which sits on the CLI-startup hot path via ``terok.cli.main`` —
    from paying that cost.
    """
    if name == "vault_token_broker_main":
        from terok_sandbox.vault.daemon.token_broker import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BestEffortLogger",
    "CheckVerdict",
    "CommandDef",
    "CommandTree",
    "ConfigScope",
    "ConfigStack",
    "ContainerRuntime",
    "CredentialDB",
    "DoctorCheck",
    "EnvironmentCheck",
    "ExecResult",
    "GATE_COMMANDS",
    "GateAuthNotConfigured",
    "GateServerManager",
    "GateServerStatus",
    "GateStalenessInfo",
    "GitGate",
    "Image",
    "LifecycleHooks",
    "NoPassphraseError",
    "NullRuntime",
    "PodmanRuntime",
    "RawSSHSection",
    "SERVICES_TCP_OPTOUT_YAML",
    "SSH_COMMANDS",
    "SSHInitResult",
    "SSHManager",
    "Sandbox",
    "SandboxConfig",
    "SelinuxStatus",
    "ServicesMode",
    "SetupVerdict",
    "Sharing",
    "VaultManager",
    "VaultStatus",
    "VaultUnreachableError",
    "VolumeSpec",
    "WrongPassphraseError",
    "_handle_vault_seal",
    "_handle_vault_to_keyring",
    "_installed_versions",
    "_read_stamp",
    "bold",
    "check_environment",
    "check_selinux_status",
    "check_units_outdated",
    "claim_port",
    "create_token",
    "down",
    "ensure_server_reachable",
    "ensure_vault_reachable",
    "gate_use_personal_ssh_default",
    "get_gate_base_path",
    "get_gate_server_port",
    "get_server_status",
    "get_ssh_signer_port",
    "get_token_broker_port",
    "get_vault_status",
    "is_ssh_url",
    "is_systemd_available",
    "is_vault_socket_active",
    "is_vault_systemd_available",
    "make_shield",
    "namespace_runtime_dir",
    "namespace_state_dir",
    "needs_setup",
    "public_line_of",
    "quarantine",
    "red",
    "release_port",
    "resolve_container_state_dir",
    "revoke_token_for_task",
    "run_setup",
    "sandbox_doctor_checks",
    "sandbox_uninstall",
    "selinux_install_command",
    "selinux_install_script",
    "setup_hooks_direct",
    "shield_interactive_session",
    "shield_watch_session",
    "stage_line",
    "stamp_path",
    "start_daemon",
    "start_vault",
    "state",
    "status",
    "stop_daemon",
    "stop_vault",
    "up",
    "vault_token_broker_main",  # noqa: F822 — PEP 562 lazy re-export via __getattr__
    "yellow",
]
