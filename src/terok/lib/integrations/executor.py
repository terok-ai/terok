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

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok_executor import (
        AGENT_NAMES as AGENT_NAMES,
        AGENTS as AGENTS,
        AGENTS_LABEL as AGENTS_LABEL,
        AUTH_PROVIDERS as AUTH_PROVIDERS,
        COMMANDS as COMMANDS,
        DEFAULT_BASE_IMAGE as DEFAULT_BASE_IMAGE,
        ACPEndpointStatus as ACPEndpointStatus,
        Agent as Agent,
        AgentConfigSpec as AgentConfigSpec,
        AgentRoster as AgentRoster,
        AgentRunner as AgentRunner,
        Authenticator as Authenticator,
        AuthSession as AuthSession,
        BuildError as BuildError,
        CLIOverrides as CLIOverrides,
        ContainerEnvSpec as ContainerEnvSpec,
        EgressProjection as EgressProjection,
        ExecutorConfigView as ExecutorConfigView,
        ImageBuilder as ImageBuilder,
        KrunHost as KrunHost,
        RawImageSection as RawImageSection,
        SharedMountStorageInfo as SharedMountStorageInfo,
        TaskStorageInfo as TaskStorageInfo,
        acp_socket_is_live as acp_socket_is_live,
        assemble_container_env as assemble_container_env,
        build_project_image as build_project_image,
        bundled_default_instructions as bundled_default_instructions,
        credential_provider as credential_provider,
        ensure_sandbox_ready as ensure_sandbox_ready,
        get_agent as get_agent,
        inject_prompt as inject_prompt,
        known_family as known_family,
        list_authenticated_agents as list_authenticated_agents,
        prepare_agent_config_dir as prepare_agent_config_dir,
        prepare_oauth_session as prepare_oauth_session,
        resolve_agent_value as resolve_agent_value,
        resolve_instructions as resolve_instructions,
        scan_leaked_credentials as scan_leaked_credentials,
        seed_workspace_from_clone_cache as seed_workspace_from_clone_cache,
        store_api_key as store_api_key,
    )

#: Public name -> defining module (PEP 562 lazy resolution).
_LAZY: dict[str, str] = {
    "ACPEndpointStatus": "terok_executor",
    "AGENTS": "terok_executor",
    "AGENTS_LABEL": "terok_executor",
    "AGENT_NAMES": "terok_executor",
    "AUTH_PROVIDERS": "terok_executor",
    "Agent": "terok_executor",
    "AgentConfigSpec": "terok_executor",
    "AgentRoster": "terok_executor",
    "AgentRunner": "terok_executor",
    "AuthSession": "terok_executor",
    "Authenticator": "terok_executor",
    "BuildError": "terok_executor",
    "CLIOverrides": "terok_executor",
    "COMMANDS": "terok_executor",
    "ContainerEnvSpec": "terok_executor",
    "DEFAULT_BASE_IMAGE": "terok_executor",
    "EgressProjection": "terok_executor",
    "ExecutorConfigView": "terok_executor",
    "ImageBuilder": "terok_executor",
    "KrunHost": "terok_executor",
    "RawImageSection": "terok_executor",
    "SharedMountStorageInfo": "terok_executor",
    "TaskStorageInfo": "terok_executor",
    "acp_socket_is_live": "terok_executor",
    "assemble_container_env": "terok_executor",
    "build_project_image": "terok_executor",
    "bundled_default_instructions": "terok_executor",
    "credential_provider": "terok_executor",
    "ensure_sandbox_ready": "terok_executor",
    "get_agent": "terok_executor",
    "inject_prompt": "terok_executor",
    "known_family": "terok_executor",
    "list_authenticated_agents": "terok_executor",
    "prepare_agent_config_dir": "terok_executor",
    "prepare_oauth_session": "terok_executor",
    "resolve_agent_value": "terok_executor",
    "resolve_instructions": "terok_executor",
    "scan_leaked_credentials": "terok_executor",
    "seed_workspace_from_clone_cache": "terok_executor",
    "store_api_key": "terok_executor",
}

__all__ = [
    "ACPEndpointStatus",
    "AGENTS",
    "AGENTS_LABEL",
    "AGENT_NAMES",
    "AUTH_PROVIDERS",
    "Agent",
    "AgentConfigSpec",
    "AgentRoster",
    "AgentRunner",
    "AuthSession",
    "Authenticator",
    "BuildError",
    "CLIOverrides",
    "COMMANDS",
    "ContainerEnvSpec",
    "DEFAULT_BASE_IMAGE",
    "EgressProjection",
    "ExecutorConfigView",
    "ImageBuilder",
    "KrunHost",
    "RawImageSection",
    "SharedMountStorageInfo",
    "TaskStorageInfo",
    "acp_socket_is_live",
    "assemble_container_env",
    "build_project_image",
    "bundled_default_instructions",
    "credential_provider",
    "ensure_sandbox_ready",
    "get_agent",
    "inject_prompt",
    "known_family",
    "list_authenticated_agents",
    "prepare_agent_config_dir",
    "prepare_oauth_session",
    "resolve_agent_value",
    "resolve_instructions",
    "scan_leaked_credentials",
    "seed_workspace_from_clone_cache",
    "store_api_key",
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
