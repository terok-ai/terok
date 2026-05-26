# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Public library API — the one stable import boundary for presentation layers.

The CLI and TUI import everything domain-, type-, or config-related from
this package rather than reaching into ``terok.lib.*`` internals — that
keeps the consumer surface narrow and lets internals refactor freely.

The package is split into focused sub-modules (catalogs of re-exports
from the appropriate adapter):

- [`vault`][terok.lib.api.vault] — vault status snapshot, DB context, sealing helpers
- [`gate`][terok.lib.api.gate] — gate-server lifecycle and status
- [`shield`][terok.lib.api.shield] — shield wrappers and the shield CLI registry
- [`agents`][terok.lib.api.agents] — providers, ACP, image build, instructions
- [`clearance`][terok.lib.api.clearance] — multi-socket subscriber, notifier, CLI registry
- [`setup`][terok.lib.api.setup] — first-run setup, env check, sickbay primitives, uninstall
- [`task`][terok.lib.api.task] — task lifecycle, runners, metadata, display tables
- [`project`][terok.lib.api.project] — project entities, lifecycle, panic, SSH

This module owns the cross-cutting bits: the
[`Config`][terok.lib.api.Config] snapshot, the runtime peek
[`get_container_state`][terok.lib.api.get_container_state], a small
number of shared sandbox types (``SandboxConfig`` and the CLI
[`CommandDef`][terok_util.cli_types.CommandDef] /
[`CommandTree`][terok_util.cli_types.CommandTree]), and the ANSI helpers
``bold``/``red``/``yellow``/``stage_line``.

For backward compatibility every sub-module's exports are also bound on
this package (so existing ``from terok.lib.api import foo`` lines keep
working) — see ``__all__`` at the bottom.

Pure utilities (``util.emoji``, ``util.yaml``, ``util.ansi``,
``util.net``, ``ui_utils.terminal``) and ``core.version`` stay
importable directly — they are genuinely cross-cutting and have no
domain coupling worth funnelling.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from terok_util import CommandDef, CommandTree  # noqa: F401 — re-exported public API

# ── Sub-module re-exports (back-compat for `from terok.lib.api import X`) ───
from terok.lib.api.agents import (  # noqa: F401 — re-exported public API
    AGENT_PROVIDERS,
    AUTH_PROVIDERS,
    DEFAULT_BASE_IMAGE,
    EXECUTOR_COMMANDS,
    PROVIDER_NAMES,
    ACPEndpointStatus,
    AgentRoster,
    AgentRunner,
    Authenticator,
    BuildError,
    ExecutorConfigView,
    ImageBuilder,
    KrunHost,
    SharedMountStorageInfo,
    TaskStorageInfo,
    acp_socket_is_live,
    authenticate,
    build_images,
    bundled_default_instructions,
    ensure_sandbox_ready,
    generate_dockerfiles,
    get_provider,
    installed_agents,
    installed_agents_for_project,
    parse_md_agent,
    resolve_agent_config,
    resolve_instructions,
)
from terok.lib.api.clearance import (  # noqa: F401 — re-exported public API
    ALL_NOTIFY_CATEGORIES,
    CLEARANCE_COMMANDS,
    NOTIFY_BLOCKED,
    NOTIFY_VERDICT,
    CallbackNotifier,
    EventSubscriber,
    MultiSocketSubscriber,
    Notification,
    create_notifier,
)
from terok.lib.api.gate import (  # noqa: F401 — re-exported public API
    GateAuthNotConfigured,
    GateServerManager,
    GateServerStatus,
    GateStalenessInfo,
    make_git_gate,
)
from terok.lib.api.project import (  # noqa: F401 — re-exported public API
    AGENTS_QUESTION,
    QUESTIONS,
    BrokenProject,
    DeleteProjectResult,
    Project,
    ProjectConfig,
    Question,
    cleanup_images,
    delete_project,
    derive_project,
    discover_projects,
    execute_panic,
    find_orphaned_images,
    find_projects_sharing_gate,
    format_panic_report,
    get_project,
    list_images,
    list_projects,
    load_project,
    panic_stop_containers,
    project_image_exists,
    remove_images,
    render_project_yaml,
    require_project_exists,
    set_project_image_agents,
    summarize_ssh_init,
    validate_answer,
    write_project_yaml,
)
from terok.lib.api.setup import (  # noqa: F401 — re-exported public API
    SERVICES_TCP_OPTOUT_YAML,
    EnvironmentCheck,
    SelinuxStatus,
    SetupVerdict,
    check_environment,
    check_selinux_status,
    is_ssh_url,
    namespace_state_dir,
    needs_setup,
    public_line_of,
    resolve_container_state_dir,
    sandbox_uninstall,
    selinux_install_command,
    selinux_install_script,
    systemd_creds_has_tpm2,
    yaml_update_section,
)
from terok.lib.api.shield import (  # noqa: F401 — re-exported public API
    SHIELD_COMMANDS,
    ArgDef,
    ExecError,
    RecoveryStatus,
    ShieldCommandDef,
    ShieldHooks,
    ShieldManager,
    installed_versions,
    read_stamp,
    stamp_path,
)
from terok.lib.api.task import (  # noqa: F401 — re-exported public API
    CONTAINER_MODES,
    GPU_DISPLAY,
    SECURITY_CLASS_DISPLAY,
    STATUS_DISPLAY,
    HeadlessRunRequest,
    LogViewOptions,
    ModeInfo,
    StatusInfo,
    Task,
    TaskDeleteResult,
    TaskMeta,
    agent_config_dir,
    container_name,
    effective_status,
    generate_task_name,
    get_all_task_states,
    get_login_command,
    get_task_meta,
    get_tasks,
    get_workspace_git_diff,
    has_gpu,
    mark_task_deleting,
    mode_info,
    sanitize_task_name,
    task_archive_list,
    task_archive_logs,
    task_delete,
    task_followup_headless,
    task_list,
    task_login,
    task_logs,
    task_new,
    task_rename,
    task_restart,
    task_run_cli,
    task_run_headless,
    task_run_toad,
    task_status,
    task_stop,
    validate_task_name,
    wait_for_container_exit,
)
from terok.lib.api.vault import (  # noqa: F401 — re-exported public API
    NoPassphraseError,
    VaultStatusSnapshot,
    WrongPassphraseError,
    handle_vault_seal,
    handle_vault_to_keyring,
    vault_db,
)
from terok.lib.core import config as _config, paths as _paths

