# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Adapter for the ``terok_executor`` wheel.

Re-exports every symbol terok consumes from terok-executor.  Callers
elsewhere in terok import from this module rather than from
``terok_executor`` directly — see the package docstring in
[`terok.lib.integrations`][terok.lib.integrations] for the rationale.

Every symbol comes from the wheel's top-level public API.  Pure re-export
boundary — no shim functions, no kwarg massaging.  Use the class APIs
directly: [`ImageBuilder`][terok_executor.ImageBuilder],
[`AgentRoster`][terok_executor.AgentRoster],
[`KrunHost`][terok_executor.KrunHost],
[`Authenticator`][terok_executor.Authenticator],
[`ExecutorConfigView`][terok_executor.ExecutorConfigView],
[`TaskStorageInfo`][terok_executor.TaskStorageInfo],
[`SharedMountStorageInfo`][terok_executor.SharedMountStorageInfo].
"""

from terok_executor import (  # noqa: F401 — re-exported public API
    AGENT_COMMANDS,
    AGENT_PROVIDERS,
    AGENTS_LABEL,
    AUTH_PROVIDERS,
    COMMANDS,
    DEFAULT_BASE_IMAGE,
    PROVIDER_NAMES,
    VAULT_COMMANDS,
    ACPEndpointStatus,
    AgentConfigSpec,
    AgentProvider,
    AgentRoster,
    AgentRunner,
    Authenticator,
    BuildError,
    CLIOverrides,
    ContainerEnvSpec,
    ExecutorConfigView,
    ImageBuilder,
    KrunHost,
    KrunHostKeypair,
    RawImageSection,
    SharedMountStorageInfo,
    TaskStorageInfo,
    acp_socket_is_live,
    assemble_container_env,
    build_project_image,
    bundled_default_instructions,
    ensure_krun_host_keypair,
    ensure_sandbox_ready,
    get_provider,
    inject_prompt,
    list_authenticated_agents,
    parse_md_agent,
    prepare_agent_config_dir,
    resolve_instructions,
    resolve_provider_value,
    scan_leaked_credentials,
    seed_workspace_from_clone_cache,
)
from terok_executor.container.build import ImageSet  # noqa: F401 — return type for callers

__all__ = [
    "ACPEndpointStatus",
    "AGENT_COMMANDS",
    "AGENT_PROVIDERS",
    "AGENTS_LABEL",
    "AUTH_PROVIDERS",
    "COMMANDS",
    "AgentConfigSpec",
    "AgentProvider",
    "AgentRoster",
    "AgentRunner",
    "Authenticator",
    "BuildError",
    "CLIOverrides",
    "ContainerEnvSpec",
    "DEFAULT_BASE_IMAGE",
    "ExecutorConfigView",
    "ImageBuilder",
    "ImageSet",
    "KrunHost",
    "KrunHostKeypair",
    "PROVIDER_NAMES",
    "RawImageSection",
    "SharedMountStorageInfo",
    "TaskStorageInfo",
    "VAULT_COMMANDS",
    "acp_socket_is_live",
    "assemble_container_env",
    "build_project_image",
    "bundled_default_instructions",
    "ensure_krun_host_keypair",
    "ensure_sandbox_ready",
    "get_provider",
    "inject_prompt",
    "list_authenticated_agents",
    "parse_md_agent",
    "prepare_agent_config_dir",
    "resolve_instructions",
    "resolve_provider_value",
    "scan_leaked_credentials",
    "seed_workspace_from_clone_cache",
]
