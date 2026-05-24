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
from terok.lib.domain.auth import (  # noqa: F401 — re-exported public API
    authenticate,
    find_host_auth_image,
    resolve_credential_routing,
)
from terok.lib.integrations.executor import (  # noqa: F401 — re-exported public API
    AGENT_PROVIDERS,
    AUTH_PROVIDERS,
    COMMANDS as EXECUTOR_COMMANDS,
    DEFAULT_BASE_IMAGE,
    PROVIDER_NAMES,
    ACPEndpointStatus,
    AgentRoster,
    AgentRunner,
    Authenticator,
    AuthSession,
    BuildError,
    ExecutorConfigView,
    ImageBuilder,
    KrunHost,
    SharedMountStorageInfo,
    TaskStorageInfo,
    acp_socket_is_live,
    bundled_default_instructions,
    ensure_sandbox_ready,
    get_provider,
    parse_md_agent,
    prepare_oauth_session,
    resolve_instructions,
    store_api_key,
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
    "AgentRoster",
    "AgentRunner",
    "Authenticator",
    "AuthSession",
    "BuildError",
    "DEFAULT_BASE_IMAGE",
    "EXECUTOR_COMMANDS",
    "ExecutorConfigView",
    "ImageBuilder",
    "KrunHost",
    "PROVIDER_NAMES",
    "SharedMountStorageInfo",
    "TaskStorageInfo",
    "acp_socket_is_live",
    "authenticate",
    "build_images",
    "bundled_default_instructions",
    "ensure_sandbox_ready",
    "find_host_auth_image",
    "generate_dockerfiles",
    "get_provider",
    "installed_agents",
    "installed_agents_for_project",
    "parse_md_agent",
    "prepare_oauth_session",
    "resolve_agent_config",
    "resolve_credential_routing",
    "resolve_instructions",
    "store_api_key",
]