# Side-effecting / factory helpers that don't fit on the frozen Config object.
from terok.lib.core.config import (  # noqa: F401 — re-exported public API
    make_sandbox_config,
    set_experimental,
)
from terok.lib.integrations.sandbox import (  # noqa: F401 — re-exported public API
    PodmanRuntime,
    SandboxConfig,
    bold,
    red,
    stage_line,
    yellow,
)

# ── Config snapshot ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Config:
    """Snapshot of the global config values consumers read at startup.

    Bundles the paths, feature flags, and presentation hints that the TUI
    (and CLI) previously pulled from a dozen scattered getters in
    [`terok.lib.core.config`][terok.lib.core.config] and
    [`terok.lib.core.paths`][terok.lib.core.paths].  Capture once with
    [`get_config`][terok.lib.api.get_config]; pass the result around.

    Side-effecting helpers — ``set_experimental``, ``make_sandbox_config`` —
    stay as functions on the [`api`][terok.lib.api] module; they don't
    belong on a frozen value object.
    """

    # Paths
    config_root: Path
    core_state_dir: Path
    runtime_dir: Path
    archive_dir: Path
    vault_dir: Path
    user_projects_dir: Path
    global_config_path: Path
    # Settings
    public_host: str
    shield_bypass_firewall_no_protection: bool
    tui_default_tmux: bool
    tui_external_editor: bool
    # Presentation hints
    shield_security_hint: str


def get_config() -> Config:
    """Snapshot the global config into a single [`Config`][terok.lib.api.Config] value."""
    return Config(
        config_root=_paths.config_root(),
        core_state_dir=_paths.core_state_dir(),
        runtime_dir=_paths.runtime_dir(),
        archive_dir=_config.archive_dir(),
        vault_dir=_config.vault_dir(),
        user_projects_dir=_config.user_projects_dir(),
        global_config_path=_config.global_config_path(),
        public_host=_config.get_public_host(),
        shield_bypass_firewall_no_protection=_config.get_shield_bypass_firewall_no_protection(),
        tui_default_tmux=_config.get_tui_default_tmux(),
        tui_external_editor=_config.get_tui_external_editor(),
        shield_security_hint=_config.SHIELD_SECURITY_HINT,
    )


# ── Runtime peek ───────────────────────────────────────────────────────


