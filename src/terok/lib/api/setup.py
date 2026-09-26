# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""First-run setup, env check, sandbox-uninstall, sickbay primitives — public API surface.

[`terok.lib.core.setup`][terok.lib.core.setup] owns terok's receipt and
composes checks from its dependencies. Sandbox provisioning and diagnostic
primitives come through [`terok.lib.integrations.sandbox`][terok.lib.integrations.sandbox].
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok_util import (
        SetupStatus as SetupStatus,
        namespace_state_dir as namespace_state_dir,
        setup_status as setup_status,
    )

    from terok.lib.core.setup import (
        check_setup as check_setup,
        complete_setup as complete_setup,
        invalidate_setup as invalidate_setup,
        preflight_setup as preflight_setup,
        validate_host_setup as validate_host_setup,
    )
    from terok.lib.integrations.sandbox import (
        BUNDLE_VERSION as BUNDLE_VERSION,
        EXIT_MANUAL_STEP_NEEDED as EXIT_MANUAL_STEP_NEEDED,
        GIT_HTTP_BACKEND_HINT as GIT_HTTP_BACKEND_HINT,
        SERVICES_TCP_OPTOUT_YAML as SERVICES_TCP_OPTOUT_YAML,
        SETUP_COMPONENTS as SETUP_COMPONENTS,
        EnvironmentCheck as EnvironmentCheck,
        SelinuxStatus as SelinuxStatus,
        ShieldAnnotations as ShieldAnnotations,
        ShieldHooks as ShieldHooks,
        check_environment as check_environment,
        check_selinux_status as check_selinux_status,
        git_http_backend as git_http_backend,
        handle_setup_component as handle_setup_component,
        is_ssh_url as is_ssh_url,
        make_kernel_keyring_quota_check as make_kernel_keyring_quota_check,
        public_line_of as public_line_of,
        resolve_container_annotations as resolve_container_annotations,
        resolve_container_shield_version as resolve_container_shield_version,
        resolve_container_state_dir as resolve_container_state_dir,
        sandbox_uninstall as sandbox_uninstall,
        selinux_install_script as selinux_install_script,
        systemd_creds_has_tpm2 as systemd_creds_has_tpm2,
        yaml_update_section as yaml_update_section,
    )

#: Public name -> defining module (PEP 562 lazy resolution).
_LAZY: dict[str, str] = {
    "SetupStatus": "terok_util",
    "setup_status": "terok_util",
    "check_setup": "terok.lib.core.setup",
    "complete_setup": "terok.lib.core.setup",
    "invalidate_setup": "terok.lib.core.setup",
    "preflight_setup": "terok.lib.core.setup",
    "validate_host_setup": "terok.lib.core.setup",
    "EnvironmentCheck": "terok.lib.integrations.sandbox",
    "SERVICES_TCP_OPTOUT_YAML": "terok.lib.integrations.sandbox",
    "SelinuxStatus": "terok.lib.integrations.sandbox",
    "ShieldHooks": "terok.lib.integrations.sandbox",
    "check_environment": "terok.lib.integrations.sandbox",
    "make_kernel_keyring_quota_check": "terok.lib.integrations.sandbox",
    "GIT_HTTP_BACKEND_HINT": "terok.lib.integrations.sandbox",
    "EXIT_MANUAL_STEP_NEEDED": "terok.lib.integrations.sandbox",
    "SETUP_COMPONENTS": "terok.lib.integrations.sandbox",
    "check_selinux_status": "terok.lib.integrations.sandbox",
    "git_http_backend": "terok.lib.integrations.sandbox",
    "handle_setup_component": "terok.lib.integrations.sandbox",
    "is_ssh_url": "terok.lib.integrations.sandbox",
    "namespace_state_dir": "terok_util",
    "public_line_of": "terok.lib.integrations.sandbox",
    "BUNDLE_VERSION": "terok.lib.integrations.sandbox",
    "ShieldAnnotations": "terok.lib.integrations.sandbox",
    "resolve_container_annotations": "terok.lib.integrations.sandbox",
    "resolve_container_shield_version": "terok.lib.integrations.sandbox",
    "resolve_container_state_dir": "terok.lib.integrations.sandbox",
    "sandbox_uninstall": "terok.lib.integrations.sandbox",
    "selinux_install_script": "terok.lib.integrations.sandbox",
    "systemd_creds_has_tpm2": "terok.lib.integrations.sandbox",
    "yaml_update_section": "terok.lib.integrations.sandbox",
}

__all__ = [
    "SetupStatus",
    "setup_status",
    "check_setup",
    "complete_setup",
    "invalidate_setup",
    "preflight_setup",
    "validate_host_setup",
    "EnvironmentCheck",
    "SERVICES_TCP_OPTOUT_YAML",
    "SelinuxStatus",
    "ShieldHooks",
    "check_environment",
    "make_kernel_keyring_quota_check",
    "GIT_HTTP_BACKEND_HINT",
    "EXIT_MANUAL_STEP_NEEDED",
    "SETUP_COMPONENTS",
    "check_selinux_status",
    "git_http_backend",
    "handle_setup_component",
    "is_ssh_url",
    "public_line_of",
    "BUNDLE_VERSION",
    "ShieldAnnotations",
    "resolve_container_annotations",
    "resolve_container_shield_version",
    "resolve_container_state_dir",
    "sandbox_uninstall",
    "selinux_install_script",
    "systemd_creds_has_tpm2",
    "yaml_update_section",
]


def __getattr__(name: str) -> object:
    """Resolve a re-exported name to its source module on first access (PEP 562)."""
    try:
        target = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module_path, _, source_name = target.partition(":")
    value = getattr(importlib.import_module(module_path), source_name or name)
    globals()[name] = value  # cache so subsequent lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    """Expose the lazy names to ``dir()`` / autocompletion."""
    return sorted({*globals(), *_LAZY})
