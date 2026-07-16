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

Every re-exported name is served lazily (PEP 562 ``__getattr__``): the
sub-module that owns a symbol is imported only when that symbol is first
accessed, so a bare ``import terok.lib.api`` pulls in neither the
executor/ACP stack nor ``terok_sandbox`` until a consumer reaches for a
name that needs them.  ``_LAZY`` maps every re-exported name to its
source sub-module and is the single source of truth for resolution;
``__all__`` (below) advertises only the names consumed through this flat
front door — sub-module-local names live on their sub-module.

Pure utilities (``util.emoji``, ``util.yaml``, ``util.ansi``,
``util.net``, ``ui_utils.terminal``) and ``core.version`` stay
importable directly — they are genuinely cross-cutting and have no
domain coupling worth funnelling.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terok_util import (
        CommandDef as CommandDef,
        CommandTree as CommandTree,
    )

    from terok.lib.api.agents import (
        AGENT_NAMES as AGENT_NAMES,
        AGENTS as AGENTS,
        AUTH_PROVIDERS as AUTH_PROVIDERS,
        DEFAULT_BASE_IMAGE as DEFAULT_BASE_IMAGE,
        EXECUTOR_COMMANDS as EXECUTOR_COMMANDS,
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
        authenticate as authenticate,
        available_auth_modes as available_auth_modes,
        build_images as build_images,
        bundled_default_instructions as bundled_default_instructions,
        ensure_sandbox_ready as ensure_sandbox_ready,
        find_host_auth_image as find_host_auth_image,
        generate_dockerfiles as generate_dockerfiles,
        get_agent as get_agent,
        installed_agents as installed_agents,
        installed_agents_for_project as installed_agents_for_project,
        prepare_oauth_session as prepare_oauth_session,
        resolve_agent_config as resolve_agent_config,
        resolve_credential_routing as resolve_credential_routing,
        resolve_instructions as resolve_instructions,
        store_api_key as store_api_key,
    )
    from terok.lib.api.clearance import (
        ALL_NOTIFY_CATEGORIES as ALL_NOTIFY_CATEGORIES,
        CLEARANCE_COMMANDS as CLEARANCE_COMMANDS,
        NOTIFY_BLOCKED as NOTIFY_BLOCKED,
        NOTIFY_VERDICT as NOTIFY_VERDICT,
        CallbackNotifier as CallbackNotifier,
        EventSubscriber as EventSubscriber,
        MultiSocketSubscriber as MultiSocketSubscriber,
        Notification as Notification,
        create_notifier as create_notifier,
    )
    from terok.lib.api.gate import (
        GateAuthNotConfigured as GateAuthNotConfigured,
        GateStalenessInfo as GateStalenessInfo,
        make_git_gate as make_git_gate,
    )
    from terok.lib.api.project import (
        AGENTS_QUESTION as AGENTS_QUESTION,
        QUESTIONS as QUESTIONS,
        BrokenProject as BrokenProject,
        DeleteProjectResult as DeleteProjectResult,
        Project as Project,
        ProjectConfig as ProjectConfig,
        Question as Question,
        auth_image_staleness_warning as auth_image_staleness_warning,
        cleanup_images as cleanup_images,
        delete_project as delete_project,
        derive_project as derive_project,
        discover_projects as discover_projects,
        execute_panic as execute_panic,
        find_orphaned_images as find_orphaned_images,
        find_projects_sharing_gate as find_projects_sharing_gate,
        format_panic_report as format_panic_report,
        get_project as get_project,
        list_images as list_images,
        list_projects as list_projects,
        load_project as load_project,
        panic_stop_containers as panic_stop_containers,
        project_image_exists as project_image_exists,
        remove_images as remove_images,
        render_project_yaml as render_project_yaml,
        require_project_exists as require_project_exists,
        set_project_image_agents as set_project_image_agents,
        summarize_ssh_init as summarize_ssh_init,
        validate_answer as validate_answer,
        write_project_yaml as write_project_yaml,
    )
    from terok.lib.api.setup import (
        SERVICES_TCP_OPTOUT_YAML as SERVICES_TCP_OPTOUT_YAML,
        EnvironmentCheck as EnvironmentCheck,
        SelinuxStatus as SelinuxStatus,
        SetupVerdict as SetupVerdict,
        check_environment as check_environment,
        check_selinux_status as check_selinux_status,
        is_ssh_url as is_ssh_url,
        namespace_state_dir as namespace_state_dir,
        needs_setup as needs_setup,
        public_line_of as public_line_of,
        resolve_container_state_dir as resolve_container_state_dir,
        sandbox_uninstall as sandbox_uninstall,
        selinux_install_command as selinux_install_command,
        selinux_install_script as selinux_install_script,
        systemd_creds_has_tpm2 as systemd_creds_has_tpm2,
        yaml_update_section as yaml_update_section,
    )
    from terok.lib.api.shield import (
        SHIELD_COMMANDS as SHIELD_COMMANDS,
        ArgDef as ArgDef,
        ExecError as ExecError,
        RecoveryStatus as RecoveryStatus,
        ShieldCommandDef as ShieldCommandDef,
        ShieldHooks as ShieldHooks,
        ShieldManager as ShieldManager,
        installed_versions as installed_versions,
        read_stamp as read_stamp,
        stamp_path as stamp_path,
    )
    from terok.lib.api.task import (
        CONTAINER_MODES as CONTAINER_MODES,
        GPU_DISPLAY as GPU_DISPLAY,
        SECURITY_CLASS_DISPLAY as SECURITY_CLASS_DISPLAY,
        STATUS_DISPLAY as STATUS_DISPLAY,
        ContainerEventStream as ContainerEventStream,
        HeadlessRunRequest as HeadlessRunRequest,
        LogViewOptions as LogViewOptions,
        ModeInfo as ModeInfo,
        StatusInfo as StatusInfo,
        Task as Task,
        TaskDeleteResult as TaskDeleteResult,
        TaskMeta as TaskMeta,
        agent_config_dir as agent_config_dir,
        container_event_stream as container_event_stream,
        container_name as container_name,
        effective_status as effective_status,
        ensure_task_running as ensure_task_running,
        generate_task_name as generate_task_name,
        get_all_task_states as get_all_task_states,
        get_login_command as get_login_command,
        get_task_meta as get_task_meta,
        get_tasks as get_tasks,
        get_workspace_git_diff as get_workspace_git_diff,
        has_gpu as has_gpu,
        mark_task_deleting as mark_task_deleting,
        mode_info as mode_info,
        sanitize_task_name as sanitize_task_name,
        task_archive_list as task_archive_list,
        task_archive_logs as task_archive_logs,
        task_delete as task_delete,
        task_followup_headless as task_followup_headless,
        task_list as task_list,
        task_login as task_login,
        task_logs as task_logs,
        task_new as task_new,
        task_rename as task_rename,
        task_restart as task_restart,
        task_run_cli as task_run_cli,
        task_run_headless as task_run_headless,
        task_run_toad as task_run_toad,
        task_status as task_status,
        task_stop as task_stop,
        tasks_meta_dir as tasks_meta_dir,
        validate_task_name as validate_task_name,
        wait_for_container_exit as wait_for_container_exit,
    )
    from terok.lib.api.vault import (
        NoPassphraseError as NoPassphraseError,
        VaultState as VaultState,
        VaultStatus as VaultStatus,
        WrongPassphraseError as WrongPassphraseError,
        handle_vault_seal as handle_vault_seal,
        handle_vault_to_keyring as handle_vault_to_keyring,
        load_vault_status as load_vault_status,
        vault_db as vault_db,
    )
    from terok.lib.core.config import (
        make_sandbox_config as make_sandbox_config,
        save_tui_theme as save_tui_theme,
        set_experimental as set_experimental,
    )
    from terok.lib.integrations.sandbox import (
        PodmanRuntime as PodmanRuntime,
        SandboxConfig as SandboxConfig,
        bold as bold,
        red as red,
        stage_line as stage_line,
        yellow as yellow,
    )