def get_container_state(cname: str) -> str | None:
    """Return the live podman state for a container, or ``None`` if not found.

    Thin wrapper for one-shot state lookups.  The probe goes through
    plain ``PodmanRuntime`` because container-state reads are
    runtime-agnostic (``podman inspect`` returns the same shape under
    every OCI runtime), and the caller may not have project context
    in scope to resolve the per-project runtime.
    """
    return PodmanRuntime().container(cname).state


__all__ = [
    # Snapshot
    "Config",
    "get_config",
    # Side-effecting helpers
    "make_sandbox_config",
    "set_experimental",
    "get_container_state",
    # Shared sandbox types and ANSI helpers kept on __init__
    "SandboxConfig",
    "CommandDef",
    "CommandTree",
    "bold",
    "red",
    "yellow",
    "stage_line",
    # ── Re-exports from sub-modules ────────────────────────────────
    # Agents
    "ACPEndpointStatus",
    "AGENT_PROVIDERS",
    "AUTH_PROVIDERS",
    "AgentRunner",
    "BuildError",
    "DEFAULT_BASE_IMAGE",
    "AgentRoster",
    "Authenticator",
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
    "generate_dockerfiles",
    "get_provider",
    "installed_agents",
    "installed_agents_for_project",
    "parse_md_agent",
    "resolve_agent_config",
    "resolve_instructions",
    # Clearance
    "ALL_NOTIFY_CATEGORIES",
    "CLEARANCE_COMMANDS",
    "NOTIFY_BLOCKED",
    "NOTIFY_VERDICT",
    "CallbackNotifier",
    "EventSubscriber",
    "MultiSocketSubscriber",
    "Notification",
    "create_notifier",
    # Gate
    "GateAuthNotConfigured",
    "GateServerManager",
    "GateServerStatus",
    "GateStalenessInfo",
    "make_git_gate",
    # Project
    "AGENTS_QUESTION",
    "BrokenProject",
    "DeleteProjectResult",
    "Project",
    "ProjectConfig",
    "QUESTIONS",
    "Question",
    "cleanup_images",
    "delete_project",
    "derive_project",
    "discover_projects",
    "execute_panic",
    "find_orphaned_images",
    "find_projects_sharing_gate",
    "format_panic_report",
    "get_project",
    "list_images",
    "list_projects",
    "load_project",
    "panic_stop_containers",
    "project_image_exists",
    "remove_images",
    "render_project_yaml",
    "require_project_exists",
    "set_project_image_agents",
    "summarize_ssh_init",
    "validate_answer",
    "write_project_yaml",
    # Setup
    "EnvironmentCheck",
    "SERVICES_TCP_OPTOUT_YAML",
    "SelinuxStatus",
    "SetupVerdict",
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
    "systemd_creds_has_tpm2",
    "yaml_update_section",
    # Shield
    "ArgDef",
    "ExecError",
    "RecoveryStatus",
    "SHIELD_COMMANDS",
    "ShieldCommandDef",
    "ShieldHooks",
    "ShieldManager",
    "installed_versions",
    "read_stamp",
    "stamp_path",
    # Task
    "CONTAINER_MODES",
    "GPU_DISPLAY",
    "HeadlessRunRequest",
    "LogViewOptions",
    "ModeInfo",
    "SECURITY_CLASS_DISPLAY",
    "STATUS_DISPLAY",
    "StatusInfo",
    "Task",
    "TaskDeleteResult",
    "TaskMeta",
    "agent_config_dir",
    "container_name",
    "effective_status",
    "generate_task_name",
    "get_all_task_states",
    "get_login_command",
    "get_task_meta",
    "get_tasks",
    "get_workspace_git_diff",
    "has_gpu",
    "mark_task_deleting",
    "mode_info",
    "sanitize_task_name",
    "task_archive_list",
    "task_archive_logs",
    "task_delete",
    "task_followup_headless",
    "task_list",
    "task_login",
    "task_logs",
    "task_new",
    "task_rename",
    "task_restart",
    "task_run_cli",
    "task_run_headless",
    "task_run_toad",
    "task_status",
    "task_stop",
    "validate_task_name",
    "wait_for_container_exit",
    # Vault
    "NoPassphraseError",
    "VaultStatusSnapshot",
    "WrongPassphraseError",
    "handle_vault_seal",
    "handle_vault_to_keyring",
    "vault_db",
]
