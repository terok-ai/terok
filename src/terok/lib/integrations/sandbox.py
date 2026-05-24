# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Adapter for the ``terok_sandbox`` wheel (and its re-exports of shield).

Re-exports every symbol terok consumes from terok-sandbox.  Callers
elsewhere in terok import from this module rather than from
``terok_sandbox`` directly — see the package docstring in
[`terok.lib.integrations`][terok.lib.integrations] for the rationale.

Every symbol comes from the wheel's top-level public API.  Shield's
high-level surface is now the
[`ShieldManager`][terok_sandbox.ShieldManager] /
[`ShieldHooks`][terok_sandbox.ShieldHooks] class pair (W5.B);
shield's command registry has its own adapter,
``terok.lib.integrations.shield``.

Foundation-layer symbols (``ConfigStack`` / ``ConfigScope`` /
``deep_merge``, ``namespace_state_dir`` / ``namespace_runtime_dir``)
do **not** flow through here — the adapter is the conduit for sibling
**wheels**, while [`terok_util`][terok_util] is a foundation library
imported directly by every layer that needs it.
"""

# ── Thin shims wrapping the post-W5.B class API ─────────
#
# Pre-W5.B free-function names kept as one-liner pass-throughs so
# tests and ad-hoc terok callers don't have to chase every sandbox
# rename through this integration layer.
from pathlib import Path  # noqa: E402 — placed near shim that uses it
from typing import Any  # noqa: E402

from terok_sandbox import (  # noqa: F401 — re-exported public API
    DEFAULT_GUEST_SSHD_PORT,
    DEFAULT_SSH_HOST,
    SERVICES_TCP_OPTOUT_YAML,
    BestEffortLogger,
    CheckVerdict,
    ContainerRuntime,
    CredentialDB,
    DoctorCheck,
    EnvironmentCheck,
    ExecResult,
    GateAuthNotConfigured,
    GateServerManager,
    GateServerStatus,
    GateStalenessInfo,
    GitGate,
    Image,
    KrunRuntime,
    LifecycleHooks,
    NoPassphraseError,
    NullRuntime,
    PodmanRuntime,
    RawRunSection,
    RawSSHSection,
    RecoveryStatus,
    Sandbox,
    SandboxConfig,
    SelinuxStatus,
    ServicesMode,
    SetupVerdict,
    Sharing,
    ShieldHooks,
    ShieldManager,
    SSHInitResult,
    SSHManager,
    TcpSSHTransport,
    TokenStore,
    VaultManager,
    VaultStatus,
    VaultUnreachableError,
    VolumeSpec,
    WrongPassphraseError,
    bold,
    check_environment,
    check_selinux_status,
    claim_port,
    ensure_infra_keypair,
    gate_use_personal_ssh_default,
    handle_vault_seal,
    handle_vault_to_keyring,
    installed_versions,
    is_ssh_url,
    needs_setup,
    podman_port_resolver,
    public_line_of,
    read_stamp,
    red,
    release_port,
    resolve_container_state_dir,
    sandbox_doctor_checks,
    sandbox_uninstall,
    selinux_install_command,
    selinux_install_script,
    stage_line,
    stamp_path,
    systemd_creds_has_tpm2,
    yaml_update_section,
    yellow,
)

# Shim return types use ``Any`` rather than the concrete ``Shield`` /
# ``ShieldState`` so this adapter doesn't import terok_shield — the
# importlinter contract limits terok_shield reach to integrations.shield.
# Callers should use the new class API (ShieldManager / ShieldHooks) where
# they want precise types.


def make_shield(task_dir: Path, cfg: SandboxConfig | None = None) -> Any:
    """Shim around ``ShieldManager(...).shield`` (post-W5.B)."""
    return ShieldManager(task_dir, cfg).shield


def up(cname: str, task_dir: Path, cfg: SandboxConfig | None = None) -> None:
    """Shim around ``ShieldManager.up`` (post-W5.B)."""
    ShieldManager(task_dir, cfg).up(cname)


def down(
    cname: str, task_dir: Path, cfg: SandboxConfig | None = None, *, allow_all: bool = False
) -> None:
    """Shim around ``ShieldManager.down``."""
    ShieldManager(task_dir, cfg).down(cname, allow_all=allow_all)


def quarantine(cname: str, task_dir: Path, cfg: SandboxConfig | None = None) -> None:
    """Shim around ``ShieldManager.quarantine``."""
    ShieldManager(task_dir, cfg).quarantine(cname)


def state(cname: str, task_dir: Path, cfg: SandboxConfig | None = None) -> Any:
    """Shim around ``ShieldManager.state``."""
    return ShieldManager(task_dir, cfg).state(cname)


def status(cfg: SandboxConfig | None = None) -> dict[str, Any]:
    """Shim around ``ShieldManager.status`` (config-only, no instance state needed)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        return ShieldManager(Path(tmp), cfg).status()


def run_setup(*, root: bool = False, user: bool = False) -> None:
    """Shim around ``ShieldHooks.install`` (post-W5.B)."""
    ShieldHooks.install(root=root, user=user)


def setup_hooks_direct(*, root: bool = False) -> None:
    """Shim around ``ShieldHooks.install`` selecting one scope."""
    ShieldHooks.install(root=root, user=not root)


__all__ = [
    "BestEffortLogger",
    "CheckVerdict",
    "ContainerRuntime",
    "CredentialDB",
    "DEFAULT_GUEST_SSHD_PORT",
    "DEFAULT_SSH_HOST",
    "DoctorCheck",
    "EnvironmentCheck",
    "ExecResult",
    "GateAuthNotConfigured",
    "GateServerManager",
    "GateServerStatus",
    "GateStalenessInfo",
    "GitGate",
    "Image",
    "KrunRuntime",
    "LifecycleHooks",
    "NoPassphraseError",
    "NullRuntime",
    "PodmanRuntime",
    "RawRunSection",
    "RawSSHSection",
    "SERVICES_TCP_OPTOUT_YAML",
    "SSHInitResult",
    "SSHManager",
    "Sandbox",
    "SandboxConfig",
    "SelinuxStatus",
    "ServicesMode",
    "SetupVerdict",
    "Sharing",
    "ShieldHooks",
    "ShieldManager",
    "TcpSSHTransport",
    "TokenStore",
    "VaultManager",
    "VaultStatus",
    "VaultUnreachableError",
    "VolumeSpec",
    "WrongPassphraseError",
    "RecoveryStatus",
    "bold",
    "check_environment",
    "check_selinux_status",
    "claim_port",
    "down",
    "ensure_infra_keypair",
    "gate_use_personal_ssh_default",
    "handle_vault_seal",
    "handle_vault_to_keyring",
    "installed_versions",
    "is_ssh_url",
    "make_shield",
    "needs_setup",
    "podman_port_resolver",
    "public_line_of",
    "quarantine",
    "read_stamp",
    "red",
    "release_port",
    "resolve_container_state_dir",
    "run_setup",
    "sandbox_doctor_checks",
    "sandbox_uninstall",
    "selinux_install_command",
    "selinux_install_script",
    "setup_hooks_direct",
    "stage_line",
    "stamp_path",
    "state",
    "status",
    "systemd_creds_has_tpm2",
    "up",
    "yaml_update_section",
    "yellow",
]
