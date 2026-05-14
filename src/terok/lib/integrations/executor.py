# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Adapter for the ``terok_executor`` wheel.

Re-exports every symbol terok consumes from terok-executor.  Callers
elsewhere in terok import from this module rather than from
``terok_executor`` directly — see the package docstring in
[`terok.lib.integrations`][terok.lib.integrations] for the rationale.
"""

from terok_executor import (  # noqa: F401 — re-exported public API
    AGENTS_LABEL,
    AUTH_PROVIDERS,
    PROVIDER_NAMES,
    ACPEndpointStatus,
    AgentConfigSpec,
    AgentRunner,
    BuildError,
    ExecutorConfigView,
    RawImageSection,
    SharedMountStorageInfo,
    TaskStorageInfo,
    acp_socket_is_live,
    agent_doctor_checks,
    authenticate,
    build_base_images,
    build_project_image,
    detect_family,
    get_provider,
    get_roster,
    get_shared_mounts_storage,
    get_tasks_storage,
    l0_image_tag,
    list_authenticated_agents,
    parse_agent_selection,
    parse_md_agent,
    prepare_agent_config_dir,
    resolve_instructions,
    resolve_provider_value,
    stage_scripts,
    stage_tmux_config,
    stage_toad_agents,
)

__all__ = [
    "ACPEndpointStatus",
    "AGENTS_LABEL",
    "AUTH_PROVIDERS",
    "AgentConfigSpec",
    "AgentRunner",
    "BuildError",
    "ExecutorConfigView",
    "PROVIDER_NAMES",
    "RawImageSection",
    "SharedMountStorageInfo",
    "TaskStorageInfo",
    "acp_socket_is_live",
    "agent_doctor_checks",
    "authenticate",
    "build_base_images",
    "build_project_image",
    "detect_family",
    "get_provider",
    "get_roster",
    "get_shared_mounts_storage",
    "get_tasks_storage",
    "l0_image_tag",
    "list_authenticated_agents",
    "parse_agent_selection",
    "parse_md_agent",
    "prepare_agent_config_dir",
    "resolve_instructions",
    "resolve_provider_value",
    "stage_scripts",
    "stage_tmux_config",
    "stage_toad_agents",
]
