# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Agent providers, ACP, image build, instructions — public API surface.

Re-export catalog for everything agent-shaped: the provider registry,
the runner abstraction, ACP-socket inspection, image build, and the
instructions / config bundlers.  Sources:
[`terok.lib.integrations.executor`][terok.lib.integrations.executor] for
the executor wheel's surface,
[`terok.lib.orchestration.image`][terok.lib.orchestration.image] for
terok's Dockerfile + image pipeline,
[`terok.lib.core.images`][terok.lib.core.images] for installed-agent
queries, [`terok.lib.domain.auth`][terok.lib.domain.auth] for the
``authenticate`` workflow, and
[`terok.lib.orchestration.agent_config`][terok.lib.orchestration.agent_config]
for stack resolution.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok.lib.core.images import (
        installed_agents as installed_agents,
        installed_agents_for_project as installed_agents_for_project,
    )
    from terok.lib.domain.auth import (
        auth_provider_aliases as auth_provider_aliases,
        authenticate as authenticate,
        authenticated_entries as authenticated_entries,
        available_auth_modes as available_auth_modes,
        find_host_auth_image as find_host_auth_image,
        resolve_auth_provider as resolve_auth_provider,
        resolve_credential_routing as resolve_credential_routing,
    )
    from terok.lib.integrations.executor import (
        AGENT_NAMES as AGENT_NAMES,
        AGENTS as AGENTS,
        AUTH_PROVIDERS as AUTH_PROVIDERS,
        COMMANDS as EXECUTOR_COMMANDS,
        DEFAULT_BASE_IMAGE as DEFAULT_BASE_IMAGE,
        ACPEndpointStatus as ACPEndpointStatus,
        AgentRoster as AgentRoster,
        AgentRunner as AgentRunner,
        Authenticator as Authenticator,
        AuthSession as AuthSession,
        BuildError as BuildError,
        ExecutorConfigView as ExecutorConfigView,
        ImageBuilder as ImageBuilder,
        KrunHost as KrunHost,
        SharedMountStorageInfo as SharedMountStorageInfo,
        TaskStorageInfo as TaskStorageInfo,
        acp_socket_is_live as acp_socket_is_live,
        bundled_default_instructions as bundled_default_instructions,
        ensure_sandbox_ready as ensure_sandbox_ready,
        get_agent as get_agent,
        prepare_oauth_session as prepare_oauth_session,
        resolve_instructions as resolve_instructions,
        store_api_key as store_api_key,
    )
    from terok.lib.orchestration.agent_config import (
        resolve_agent_config as resolve_agent_config,
    )
    from terok.lib.orchestration.image import (
        build_images as build_images,
        generate_dockerfiles as generate_dockerfiles,
    )

#: Public name -> defining module (PEP 562 lazy resolution).
_LAZY: dict[str, str] = {
    "ACPEndpointStatus": "terok.lib.integrations.executor",
    "AGENTS": "terok.lib.integrations.executor",
    "AGENT_NAMES": "terok.lib.integrations.executor",
    "AUTH_PROVIDERS": "terok.lib.integrations.executor",
    "AgentRoster": "terok.lib.integrations.executor",
    "AgentRunner": "terok.lib.integrations.executor",
    "AuthSession": "terok.lib.integrations.executor",
    "Authenticator": "terok.lib.integrations.executor",
    "BuildError": "terok.lib.integrations.executor",
    "DEFAULT_BASE_IMAGE": "terok.lib.integrations.executor",
    "EXECUTOR_COMMANDS": "terok.lib.integrations.executor:COMMANDS",
    "ExecutorConfigView": "terok.lib.integrations.executor",
    "ImageBuilder": "terok.lib.integrations.executor",
    "KrunHost": "terok.lib.integrations.executor",
    "SharedMountStorageInfo": "terok.lib.integrations.executor",
    "TaskStorageInfo": "terok.lib.integrations.executor",
    "acp_socket_is_live": "terok.lib.integrations.executor",
    "auth_provider_aliases": "terok.lib.domain.auth",
    "authenticate": "terok.lib.domain.auth",
    "authenticated_entries": "terok.lib.domain.auth",
    "available_auth_modes": "terok.lib.domain.auth",
    "build_images": "terok.lib.orchestration.image",
    "bundled_default_instructions": "terok.lib.integrations.executor",
    "ensure_sandbox_ready": "terok.lib.integrations.executor",
    "find_host_auth_image": "terok.lib.domain.auth",
    "generate_dockerfiles": "terok.lib.orchestration.image",
    "get_agent": "terok.lib.integrations.executor",
    "installed_agents": "terok.lib.core.images",
    "installed_agents_for_project": "terok.lib.core.images",
    "prepare_oauth_session": "terok.lib.integrations.executor",
    "resolve_agent_config": "terok.lib.orchestration.agent_config",
    "resolve_auth_provider": "terok.lib.domain.auth",
    "resolve_credential_routing": "terok.lib.domain.auth",
    "resolve_instructions": "terok.lib.integrations.executor",
    "store_api_key": "terok.lib.integrations.executor",
}

__all__ = [
    "ACPEndpointStatus",
    "AGENTS",
    "AGENT_NAMES",
    "AUTH_PROVIDERS",
    "AgentRoster",
    "AgentRunner",
    "BuildError",
    "DEFAULT_BASE_IMAGE",
    "EXECUTOR_COMMANDS",
    "ExecutorConfigView",
    "ImageBuilder",
    "acp_socket_is_live",
    "auth_provider_aliases",
    "available_auth_modes",
    "bundled_default_instructions",
    "ensure_sandbox_ready",
    "get_agent",
    "resolve_auth_provider",
    "resolve_instructions",
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
