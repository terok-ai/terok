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

from terok.lib.core.images import (  # noqa: F401 — re-exported public API
    installed_agents,
    installed_agents_for_project,
)
from terok.lib.domain.auth import authenticate  # noqa: F401 — re-exported public API
from terok.lib.integrations.executor import (  # noqa: F401 — re-exported public API
    AGENT_PROVIDERS,
    AUTH_PROVIDERS,
    COMMANDS as EXECUTOR_COMMANDS,
    DEFAULT_BASE_IMAGE,
    PROVIDER_NAMES,
    ACPEndpointStatus,
    AgentRunner,
    BuildError,
    acp_socket_is_live,
    build_base_images,
    build_sidecar_image,
    bundled_default_instructions,
    ensure_sandbox_ready,
    ensure_vault_routes,
    get_global_image_agents,
    get_global_image_base_image,
    get_provider,
    get_roster,
    parse_agent_selection,
    parse_md_agent,
    prompt_agents_selection,
    resolve_instructions,
    set_global_image_agents,
    validate_agent_selection,
)
from terok.lib.orchestration.agent_config import (  # noqa: F401 — re-exported public API
    resolve_agent_config,
)
from terok.lib.orchestration.image import (  # noqa: F401 — re-exported public API
    build_images,
    generate_dockerfiles,
)

__all__ = [
    "ACPEndpointStatus",
    "AGENT_PROVIDERS",
    "AUTH_PROVIDERS",
    "AgentRunner",
    "BuildError",
    "DEFAULT_BASE_IMAGE",
    "EXECUTOR_COMMANDS",
    "PROVIDER_NAMES",
    "acp_socket_is_live",
    "authenticate",
    "build_base_images",
    "build_images",
    "build_sidecar_image",
    "bundled_default_instructions",
    "ensure_sandbox_ready",
    "ensure_vault_routes",
    "generate_dockerfiles",
    "get_global_image_agents",
    "get_global_image_base_image",
    "get_provider",
    "get_roster",
    "installed_agents",
    "installed_agents_for_project",
    "parse_agent_selection",
    "parse_md_agent",
    "prompt_agents_selection",
    "resolve_agent_config",
    "resolve_instructions",
    "set_global_image_agents",
    "validate_agent_selection",
]
