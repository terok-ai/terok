# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Adapter for the ``terok_executor`` wheel.

Re-exports every symbol terok consumes from terok-executor.  Callers
elsewhere in terok import from this module rather than from
``terok_executor`` directly — see the package docstring in
[`terok.lib.integrations`][terok.lib.integrations] for the rationale.

Every symbol comes from the wheel's top-level public API.  The
W5.A class consolidation replaced the build/auth/global-config free
functions with ``ImageBuilder``, ``Authenticator``, and three
``ExecutorConfigView`` static methods respectively; the old loose
fns are gone.
"""

# ── Thin shims wrapping the post-W5.A class API ─────────
#
# Pre-W5.A free-function names kept as one-liner pass-throughs so
# tests and ad-hoc terok callers that target the integrations layer
# don't have to chase every executor API rename.
from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from terok_executor.integrations.sandbox import DoctorCheck, KrunRuntime, SandboxConfig

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
from terok_executor.container.build import ImageSet  # noqa: E402 — return type for shims


def authenticate(
    project_id: str | None,
    provider: str,
    *,
    mounts_dir: Path,
    image: str | Callable[[], str] | None = None,
    expose_token: bool = False,
    oauth_enabled: bool = True,
) -> None:
    """Shim around ``Authenticator(provider).run(...)`` (post-W5.A)."""
    Authenticator(provider).run(
        project_id,
        mounts_dir=mounts_dir,
        image=image,
        expose_token=expose_token,
        oauth_enabled=oauth_enabled,
    )


def build_base_images(
    base_image: str = DEFAULT_BASE_IMAGE,
    *,
    family: str | None = None,
    agents: str | tuple[str, ...] = "all",
    rebuild: bool = False,
    full_rebuild: bool = False,
    build_dir: Path | None = None,
    tag_as_default: bool = False,
) -> ImageSet:
    """Shim around ``ImageBuilder(base, family).build_base(...)``."""
    return ImageBuilder(base_image, family=family).build_base(
        agents=agents,
        rebuild=rebuild,
        full_rebuild=full_rebuild,
        build_dir=build_dir,
        tag_as_default=tag_as_default,
    )


def build_sidecar_image(
    base_image: str = DEFAULT_BASE_IMAGE,
    *,
    family: str | None = None,
    tool_name: str = "coderabbit",
    rebuild: bool = False,
    full_rebuild: bool = False,
    build_dir: Path | None = None,
) -> str:
    """Shim around ``ImageBuilder(base, family).build_sidecar(...)``."""
    return ImageBuilder(base_image, family=family).build_sidecar(
        tool_name=tool_name,
        rebuild=rebuild,
        full_rebuild=full_rebuild,
        build_dir=build_dir,
    )


def ensure_default_l1(
    base_image: str = DEFAULT_BASE_IMAGE,
    *,
    family: str | None = None,
    agents: str | tuple[str, ...] = "all",
) -> str:
    """Shim around ``ImageBuilder.ensure_default_l1``."""
    return ImageBuilder(base_image, family=family).ensure_default_l1(agents=agents)


def detect_family(base_image: str, override: str | None = None) -> str:
    """Shim around ``ImageBuilder.detect_family``."""
    return ImageBuilder.detect_family(base_image, override)


def l0_image_tag(base_image: str) -> str:
    """Shim around ``ImageBuilder(base).l0_tag``."""
    return ImageBuilder(base_image).l0_tag


def l1_image_tag(base_image: str, agents: tuple[str, ...] | None = None) -> str:
    """Shim around ``ImageBuilder(base).l1_tag(agents)``."""
    return ImageBuilder(base_image).l1_tag(agents)


def image_agents(image: str) -> set[str]:
    """Shim around ``ImageBuilder.image_agents``."""
    return ImageBuilder.image_agents(image)


def render_l0(base_image: str = DEFAULT_BASE_IMAGE, *, family: str | None = None) -> str:
    """Shim around ``ImageBuilder.render_l0``."""
    return ImageBuilder(base_image, family=family).render_l0()


def render_l1(
    l0_image: str,
    *,
    family: str,
    agents: str | tuple[str, ...] = "all",
    cache_bust: str = "0",
) -> str:
    """Shim around ``ImageBuilder.render_l1`` (now a staticmethod)."""
    return ImageBuilder.render_l1(l0_image, family=family, agents=agents, cache_bust=cache_bust)


def stage_scripts(dest: Path) -> None:
    """Shim around ``ImageBuilder.stage_scripts``."""
    ImageBuilder.stage_scripts(dest)


def stage_toad_agents(dest: Path) -> None:
    """Shim around ``ImageBuilder.stage_toad_agents``."""
    ImageBuilder.stage_toad_agents(dest)


def stage_tmux_config(dest: Path) -> None:
    """Shim around ``ImageBuilder.stage_tmux_config``."""
    ImageBuilder.stage_tmux_config(dest)


def get_global_image_agents() -> str | None:
    """Shim around ``ExecutorConfigView.image_agents``."""
    return ExecutorConfigView.image_agents()


def get_global_image_base_image() -> str | None:
    """Shim around ``ExecutorConfigView.image_base_image``."""
    return ExecutorConfigView.image_base_image()


def set_global_image_agents(selection: str) -> Path:
    """Shim around ``ExecutorConfigView.set_image_agents``."""
    return ExecutorConfigView.set_image_agents(selection)


# ── Shims wrapping the post-W5.A.2 roster/krun class API ─────


def get_roster() -> AgentRoster:
    """Shim around ``AgentRoster.shared`` (post-W5.A.2)."""
    return AgentRoster.shared()


def parse_agent_selection(raw: str) -> str | tuple[str, ...]:
    """Shim around ``AgentRoster.parse_selection`` (post-W5.A.2)."""
    return AgentRoster.parse_selection(raw)


def validate_agent_selection(raw: str) -> None:
    """Shim around ``AgentRoster.validate_selection`` (post-W5.A.2)."""
    AgentRoster.shared().validate_selection(raw)


def prompt_agents_selection() -> str:
    """Shim around ``AgentRoster.prompt_selection`` (post-W5.A.2)."""
    return AgentRoster.shared().prompt_selection()


def ensure_vault_routes(cfg: "SandboxConfig | None" = None) -> Path:
    """Shim around ``AgentRoster.ensure_vault_routes`` (post-W5.A.2)."""
    return AgentRoster.shared().ensure_vault_routes(cfg=cfg)


def agent_doctor_checks(
    roster: AgentRoster, *, token_broker_port: int | None = None
) -> list["DoctorCheck"]:
    """Shim around ``AgentRoster.doctor_checks`` (post-W5.A.2)."""
    return roster.doctor_checks(token_broker_port=token_broker_port)


def collect_all_auto_approve_env() -> dict[str, str]:
    """Shim around ``AgentRoster.collect_all_auto_approve_env`` (post-W5.A.2)."""
    return AgentRoster.shared().collect_all_auto_approve_env()


def get_tasks_storage(tasks_root: Path) -> list[TaskStorageInfo]:
    """Shim around ``TaskStorageInfo.measure_all`` (post-W5.A.2)."""
    return TaskStorageInfo.measure_all(tasks_root)


def get_shared_mounts_storage(mounts_base: Path | None = None) -> list[SharedMountStorageInfo]:
    """Shim around ``SharedMountStorageInfo.measure_all`` (post-W5.A.2)."""
    return SharedMountStorageInfo.measure_all(mounts_base)


def make_krun_runtime(*, cfg: "SandboxConfig | None" = None) -> "KrunRuntime":
    """Shim around ``KrunHost(cfg=cfg).runtime()`` (post-W5.A.2)."""
    return KrunHost(cfg=cfg).runtime()


def krun_launch_args(*, cfg: "SandboxConfig | None" = None) -> list[str]:
    """Shim around ``KrunHost(cfg=cfg).launch_args()`` (post-W5.A.2)."""
    return KrunHost(cfg=cfg).launch_args()


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
    "KrunHost",
    "PROVIDER_NAMES",
    "RawImageSection",
    "SharedMountStorageInfo",
    "TaskStorageInfo",
    "VAULT_COMMANDS",
    "acp_socket_is_live",
    "agent_doctor_checks",
    "assemble_container_env",
    "authenticate",
    "build_base_images",
    "build_project_image",
    "build_sidecar_image",
    "bundled_default_instructions",
    "collect_all_auto_approve_env",
    "detect_family",
    "ensure_default_l1",
    "ensure_sandbox_ready",
    "ensure_vault_routes",
    "get_global_image_agents",
    "get_global_image_base_image",
    "get_provider",
    "get_roster",
    "get_shared_mounts_storage",
    "get_tasks_storage",
    "image_agents",
    "inject_prompt",
    "l0_image_tag",
    "l1_image_tag",
    "list_authenticated_agents",
    "KrunHostKeypair",
    "ensure_krun_host_keypair",
    "krun_launch_args",
    "make_krun_runtime",
    "parse_agent_selection",
    "parse_md_agent",
    "prepare_agent_config_dir",
    "prompt_agents_selection",
    "render_l0",
    "render_l1",
    "resolve_instructions",
    "resolve_provider_value",
    "scan_leaked_credentials",
    "seed_workspace_from_clone_cache",
    "set_global_image_agents",
    "stage_scripts",
    "stage_tmux_config",
    "stage_toad_agents",
    "validate_agent_selection",
]