#: Public name -> defining sub-module (PEP 562 lazy resolution).
_LAZY: dict[str, str] = {
    "ACPEndpointStatus": "terok.lib.api.agents",
    "AGENTS": "terok.lib.api.agents",
    "AGENTS_QUESTION": "terok.lib.api.project",
    "AGENT_NAMES": "terok.lib.api.agents",
    "ALL_NOTIFY_CATEGORIES": "terok.lib.api.clearance",
    "AUTH_PROVIDERS": "terok.lib.api.agents",
    "AgentRoster": "terok.lib.api.agents",
    "AgentRunner": "terok.lib.api.agents",
    "ArgDef": "terok.lib.api.shield",
    "AuthSession": "terok.lib.api.agents",
    "Authenticator": "terok.lib.api.agents",
    "BrokenProject": "terok.lib.api.project",
    "BuildError": "terok.lib.api.agents",
    "CLEARANCE_COMMANDS": "terok.lib.api.clearance",
    "CONTAINER_MODES": "terok.lib.api.task",
    "CallbackNotifier": "terok.lib.api.clearance",
    "CommandDef": "terok_util",
    "CommandTree": "terok_util",
    "ContainerEventStream": "terok.lib.api.task",
    "DEFAULT_BASE_IMAGE": "terok.lib.api.agents",
    "DeleteProjectResult": "terok.lib.api.project",
    "EXECUTOR_COMMANDS": "terok.lib.api.agents",
    "EnvironmentCheck": "terok.lib.api.setup",
    "EventSubscriber": "terok.lib.api.clearance",
    "ExecError": "terok.lib.api.shield",
    "ExecutorConfigView": "terok.lib.api.agents",
    "GPU_DISPLAY": "terok.lib.api.task",
    "GateAuthNotConfigured": "terok.lib.api.gate",
    "GateStalenessInfo": "terok.lib.api.gate",
    "HeadlessRunRequest": "terok.lib.api.task",
    "ImageBuilder": "terok.lib.api.agents",
    "KrunHost": "terok.lib.api.agents",
    "LogViewOptions": "terok.lib.api.task",
    "ModeInfo": "terok.lib.api.task",
    "MultiSocketSubscriber": "terok.lib.api.clearance",
    "NOTIFY_BLOCKED": "terok.lib.api.clearance",
    "NOTIFY_VERDICT": "terok.lib.api.clearance",
    "NoPassphraseError": "terok.lib.api.vault",
    "Notification": "terok.lib.api.clearance",
    "Project": "terok.lib.api.project",
    "ProjectConfig": "terok.lib.api.project",
    "QUESTIONS": "terok.lib.api.project",
    "Question": "terok.lib.api.project",
    "RecoveryStatus": "terok.lib.api.shield",
    "SECURITY_CLASS_DISPLAY": "terok.lib.api.task",
    "SERVICES_TCP_OPTOUT_YAML": "terok.lib.api.setup",
    "SHIELD_COMMANDS": "terok.lib.api.shield",
    "STATUS_DISPLAY": "terok.lib.api.task",
    "SandboxConfig": "terok.lib.integrations.sandbox",
    "SelinuxStatus": "terok.lib.api.setup",
    "SetupVerdict": "terok.lib.api.setup",
    "SharedMountStorageInfo": "terok.lib.api.agents",
    "ShieldCommandDef": "terok.lib.api.shield",
    "ShieldHooks": "terok.lib.api.shield",
    "ShieldManager": "terok.lib.api.shield",
    "StatusInfo": "terok.lib.api.task",
    "Task": "terok.lib.api.task",
    "TaskDeleteResult": "terok.lib.api.task",
    "TaskMeta": "terok.lib.api.task",
    "TaskStorageInfo": "terok.lib.api.agents",
    "VaultState": "terok.lib.api.vault",
    "VaultStatus": "terok.lib.api.vault",
    "load_vault_status": "terok.lib.api.vault",
    "WrongPassphraseError": "terok.lib.api.vault",
    "acp_socket_is_live": "terok.lib.api.agents",
    "agent_config_dir": "terok.lib.api.task",
    "auth_image_staleness_warning": "terok.lib.api.project",
    "authenticate": "terok.lib.api.agents",
    "available_auth_modes": "terok.lib.api.agents",
    "bold": "terok.lib.integrations.sandbox",
    "build_images": "terok.lib.api.agents",
    "bundled_default_instructions": "terok.lib.api.agents",
    "check_environment": "terok.lib.api.setup",
    "check_selinux_status": "terok.lib.api.setup",
    "cleanup_images": "terok.lib.api.project",
    "container_event_stream": "terok.lib.api.task",
    "container_name": "terok.lib.api.task",
    "create_notifier": "terok.lib.api.clearance",
    "delete_project": "terok.lib.api.project",
    "derive_project": "terok.lib.api.project",
    "discover_projects": "terok.lib.api.project",
    "effective_status": "terok.lib.api.task",
    "ensure_sandbox_ready": "terok.lib.api.agents",
    "ensure_task_running": "terok.lib.api.task",
    "execute_panic": "terok.lib.api.project",
    "find_host_auth_image": "terok.lib.api.agents",
    "find_orphaned_images": "terok.lib.api.project",
    "find_projects_sharing_gate": "terok.lib.api.project",
    "format_panic_report": "terok.lib.api.project",
    "generate_dockerfiles": "terok.lib.api.agents",
    "generate_task_name": "terok.lib.api.task",
    "get_agent": "terok.lib.api.agents",
    "get_all_task_states": "terok.lib.api.task",
    "get_login_command": "terok.lib.api.task",
    "get_project": "terok.lib.api.project",
    "get_task_meta": "terok.lib.api.task",
    "get_tasks": "terok.lib.api.task",
    "get_workspace_git_diff": "terok.lib.api.task",
    "handle_vault_seal": "terok.lib.api.vault",
    "handle_vault_to_keyring": "terok.lib.api.vault",
    "has_gpu": "terok.lib.api.task",
    "installed_agents": "terok.lib.api.agents",
    "installed_agents_for_project": "terok.lib.api.agents",
    "installed_versions": "terok.lib.api.shield",
    "is_ssh_url": "terok.lib.api.setup",
    "list_images": "terok.lib.api.project",
    "list_projects": "terok.lib.api.project",
    "load_project": "terok.lib.api.project",
    "make_git_gate": "terok.lib.api.gate",
    "make_sandbox_config": "terok.lib.core.config",
    "mark_task_deleting": "terok.lib.api.task",
    "mode_info": "terok.lib.api.task",
    "namespace_state_dir": "terok.lib.api.setup",
    "needs_setup": "terok.lib.api.setup",
    "panic_stop_containers": "terok.lib.api.project",
    "prepare_oauth_session": "terok.lib.api.agents",
    "project_image_exists": "terok.lib.api.project",
    "public_line_of": "terok.lib.api.setup",
    "read_stamp": "terok.lib.api.shield",
    "red": "terok.lib.integrations.sandbox",
    "remove_images": "terok.lib.api.project",
    "render_project_yaml": "terok.lib.api.project",
    "require_project_exists": "terok.lib.api.project",
    "resolve_agent_config": "terok.lib.api.agents",
    "resolve_container_state_dir": "terok.lib.api.setup",
    "resolve_credential_routing": "terok.lib.api.agents",
    "resolve_instructions": "terok.lib.api.agents",
    "sandbox_uninstall": "terok.lib.api.setup",
    "sanitize_task_name": "terok.lib.api.task",
    "save_tui_theme": "terok.lib.core.config",
    "selinux_install_command": "terok.lib.api.setup",
    "selinux_install_script": "terok.lib.api.setup",
    "set_experimental": "terok.lib.core.config",
    "set_project_image_agents": "terok.lib.api.project",
    "stage_line": "terok.lib.integrations.sandbox",
    "stamp_path": "terok.lib.api.shield",
    "store_api_key": "terok.lib.api.agents",
    "summarize_ssh_init": "terok.lib.api.project",
    "systemd_creds_has_tpm2": "terok.lib.api.setup",
    "task_archive_list": "terok.lib.api.task",
    "task_archive_logs": "terok.lib.api.task",
    "task_delete": "terok.lib.api.task",
    "task_followup_headless": "terok.lib.api.task",
    "task_list": "terok.lib.api.task",
    "task_login": "terok.lib.api.task",
    "task_logs": "terok.lib.api.task",
    "task_new": "terok.lib.api.task",
    "task_rename": "terok.lib.api.task",
    "task_restart": "terok.lib.api.task",
    "task_run_cli": "terok.lib.api.task",
    "task_run_headless": "terok.lib.api.task",
    "task_run_toad": "terok.lib.api.task",
    "task_status": "terok.lib.api.task",
    "task_stop": "terok.lib.api.task",
    "tasks_meta_dir": "terok.lib.api.task",
    "validate_answer": "terok.lib.api.project",
    "validate_task_name": "terok.lib.api.task",
    "vault_db": "terok.lib.api.vault",
    "wait_for_container_exit": "terok.lib.api.task",
    "write_project_yaml": "terok.lib.api.project",
    "yaml_update_section": "terok.lib.api.setup",
    "yellow": "terok.lib.integrations.sandbox",
    # Internal helpers kept resolvable (not advertised in ``__all__``): the
    # runtime driver used by ``get_container_state`` and the config/paths
    # modules ``get_config`` snapshots.  Consumers (and tests) that reach for
    # ``terok.lib.api.PodmanRuntime`` / ``._config`` / ``._paths`` still work.
    "PodmanRuntime": "terok.lib.integrations.sandbox",
    "_config": "terok.lib.core:config",
    "_paths": "terok.lib.core:paths",
}


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
    # Defaulted late additions — hand-built Config literals in tests
    # predate them, so new fields land here with a default.
    tui_theme: str | None = None


