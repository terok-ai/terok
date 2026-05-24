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

# ── Thin shims wrapping the post-W5.A class API ─────────
#
# These re-export the typed shims from the integrations layer so the
# api/* surface keeps the pre-W5.A function names callers reach for.
from terok.lib.integrations.executor import (  # noqa: F401 — re-exported public API  # noqa: F401 — shim re-exports
    AGENT_PROVIDERS,
    AUTH_PROVIDERS,
    COMMANDS as EXECUTOR_COMMANDS,
    DEFAULT_BASE_IMAGE,
    PROVIDER_NAMES,
    ACPEndpointStatus,
    AgentRunner,
    BuildError,
    ExecutorConfigView,
    ImageBuilder,
    acp_socket_is_live,
    build_base_images,
    build_sidecar_image,
    bundled_default_instructions,
    detect_family,
    ensure_default_l1,
    ensure_sandbox_ready,
    ensure_vault_routes,
    get_global_image_agents,
    get_global_image_base_image,
    get_provider,
    get_roster,
    image_agents,
    l0_image_tag,
    l1_image_tag,
    parse_agent_selection,
    parse_md_agent,
    prompt_agents_selection,
    render_l0,
    render_l1,
    resolve_instructions,
    set_global_image_agents,
    stage_scripts,
    stage_tmux_config,
    stage_toad_agents,
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
    "ExecutorConfigView",
    "ImageBuilder",
    "PROVIDER_NAMES",
    "acp_socket_is_live",
    "authenticate",
    "build_base_images",
    "build_images",
    "build_sidecar_image",
    "bundled_default_instructions",
    "detect_family",
    "ensure_default_l1",
    "ensure_sandbox_ready",
    "ensure_vault_routes",
    "generate_dockerfiles",
    "get_global_image_agents",
    "get_global_image_base_image",
    "get_provider",
    "get_roster",
    "image_agents",
    "installed_agents",
    "installed_agents_for_project",
    "l0_image_tag",
    "l1_image_tag",
    "parse_agent_selection",
    "parse_md_agent",
    "prompt_agents_selection",
    "render_l0",
    "render_l1",
    "resolve_agent_config",
    "resolve_instructions",
    "set_global_image_agents",
    "stage_scripts",
    "stage_tmux_config",
    "stage_toad_agents",
    "validate_agent_selection",
]
