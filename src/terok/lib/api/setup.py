# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""First-run setup, env check, sandbox-uninstall, sickbay primitives — public API surface.

Re-export catalog for the host-side bootstrap pieces (and the sickbay
diagnostic surface that lives on top of them).  Source:
[`terok.lib.integrations.sandbox`][terok.lib.integrations.sandbox] —
terok-sandbox owns the bootstrap logic; terok presents it.  The
``namespace_state_dir`` path resolver flows from
[`terok_util`][terok_util] — the foundation library — not through the
sandbox adapter.
"""

from terok_util import namespace_state_dir  # noqa: F401 — re-exported public API

from terok.lib.integrations.sandbox import (  # noqa: F401 — re-exported public API
    EXIT_MANUAL_STEP_NEEDED,
    SERVICES_TCP_OPTOUT_YAML,
    EnvironmentCheck,
    GateServerManager,
    SelinuxStatus,
    SetupVerdict,
    VaultManager,
    check_environment,
    check_selinux_status,
    is_ssh_url,
    needs_setup,
    public_line_of,
    resolve_container_state_dir,
    sandbox_uninstall,
    selinux_install_command,
    selinux_install_script,
    setup_hooks_direct,
    systemd_creds_has_tpm2,
    yaml_update_section,
)

__all__ = [
    "EXIT_MANUAL_STEP_NEEDED",
    "EnvironmentCheck",
    "GateServerManager",
    "SERVICES_TCP_OPTOUT_YAML",
    "SelinuxStatus",
    "SetupVerdict",
    "VaultManager",
    "check_environment",
    "check_selinux_status",
    "is_ssh_url",
    "namespace_state_dir",
    "needs_setup",
    "public_line_of",
    "resolve_container_state_dir",
    "sandbox_uninstall",
    "selinux_install_command",
    "selinux_install_script",
    "setup_hooks_direct",
    "systemd_creds_has_tpm2",
    "yaml_update_section",
]