def get_config() -> Config:
    """Snapshot the global config into a single [`Config`][terok.lib.api.Config] value."""
    # Deferred: ``core.config`` pulls the sandbox adapter (and thus
    # ``terok_sandbox``); importing it lazily keeps ``import terok.lib.api``
    # off that path until a caller actually snapshots config.
    from terok.lib.core import config as _config, paths as _paths

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
        tui_theme=_config.get_tui_theme(),
        shield_security_hint=_config.SHIELD_SECURITY_HINT,
    )


def get_container_state(cname: str) -> str | None:
    """Return the live podman state for a container, or ``None`` if not found.

    Thin wrapper for one-shot state lookups.  The probe goes through
    plain ``PodmanRuntime`` because container-state reads are
    runtime-agnostic (``podman inspect`` returns the same shape under
    every OCI runtime), and the caller may not have project context
    in scope to resolve the per-project runtime.
    """
    # Resolve through this package (not the adapter directly) so a test that
    # patches ``terok.lib.api.PodmanRuntime`` intercepts the probe.
    from terok.lib.api import PodmanRuntime

    return PodmanRuntime().container(cname).state


__all__ = [
    "AUTH_PROVIDERS",
    "AuthSession",
    "Authenticator",
    "BrokenProject",
    "CommandDef",
    "CommandTree",
    "Config",
    "ContainerEventStream",
    "GPU_DISPLAY",
    "HeadlessRunRequest",
    "LogViewOptions",
    "Project",
    "ProjectConfig",
    "QUESTIONS",
    "Question",
    "SECURITY_CLASS_DISPLAY",
    "STATUS_DISPLAY",
    "SandboxConfig",
    "StatusInfo",
    "Task",
    "TaskMeta",
    "VaultState",
    "VaultStatus",
    "agent_config_dir",
    "auth_image_staleness_warning",
    "authenticate",
    "available_auth_modes",
    "bold",
    "build_images",
    "container_event_stream",
    "container_name",
    "delete_project",
    "derive_project",
    "discover_projects",
    "effective_status",
    "ensure_task_running",
    "execute_panic",
    "find_host_auth_image",
    "find_orphaned_images",
    "find_projects_sharing_gate",
    "format_panic_report",
    "generate_dockerfiles",
    "generate_task_name",
    "get_all_task_states",
    "get_config",
    "get_container_state",
    "get_login_command",
    "get_project",
    "get_task_meta",
    "get_tasks",
    "get_workspace_git_diff",
    "has_gpu",
    "installed_agents",
    "installed_agents_for_project",
    "list_images",
    "list_projects",
    "load_project",
    "load_vault_status",
    "make_git_gate",
    "make_sandbox_config",
    "mark_task_deleting",
    "mode_info",
    "panic_stop_containers",
    "project_image_exists",
    "red",
    "remove_images",
    "render_project_yaml",
    "require_project_exists",
    "resolve_agent_config",
    "resolve_credential_routing",
    "sanitize_task_name",
    "save_tui_theme",
    "set_experimental",
    "set_project_image_agents",
    "stage_line",
    "store_api_key",
    "summarize_ssh_init",
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
    "tasks_meta_dir",
    "validate_answer",
    "validate_task_name",
    "vault_db",
    "wait_for_container_exit",
    "write_project_yaml",
    "yellow",
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
