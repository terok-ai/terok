# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Project discovery and loading — and the writers that edit a project's egress policy."""

import copy
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from terok_util import ConfigStack, require_host_tool, write_config_file
from terok_util.config_stack import ConfigScope

from terok.lib.integrations.sandbox import CredentialsConfigError, gate_use_personal_ssh_default

from ..integrations.executor import ExecutorConfigView
from ..util.net import git_remote_host
from ..util.yaml import (
    YAMLError,
    append_entry,
    append_section,
    dump as _yaml_dump,
    dump_like,
    edit_in_place,
    literal_block,
    load as _yaml_load,
    put_key,
    set_entries,
)
from .config import (
    build_dir,
    gate_repos_dir,
    get_global_aslr_control,
    get_global_default_agent,
    get_global_default_provider,
    get_global_default_shell,
    get_global_hooks,
    get_global_hosts_file,
    get_global_section,
    get_services_clearance,
    get_services_mode,
    get_shield_down_on_task_run,
    get_shield_on_task_restart,
    projects_dir,
    sandbox_live_dir,
    user_projects_dir,
)
from .egress_sets import (
    EGRESS_SETS,
    RECOMMENDED_SET,
    EgressEdit,
    describe_egress_sets,
    granting_egress_set,
    normalize_egress_host,
    validate_egress_sets,
)
from .project_model import (  # noqa: F401 - re-exported public API
    ProjectConfig,
    ShieldOverride,
    is_valid_project_name,
    validate_project_name,
)
from .yaml_schema import RawGlobalGitSection, RawProjectYaml

logger = logging.getLogger(__name__)

_PROJECT_YML = "project.yml"
_INSTRUCTIONS_MD = "instructions.md"

# ── Git authorship policy ─────────────────────────────────────────────

DEFAULT_GIT_AUTHORSHIP = "agent-human"
"""Default Git authorship mode for task containers."""

VALID_GIT_AUTHORSHIP_MODES: tuple[str, ...] = (
    "agent-human",
    "human-agent",
    "agent",
    "human",
)
"""Supported values for ``git.authorship`` in config files."""


def normalize_git_authorship(value: object) -> str:
    """Validate and normalize a ``git.authorship`` config value.

    ``None`` or an empty string fall back to [`DEFAULT_GIT_AUTHORSHIP`][terok.lib.core.projects.DEFAULT_GIT_AUTHORSHIP].
    Raises [`SystemExit`][SystemExit] for invalid values so project loading can fail
    with a clear configuration error.
    """
    if value is None:
        return DEFAULT_GIT_AUTHORSHIP

    if not isinstance(value, str):
        valid = ", ".join(VALID_GIT_AUTHORSHIP_MODES)
        raise SystemExit(f"Invalid git.authorship value: expected a string.\nValid values: {valid}")

    normalized = value.strip().lower()
    if not normalized:
        return DEFAULT_GIT_AUTHORSHIP

    if normalized in VALID_GIT_AUTHORSHIP_MODES:
        return normalized

    valid = ", ".join(VALID_GIT_AUTHORSHIP_MODES)
    raise SystemExit(f"Invalid git.authorship value {value!r}.\nValid values: {valid}")


def _get_global_git_config(key: str) -> str | None:
    """Get a value from the user's global git config.

    Returns None if git is not available or the key is not set.
    """
    try:
        result = subprocess.run(
            ["git", "config", "--global", "--get", key],
            executable=require_host_tool("git"),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
        return None
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def _git_global_identity() -> dict[str, str]:
    """Return human_name/human_email from global git config as a dict."""
    result: dict[str, str] = {}
    name = _get_global_git_config("user.name")
    if name:
        result["human_name"] = name
    email = _get_global_git_config("user.email")
    if email:
        result["human_email"] = email
    return result


def _format_validation_error(exc: ValidationError, cfg_path: Path) -> str:
    """Format a Pydantic ValidationError into a user-friendly message."""
    lines = [f"Invalid {_PROJECT_YML} ({cfg_path}):"]
    for err in exc.errors():
        loc = " → ".join(str(p) for p in err["loc"])
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)


def _parse_project_yaml(cfg_path: Path) -> RawProjectYaml:
    """Read and validate a project.yml file, returning a typed model.

    A read failure becomes the same ``SystemExit`` that
    [`_parse_project_text`][terok.lib.core.projects._parse_project_text]
    raises for a parse or schema failure.
    """
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SystemExit(f"Failed to read {cfg_path}: {exc}") from exc
    return _parse_project_text(text, cfg_path)


def _parse_project_text(text: str, cfg_path: Path) -> RawProjectYaml:
    """Parse and validate *text* as the content of the project.yml at *cfg_path*.

    Any error parsing the text — including internal crashes from the
    YAML library itself — is converted into a single ``SystemExit``
    with the path embedded in the message.  The narrow catch list
    (YAMLError) would let ruamel.yaml's own quirks (``IndexError`` on
    certain inputs, ``AttributeError`` mid-scan, etc.) escape and crash
    whatever called us — the TUI's project-list keypress handler was
    one such path.  ``discover_projects`` already treats ``SystemExit``
    from this module as "broken project" and surfaces the entry in-UI,
    so the robust policy is: no matter what goes wrong per file, the
    app keeps running and the user sees a damaged project in the list.
    """
    try:
        raw = _yaml_load(text) or {}
    except YAMLError as exc:
        raise SystemExit(f"Failed to read {cfg_path}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - YAML parsers can raise anything; quarantine it
        raise SystemExit(f"Failed to read {cfg_path}: {type(exc).__name__}: {exc}") from exc
    try:
        return RawProjectYaml.model_validate(raw)
    except ValidationError as exc:
        raise SystemExit(_format_validation_error(exc, cfg_path)) from exc
    except Exception as exc:  # noqa: BLE001 - defensive against non-Validation pydantic surprises
        raise SystemExit(f"Failed to validate {cfg_path}: {type(exc).__name__}: {exc}") from exc


def _resolve_shield_config(raw: RawProjectYaml) -> tuple[bool, str]:
    """Resolve shield settings with project-overrides-global fallback."""
    down = (
        raw.shield.down_on_task_run
        if raw.shield.down_on_task_run is not None
        else get_shield_down_on_task_run()
    )
    restart = raw.shield.on_task_restart or get_shield_on_task_restart()
    return down, restart


def _resolve_hooks(raw: RawProjectYaml) -> tuple[str | None, str | None, str | None, str | None]:
    """Merge project run.hooks over global hook defaults."""
    g_pre, g_post, g_ready, g_stop = get_global_hooks()
    h = raw.run.hooks
    return (
        h.pre_start or g_pre,
        h.post_start or g_post,
        h.post_ready or g_ready,
        h.post_stop or g_stop,
    )


def _expand_user(value: str, key: str) -> Path:
    """Return *value* as a path with ``~`` expanded; an unknown ``~user`` fails the load, naming *key*."""
    try:
        return Path(value).expanduser()
    except RuntimeError as exc:
        raise SystemExit(f"{key}: cannot expand {value!r}: {exc}") from exc


def _build_project_config(
    raw: RawProjectYaml,
    identity: dict[str, str | None],
    root: Path,
    project_name: str,
) -> ProjectConfig:
    """Transform a validated raw YAML model + resolved identity into a flat ProjectConfig."""
    _validate_project_name_matches_directory(raw.project.name, project_name, root / _PROJECT_YML)
    pid = raw.project.name or project_name
    validate_project_name(pid)
    sec = raw.project.security_class
    tasks_root = Path(raw.tasks.root or (sandbox_live_dir() / "tasks" / pid)).resolve()
    gate_path = Path(raw.gate.path or (gate_repos_dir() / f"{pid}.git")).resolve()

    # ``gatekeeping`` mode is defined *by* the gate enforcing human review,
    # so disabling the gate in that mode is an incoherent configuration.
    # Reject at load time with a pointer at the two coherent resolutions.
    if sec == "gatekeeping" and not raw.gate.enabled:
        raise SystemExit(
            f"Project {pid!r}: security_class 'gatekeeping' requires gate.enabled: true "
            "(gatekeeping *is* the gate-enforced mode).  Either set security_class: online "
            "to drop the gate, or set gate.enabled: true."
        )

    staging_root: Path | None = None
    if sec == "gatekeeping":
        staging_root = Path(raw.gatekeeping.staging_root or (build_dir() / pid)).resolve()

    match raw.shared_dir:
        case True:
            from ..util.host_cmd import SHARED_DIRNAME

            shared_dir: Path | None = tasks_root / SHARED_DIRNAME
        case str() as s:
            p = _expand_user(s, "shared_dir")
            if not p.is_absolute():
                raise SystemExit(f"shared_dir must be an absolute path, got: {s!r}")
            shared_dir = p.resolve()
        case _:
            shared_dir = None

    snippet_path: Path | None = None
    if raw.image.user_snippet_file:
        snippet_path = _expand_user(raw.image.user_snippet_file, "image.user_snippet_file")
        if not snippet_path.is_absolute():
            snippet_path = root.resolve() / snippet_path

    agent_cfg = dict(raw.agent)

    shield_down, shield_restart = _resolve_shield_config(raw)
    validate_egress_sets(raw.shield.sets)
    hook_pre, hook_post, hook_ready, hook_stop = _resolve_hooks(raw)

    return ProjectConfig(
        name=pid,
        description=raw.project.description,
        security_class=sec,
        isolation=raw.project.isolation,
        # Normalise "" → None so downstream ``is None`` checks and truthy
        # checks agree — the wizard and the template both emit an empty
        # string for a no-upstream project, but the rest of the stack
        # treats None as the canonical "no upstream" sentinel.
        upstream_url=raw.git.upstream_url or None,
        default_branch=raw.git.default_branch or None,
        root=root.resolve(),
        tasks_root=tasks_root,
        gate_path=gate_path,
        gate_enabled=raw.gate.enabled,
        gate_backups_enabled=raw.gate.backups.enabled,
        gate_backup_retention_days=raw.gate.backups.retention_days,
        staging_root=staging_root,
        # ssh.use_personal resolves through three tiers:
        #
        #     CLI ``--use-personal-ssh``     (highest, applied in make_git_gate)
        #     project ``project.yml`` ssh
        #     global ``config.yml`` ssh      ← read via sandbox helper
        #     False                          (default)
        #
        # ``RawSSHSection.use_personal`` defaults to ``None`` (unset),
        # which lets us tell *unset* from *explicitly false* — only when
        # the project layer is unset do we fall through to the sandbox-side
        # global reader.  Sandbox owns both the schema (``RawSSHSection``)
        # and the consumer (``gate/mirror.py:_git_env_with_ssh``); terok
        # composes the project layer on top.
        ssh_use_personal=(
            raw.ssh.use_personal
            if raw.ssh.use_personal is not None
            else gate_use_personal_ssh_default()
        ),
        expose_external_remote=raw.gatekeeping.expose_external_remote,
        human_name=identity.get("human_name") or "Nobody",
        human_email=identity.get("human_email") or "nobody@localhost",
        git_authorship=normalize_git_authorship(identity.get("authorship")),
        upstream_polling_enabled=raw.gatekeeping.upstream_polling.enabled,
        upstream_polling_interval_minutes=raw.gatekeeping.upstream_polling.interval_minutes,
        auto_sync_enabled=raw.gatekeeping.auto_sync.enabled,
        auto_sync_branches=raw.gatekeeping.auto_sync.branches,
        review_lag_enabled=raw.gatekeeping.review_lag.enabled,
        review_lag_surface_in_tasks=raw.gatekeeping.review_lag.surface_in_tasks,
        default_agent=raw.default_agent or get_global_default_agent(),
        default_provider=raw.default_provider or get_global_default_provider(),
        default_shell=raw.default_shell or get_global_default_shell(),
        credentials_scope=raw.credentials.scope,
        agent_config=agent_cfg,
        shutdown_timeout=raw.run.shutdown_timeout,
        memory=raw.run.memory,
        cpus=raw.run.cpus,
        gpus=raw.run.gpus,
        nested_containers=raw.run.nested_containers,
        perf=raw.run.perf,
        aslr_control=(
            raw.run.aslr_control
            if "aslr_control" in raw.run.model_fields_set
            else get_global_aslr_control()
        ),
        hosts_file=(
            raw.run.hosts_file
            if "hosts_file" in raw.run.model_fields_set
            else get_global_hosts_file()
        ),
        podman_args=raw.run.podman_args,
        runtime=raw.run.runtime,
        timezone=raw.run.timezone,
        task_name_categories=raw.tasks.name_categories,
        services_mode=raw.services.mode or get_services_mode(),
        clearance=raw.services.clearance or get_services_clearance(),
        shield_down_on_task_run=shield_down,
        shield_on_task_restart=shield_restart,
        shield_sets=None if raw.shield.sets is None else tuple(raw.shield.sets),
        shield_allow=tuple(raw.shield.allow),
        shield_override=tuple(
            ShieldOverride(host=o.host, reason=o.reason, expires=o.expires)
            for o in raw.shield.override
        ),
        hook_pre_start=hook_pre,
        hook_post_start=hook_post,
        hook_post_ready=hook_ready,
        hook_post_stop=hook_stop,
        base_image=raw.image.base_image,
        family=raw.image.family,
        agents=raw.image.agents or (ExecutorConfigView.image_agents() or "all"),
        snippet_inline=raw.image.user_snippet_inline,
        snippet_path=snippet_path,
        shared_dir=shared_dir,
    )


def derive_project(source_id: str, new_id: str) -> Path:
    """Create a new project config that *shares infrastructure* with an existing one.

    The derived project points at the same git-gate mirror and the same SSH
    keypair as the source — only ``project.name`` and the ``agent:`` section
    differ.  This is the "sibling project" use case: rerun the same repo
    through a different image or agent without re-provisioning keys or
    re-cloning the mirror.  The source's ``instructions.md``, if present, is
    copied over so the derived project starts with the same user-provided
    guidance.

    Returns the new project's root directory.

    Raises SystemExit if the source project is not found or the target already exists.
    """
    validate_project_name(new_id)
    source = load_project(source_id)
    projects_root = user_projects_dir().resolve()
    target_root = (projects_root / new_id).resolve()

    # Guard against directory traversal (belt-and-suspenders with the regex above)
    if not target_root.is_relative_to(projects_root):
        raise SystemExit(f"Invalid project name '{new_id}': path escapes projects directory")

    if target_root.exists():
        raise SystemExit(f"Project '{new_id}' already exists at {target_root}")

    source_cfg = _yaml_load((source.root / _PROJECT_YML).read_text(encoding="utf-8")) or {}

    _rewrite_project_identity(source_cfg, new_id)
    source_cfg.pop("agent", None)
    _pin_shared_infra(source_cfg, source)

    write_config_file(target_root / _PROJECT_YML, _yaml_dump(source_cfg))

    instructions_src = source.root / _INSTRUCTIONS_MD
    if instructions_src.is_file():
        shutil.copy2(instructions_src, target_root / _INSTRUCTIONS_MD)

    return target_root


def _pin_shared_infra(cfg: dict, source: ProjectConfig) -> None:
    """Pin *source*'s resolved gate path into *cfg*.

    SSH keys are shared through the vault DB (assignments table) — no
    filesystem-level pinning required.  The gate path stays explicit so a
    derived project lands on the same mirror as its source.
    """
    cfg.setdefault("gate", {})["path"] = str(source.gate_path)


def _find_project_root(project_name: str) -> Path:
    """Return the root directory for *project_name*, preferring user over system."""
    user_root = user_projects_dir() / project_name
    sys_root = projects_dir() / project_name
    if (user_root / _PROJECT_YML).is_file():
        return user_root
    if (sys_root / _PROJECT_YML).is_file():
        return sys_root
    raise SystemExit(f"Project '{project_name}' not found in {user_root} or {sys_root}")


def _validate_project_name_matches_directory(
    declared_name: str | None, directory_name: str, cfg_path: Path
) -> None:
    """Reject a ``project.yml`` name/id that disagrees with its directory name."""
    if declared_name is None or declared_name == directory_name:
        return
    raise SystemExit(
        "Project name mismatch:\n"
        f"  directory name: {directory_name!r}\n"
        f"  project.yml name: {declared_name!r}\n"
        f"  config file: {cfg_path}\n\n"
        "Terok treats the directory name as the local project identity and "
        "requires project.name (or legacy project.id) to match it.\n"
        "Fix the file by hand, or run:\n\n"
        f"  terok project normalize-name {directory_name}\n"
    )


def _project_section_for_write(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a mutable ``project`` section, replacing invalid section shapes."""
    section = cfg.setdefault("project", {})
    if not isinstance(section, dict):
        section = {}
        cfg["project"] = section
    return section


def _rewrite_project_identity(cfg: dict[str, Any], project_name: str) -> None:
    """Rewrite ``cfg`` so its ``project`` section declares *project_name*.

    Handles both the new shape (``project.name`` is the slug) and the
    legacy shape (``project.id`` was the slug, ``project.name`` was a
    display label).  Legacy display labels are preserved as
    ``project.description`` when possible.
    """
    project_section = _project_section_for_write(cfg)
    legacy_slug = project_section.pop("id", None)
    previous_name = project_section.get("name")
    old_name_is_display_label = legacy_slug is not None or (
        isinstance(previous_name, str)
        and previous_name != project_name
        and not is_valid_project_name(previous_name)
    )
    if (
        previous_name is not None
        and project_section.get("description") is None
        and old_name_is_display_label
    ):
        project_section["description"] = previous_name
    project_section["name"] = project_name


def normalize_project_name(project_name: str) -> Path:
    """Rewrite ``project.yml`` so ``project.name`` matches its directory.

    This is the explicit quick fix for project-name mismatches: directory
    names are the local project identity, and the config file is normalised
    to declare the same name.  Legacy ``project.id`` is removed and any old
    display-only ``project.name`` is preserved as ``project.description``
    when safe.
    """
    validate_project_name(project_name)
    cfg_path = _find_project_root(project_name) / _PROJECT_YML
    try:
        cfg = _yaml_load(cfg_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        raise SystemExit(f"Failed to read {cfg_path}: {exc}") from exc
    if not isinstance(cfg, dict):
        raise SystemExit(f"Invalid {_PROJECT_YML} ({cfg_path}): expected a mapping")
    _rewrite_project_identity(cfg, project_name)
    write_config_file(cfg_path, _yaml_dump(cfg))
    return cfg_path


def require_project_exists(project_name: str) -> None:
    """Raise [`SystemExit`][SystemExit] unless *project_name* names a known project.

    Cheap stat-based check — no YAML parse, no pydantic validation.  Use
    this in CLI entry points that want to fail before any user-visible
    side effect (interactive prompt, status print, image build offer).
    The downstream [`load_project`][terok.lib.core.projects.load_project]
    call still catches malformed YAML.
    """
    _find_project_root(project_name)


def project_config_path(project_name: str) -> Path:
    """Return the ``project.yml`` that [`load_project`][terok.lib.core.projects.load_project] reads for *project_name*.

    Only finds the file and does not parse it, so it also works for a
    broken project.  Raises ``SystemExit`` when no such project exists.
    """
    return _find_project_root(project_name) / _PROJECT_YML


# ---------- Project listing ----------


@dataclass(frozen=True)
class BrokenProject:
    """A project unavailable because its local or global configuration failed to load.

    Carries just enough context for the TUI to render a row and show the
    configuration error in the details pane, without forcing callers to
    re-run the failing ``load_project`` to rediscover the message.
    ``global_config_paths`` distinguishes global configuration failures
    from errors in the project's own ``config_path``.
    """

    name: str
    config_path: Path
    error: str
    global_config_paths: tuple[Path, ...] = ()


class _GlobalConfigError(SystemExit):
    """Keep global provenance while preserving project loading's CLI exit contract."""

    def __init__(self, error: CredentialsConfigError) -> None:
        """Carry only the credential reader's secret-safe message and source paths."""
        super().__init__(_sanitize_for_tty(str(error)))
        self.config_paths = error.config_paths


def discover_projects() -> tuple[list[ProjectConfig], list[BrokenProject]]:
    """Load every project on disk, splitting successes from config-level failures.

    The broken list lets the TUI render damaged projects alongside healthy
    ones (issue #565) — silently hiding them turns "project vanished" into
    a mystery.  ``_parse_project_yaml`` wraps every config error (bad YAML,
    schema drift, filesystem issues) in ``SystemExit`` with a human-readable
    message. Global credential failures retain their source paths so callers
    can direct repairs to the global configuration instead of ``project.yml``.
    """
    paths_by_id = _discover_project_paths()
    valid: list[ProjectConfig] = []
    broken: list[BrokenProject] = []
    for pid in sorted(paths_by_id):
        try:
            valid.append(load_project(pid))
        except _GlobalConfigError as exc:
            broken.append(
                BrokenProject(
                    name=pid,
                    config_path=paths_by_id[pid],
                    error=str(exc),
                    global_config_paths=exc.config_paths,
                )
            )
        except SystemExit as exc:
            msg = _sanitize_for_tty(str(exc))
            broken.append(BrokenProject(name=pid, config_path=paths_by_id[pid], error=msg))
    return valid, broken


def list_projects() -> list[ProjectConfig]:
    """Discover all projects (user + system), warning on broken configs.

    Thin wrapper over [`discover_projects`][terok.lib.core.projects.discover_projects] that preserves the existing
    stderr + logger diagnostics for CLI callers.  The TUI uses
    [`discover_projects`][terok.lib.core.projects.discover_projects] directly to render broken entries in-place.

    User projects override system ones with the same id.
    """
    valid, broken = discover_projects()
    for bp in broken:
        # Log records are one-line structured entries; a message carrying
        # embedded newlines would split across records and could be read
        # as injected log lines.  stderr print keeps newlines so pydantic's
        # multi-line validation output is readable on the console.
        status = "blocked" if bp.global_config_paths else "broken"
        logger.warning(
            "Skipping %s project '%s': %s", status, bp.name, bp.error.replace("\n", "\\n")
        )
        print(f"warning: skipping {status} project '{bp.name}': {bp.error}", file=sys.stderr)
    return valid


def _discover_project_paths() -> dict[str, Path]:
    """Map each on-disk project name to its ``project.yml`` path.

    User scope wins over system scope for collisions — matches how
    [`load_project`][terok.lib.core.projects.load_project] resolves the effective config.  Returning the
    path alongside the ID lets [`discover_projects`][terok.lib.core.projects.discover_projects] carry the
    location forward to ``BrokenProject`` without re-walking.
    """
    paths: dict[str, Path] = {}
    for root in (user_projects_dir(), projects_dir()):
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if not d.is_dir() or d.name in paths:
                continue
            yml = d / _PROJECT_YML
            if yml.is_file() and is_valid_project_name(d.name):
                paths[d.name] = yml
    return paths


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
"""C0/C1 control characters except TAB (``\t``) and LF (``\n``).

ANSI escape sequences start with ESC (``\x1b``) and are caught here.
Error messages from pydantic / YAMLError can include attacker-supplied
bytes from project config files; blanking these prevents log-spoofing
and terminal-escape injection when messages hit an interactive stderr.
"""


def _sanitize_for_tty(s: str) -> str:
    """Strip control/escape chars so attacker-supplied bytes can't spoof TTY output."""
    return _CONTROL_CHARS.sub("?", s)


def _validated_global_git_section() -> dict[str, Any]:
    """Return the global config ``git:`` section, validated through the schema.

    If the global config has type errors in the git section (e.g. ``human_name: 123``),
    they are caught here with a clear message rather than surfacing later as a confusing
    Pydantic error during ProjectConfig construction.
    """
    raw = get_global_section("git")
    if not raw:
        return {}
    try:
        return RawGlobalGitSection.model_validate(raw).model_dump(exclude_none=True)
    except ValidationError:
        logger.warning("Invalid git section in global config, ignoring", exc_info=True)
        return {}


def load_project(project_name: str) -> ProjectConfig:
    """Load and return a fully resolved [`ProjectConfig`][terok.cli.commands.sickbay.ProjectConfig] from *project_name*."""
    root = _find_project_root(project_name)
    cfg_path = root / _PROJECT_YML
    if not cfg_path.is_file():
        raise SystemExit(f"Missing {_PROJECT_YML} in {root}")
    return _resolve_project(_parse_project_yaml(cfg_path), root, project_name)


def validate_project_yaml(project_name: str, text: str) -> str | None:
    """Return why *text* would not load as *project_name*'s ``project.yml``, or ``None``.

    Runs every check that [`load_project`][terok.lib.core.projects.load_project]
    runs, on *text* instead of the file on disk: YAML syntax, the schema,
    the name/directory match, the egress-set names and the merged git
    identity.  An editor uses this to refuse a save that would break the
    project.  A project that does not exist yet is checked as the new
    project the wizard creates in the user projects directory.  Nothing
    is written.
    """
    try:
        root = _find_project_root(project_name)
    except SystemExit:
        root = user_projects_dir() / project_name
    try:
        _resolve_project(_parse_project_text(text, root / _PROJECT_YML), root, project_name)
    except SystemExit as exc:
        return _sanitize_for_tty(str(exc))
    return None


def _resolve_project(raw: RawProjectYaml, root: Path, project_name: str) -> ProjectConfig:
    """Merge the git identity layers under *raw* and build the project's config."""
    cfg_path = root / _PROJECT_YML
    # Git identity resolved via ConfigStack: git-global → terok-global → project.yml
    git_dict = raw.git.model_dump(exclude_none=True)
    identity_stack = ConfigStack()
    identity_stack.push(ConfigScope("git-global", None, _git_global_identity()))
    identity_stack.push(ConfigScope("terok-global", None, _validated_global_git_section()))
    identity_stack.push(ConfigScope("project", cfg_path, git_dict))
    identity = identity_stack.resolve()

    try:
        return _build_project_config(raw, identity, root, project_name)
    except CredentialsConfigError as exc:
        raise _GlobalConfigError(exc) from None
    except ValidationError as exc:
        # Identity values come from merged sources (git config, global config,
        # project.yml).  Include provenance in the error so the user knows
        # where to look.
        sources = ", ".join(s.level for s in identity_stack.scopes if s.data)
        raise SystemExit(
            _format_validation_error(exc, cfg_path) + f"\n  (git identity merged from: {sources})"
        )
    except (OSError, RuntimeError, ValueError) as exc:
        # A configured path no filesystem accepts (a NUL byte, a symlink
        # loop) is a broken config like any other — after ValidationError,
        # which subclasses ValueError.
        raise SystemExit(f"Invalid {_PROJECT_YML} ({cfg_path}): {exc}") from exc


def set_project_image_agents(project_name: str, selection: str) -> Path:
    """Write *selection* into the project's ``project.yml`` under ``image.agents``.

    Caller validates *selection* up-front; on success returns the
    project.yml path written.
    """
    from terok.lib.integrations.sandbox import yaml_update_section

    cfg_path = _find_project_root(project_name) / _PROJECT_YML
    yaml_update_section(cfg_path, "image", {"agents": selection})
    return cfg_path


# ---------- Egress policy writers ----------
#
# Promotion makes ``project.yml`` writes routine, so these edit the file the
# way its owner would: the one entry changed in place, everything else —
# comments, flow lists, indentation, long lines, file mode — as it was.

_ShieldEdit = Callable[[dict[str, Any], Path], tuple[bool, str]]
"""Changes a project's ``shield:`` mapping in place, given the file's path: ``(changed, message)``."""


def set_project_shield_sets(project_name: str, sets: tuple[str, ...]) -> Path:
    """Write *sets* into the project's ``project.yml`` under ``shield.sets``.

    Validates the names first, raising ``SystemExit`` that names each
    unknown or broken set and lists the available ones.  Three selections
    are expressible: a tuple holding ``recommended`` grants every built-in
    set, including sets a later release adds; any other non-empty tuple
    freezes that exact selection (user sets included); an empty tuple
    writes ``[]`` — explicitly no sets, the same effect as leaving the key
    unset.  The list is refilled in place, so its style and the comments
    around it survive.  Returns the project.yml path.
    """
    validate_egress_sets(sets)

    def replace(shield: dict[str, Any], _cfg_path: Path) -> tuple[bool, str]:
        current = shield.get("sets")
        if isinstance(current, list) and [str(name) for name in current] == list(sets):
            return False, f"shield.sets is already {describe_egress_sets(sets)}"
        set_entries(shield, "sets", list(sets))
        return True, f"shield.sets = {describe_egress_sets(sets)}"

    return _edit_shield_section(project_name, replace).path


def set_project_snippet_inline(project_name: str, snippet: str) -> Path:
    """Write *snippet* into the project's ``project.yml`` under ``image.user_snippet_inline``.

    The snippet is stored as a literal block (``|``), so it stays
    readable in the file.  Returns the project.yml path written.
    """
    from terok.lib.integrations.sandbox import yaml_update_section

    cfg_path = _find_project_root(project_name) / _PROJECT_YML
    yaml_update_section(cfg_path, "image", {"user_snippet_inline": literal_block(snippet)})
    return cfg_path


def add_project_shield_allow(project_name: str, host: str) -> EgressEdit:
    """Append *host* to the project's ``shield.allow`` — unless something grants it already.

    Nothing is written when *host* is the project's git upstream host (its
    tasks reach that already), when an enabled egress set names it, or when
    ``shield.allow`` has it; the result says which.  Otherwise *host* joins
    the end of the list, and the project's tasks pick it up at their next
    start.

    Raises:
        ValueError: *host* is not a host
            ([`normalize_egress_host`][terok.lib.core.egress_sets.normalize_egress_host]).
        SystemExit: The project does not load, or its ``shield.allow`` is
            not a list — the file is the operator's to fix.
    """
    host = normalize_egress_host(host)
    project = load_project(project_name)
    cfg_path = _find_project_root(project_name) / _PROJECT_YML
    if project.upstream_url and git_remote_host(project.upstream_url) == host:
        return EgressEdit(
            cfg_path,
            False,
            f"{host} is the project's git upstream host, which its tasks reach already; "
            "nothing written",
        )
    if granted_by := granting_egress_set(host, project.shield_sets, project.known_family):
        return EgressEdit(
            cfg_path,
            False,
            f"{host} is granted already by the enabled egress set {granted_by!r}; nothing written",
        )

    def append(shield: dict[str, Any], path: Path) -> tuple[bool, str]:
        allow = _shield_list(shield, "allow", path)
        if allow is None:
            put_key(shield, "allow", [host])
        elif host in (str(entry).strip().lower() for entry in allow):
            return False, f"{host} is in shield.allow already; nothing written"
        else:
            append_entry(allow, host)
        return True, f"Added {host} to shield.allow"

    return _edit_shield_section(project_name, append)


def enable_project_shield_set(project_name: str, name: str) -> EgressEdit:
    """Add egress set *name* to the project's ``shield.sets``.

    *name* must be a set that loads — built-in, or a user set whose file
    parses — or ``SystemExit`` names it and its file.  An unset selection
    becomes ``[name]``; one that names *name* already, or holds
    ``recommended`` when *name* is built-in, is left alone and the result
    says so.  Otherwise *name* joins the end of the list.  ``recommended``
    never covers a user set, so enabling one next to it adds it.

    Raises:
        SystemExit: *name* is not a usable set, or the project's
            ``shield.sets`` is not a list.
    """
    validate_egress_sets((name,))

    def enable(shield: dict[str, Any], path: Path) -> tuple[bool, str]:
        sets = _shield_list(shield, "sets", path)
        if sets is None:
            put_key(shield, "sets", [name])
            return True, f"Enabled the egress set {name!r}"
        current = [str(entry) for entry in sets]
        if name in current:
            return False, f"The egress set {name!r} is enabled already; nothing written"
        if name in EGRESS_SETS and RECOMMENDED_SET in current:
            return (
                False,
                f"{name!r} is a built-in set, granted already through {RECOMMENDED_SET!r}; "
                "nothing written",
            )
        append_entry(sets, name)
        return True, f"Enabled the egress set {name!r}"

    return _edit_shield_section(project_name, enable)


def add_project_shield_override(
    project_name: str, host: str, reason: str, expires: date
) -> EgressEdit:
    """Grant *host* a break-glass ``shield.override`` — above the security-deny — for *reason*.

    Both *reason* and *expires* are required: the reason is the audit trail
    for the hole, and the expiry bounds it.  The override stays valid
    *through* *expires* — to the end of that day — so the date may be today
    but never earlier.  An existing override for *host* is updated in place
    (new reason, new expiry) and the result says what changed; otherwise
    the entry joins the end of the list.  Tasks pick it up at their next
    start.

    Raises:
        ValueError: *host* is not a host, *reason* is blank, or *expires*
            is in the past.
        SystemExit: The project's ``shield.override`` is not a list.
    """
    host = normalize_egress_host(host)
    reason = reason.strip()
    if not reason:
        raise ValueError(
            "a break-glass override needs a reason: it is the audit trail for the hole it opens"
        )
    if isinstance(expires, datetime):
        expires = expires.date()
    today = date.today()
    if expires < today:
        raise ValueError(
            f"expiry {expires.isoformat()} is in the past: an override is valid through its "
            f"expiry date, so the earliest is today ({today.isoformat()})"
        )

    def grant(shield: dict[str, Any], path: Path) -> tuple[bool, str]:
        overrides = _shield_list(shield, "override", path)
        entry = {"host": host, "reason": reason, "expires": expires}
        if overrides is None:
            put_key(shield, "override", [entry])
        elif (existing := _override_for(overrides, host)) is not None:
            return _update_override(existing, host, reason, expires)
        else:
            append_entry(overrides, entry)
        return (
            True,
            f"Added a break-glass override for {host}, valid through {expires.isoformat()} "
            f"(reason: {reason})",
        )

    return _edit_shield_section(project_name, grant)


def egress_set_users() -> dict[str, tuple[str, ...]]:
    """Which projects name each egress set in ``shield.sets``, by set name.

    Reads every project's ``project.yml`` as written, so a project that
    fails to load because a user set it names went missing still counts as
    using it; a file that does not parse at all is skipped.  Only explicit
    mentions count — ``recommended`` never covers a user set — so for a
    user set this is exactly the projects an edit to it widens.
    """
    users: dict[str, list[str]] = {}
    for project_name, cfg_path in sorted(_discover_project_paths().items()):
        try:
            names = _parse_project_yaml(cfg_path).shield.sets or ()
        except SystemExit:
            continue
        for name in dict.fromkeys(names):
            users.setdefault(name, []).append(project_name)
    return {name: tuple(projects) for name, projects in users.items()}


def _shield_list(shield: dict[str, Any], key: str, cfg_path: Path) -> list[Any] | None:
    """``shield.<key>`` when it is a list, ``None`` when unset or null.

    Raises:
        SystemExit: When it holds anything else — overwriting it would
            silently discard what the operator wrote.
    """
    value = shield.get(key)
    if value is None or isinstance(value, list):
        return value
    raise SystemExit(f"Invalid {_PROJECT_YML} ({cfg_path}): shield.{key} is not a list")


def _override_for(overrides: list[Any], host: str) -> dict[str, Any] | None:
    """The first ``shield.override`` entry for *host*, matched case-insensitively."""
    for entry in overrides:
        if isinstance(entry, dict) and str(entry.get("host", "")).strip().lower() == host:
            return entry
    return None


def _update_override(
    entry: dict[str, Any], host: str, reason: str, expires: date
) -> tuple[bool, str]:
    """Refresh an existing override's reason and expiry in place; say what changed."""
    old_reason, old_expires = entry.get("reason"), entry.get("expires")
    changes: list[str] = []
    if old_reason != reason:
        put_key(entry, "reason", reason)
        changes.append(f"reason {old_reason!r} -> {reason!r}")
    if old_expires is None or str(old_expires) != expires.isoformat():
        put_key(entry, "expires", expires)
        changes.append(f"expiry {old_expires or 'none'} -> {expires.isoformat()}")
    if not changes:
        return False, f"{host} has this override already; nothing written"
    return True, f"Updated the break-glass override for {host}: {'; '.join(changes)}"


def _edit_shield_section(project_name: str, edit: _ShieldEdit) -> EgressEdit:
    """Apply *edit* to the project's ``shield:`` mapping; write the file back if it changed.

    The file is loaded round-trip, edited, and only the lines the edit
    changed are carried back into its text
    ([`edit_in_place`][terok.lib.util.yaml.edit_in_place]), so a write
    changes what *edit* changed and nothing else — every other line stays
    byte for byte, however a dump would have spelled it.  The result is
    validated like a load before it replaces the file.  A project without a
    ``shield:`` section gets one only when *edit* changes something, written
    after its last section and above the comments that close the file
    ([`append_section`][terok.lib.util.yaml.append_section]); a file with no
    content but comments gets it below them.

    Refuses — ``SystemExit``, the file untouched, the change named for the
    operator to make by hand — when the edit's lines cannot be placed without
    rewriting lines around them, and when ``project.yml`` is write-protected:
    that is the operator's own decision about the file.
    """
    cfg_path = _find_project_root(project_name) / _PROJECT_YML
    source, doc = _load_round_trip(cfg_path)
    no_content = doc is None
    doc = {} if doc is None else doc
    if not isinstance(doc, dict):
        raise SystemExit(f"Invalid {_PROJECT_YML} ({cfg_path}): expected a mapping")
    shield = _shield_mapping(doc, cfg_path)
    changed, message = edit(shield, cfg_path)
    if not changed:
        return EgressEdit(cfg_path, False, message)
    # A file without the section gets it as a text of its own, rendered before
    # the edit below hangs the file's closing comments on it.
    added = None if "shield" in doc else copy.deepcopy(shield)
    if doc.get("shield") is None:
        put_key(doc, "shield", shield, blank_line_before=True)
    _validate_edited_project(doc, cfg_path)
    if no_content:
        # Nothing to keep but comments, which ruamel keeps none of from a
        # document without content: the new section goes below them.
        text = _dump_below_comments(source, doc)
    else:
        text = _carry_into_source(source, doc, added, cfg_path, message)
    _write_back(cfg_path, text, message)
    return EgressEdit(cfg_path, True, message)


def _load_round_trip(cfg_path: Path) -> tuple[str, Any]:
    """*cfg_path*'s text as it is on disk, and the document loaded round-trip from it.

    Raises:
        SystemExit: The file cannot be read or does not parse.
    """
    try:
        # No newline translation: a CRLF file must come back CRLF.
        with cfg_path.open(encoding="utf-8", newline="") as stream:
            source = stream.read()
        return source, _yaml_load(source)
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        raise SystemExit(f"Failed to read {cfg_path}: {exc}") from exc


def _shield_mapping(doc: dict[str, Any], cfg_path: Path) -> dict[str, Any]:
    """The document's ``shield`` when it is a mapping, a new empty one when unset or null.

    Raises:
        SystemExit: When it holds anything else — the file is the operator's
            to fix.
    """
    shield = doc.get("shield")
    if shield is None:
        return {}
    if not isinstance(shield, dict):
        raise SystemExit(f"Invalid {_PROJECT_YML} ({cfg_path}): shield is not a mapping")
    return shield


def _validate_edited_project(doc: dict[str, Any], cfg_path: Path) -> None:
    """Refuse an edit whose result would not load — the writer never leaves a broken file.

    Validates a copy: some schema validators normalise their input in
    place, and the round-trip document must reach the file as edited.
    """
    try:
        raw = RawProjectYaml.model_validate(copy.deepcopy(doc))
    except ValidationError as exc:
        raise SystemExit(_format_validation_error(exc, cfg_path)) from exc
    validate_egress_sets(raw.shield.sets)


def _dump_below_comments(source: str, doc: dict[str, Any]) -> str:
    """*doc* dumped as the file's text, below the comments *source* holds, if any.

    *source* has nothing else to keep.  The dump takes its line ends, so a
    CRLF file stays CRLF.
    """
    newline = "\r\n" if "\r\n" in source else "\n"
    text = dump_like(doc, source).replace("\n", newline)
    if source.strip():
        text = f"{source.rstrip()}{newline}{newline}{text}"
    return text


def _carry_into_source(
    source: str, doc: dict[str, Any], added: dict[str, Any] | None, cfg_path: Path, message: str
) -> str:
    """*source* with the edit carried in and every other line as it was.

    *added* is the ``shield:`` section the file lacked, appended whole;
    without one, the lines the edit changed in *doc* are carried in place.

    Raises:
        SystemExit: The change cannot be placed without rewriting lines
            around it; *message* names it for the operator to make by hand.
    """
    try:
        if added is not None:
            return append_section(source, "shield", added)
        return edit_in_place(source, doc, section="shield")
    except ValueError as exc:
        raise SystemExit(
            f"{cfg_path} was left as it was: {exc}, so terok cannot change it without "
            f"rewriting lines you wrote.  Make the change by hand — {message}."
        ) from exc


def _write_back(cfg_path: Path, text: str, message: str) -> None:
    """Replace *cfg_path*'s text with *text* — unless the operator write-protected the file.

    Raises:
        SystemExit: Permission to write was denied; *message* names the
            change for the operator to make by hand.
    """
    try:
        _replace_text(cfg_path, text)
    except PermissionError as exc:
        raise SystemExit(
            f"{cfg_path} was left as it was: {exc}.  Make it writable, or make the change "
            f"by hand — {message}."
        ) from exc


def _replace_text(path: Path, text: str) -> None:
    """Replace *path*'s content with *text* atomically, keeping its permission bits.

    The new text lands in a sibling temp file renamed over the target, so a
    reader never sees half a file.  A symlinked ``project.yml`` (a dotfiles
    checkout, say) stays a symlink: the file it points at is the one
    replaced.  The rename needs only the directory to be writable, so the
    file's own write permission is checked first: a file the operator
    write-protected is not replaced behind their back.

    Raises:
        PermissionError: The file is not writable.
    """
    target = path.resolve()
    if not os.access(target, os.W_OK):
        raise PermissionError(f"{target} is write-protected")
    mode = stat.S_IMODE(target.stat().st_mode)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)  # written as given: the line ends are the file's own
        tmp.chmod(mode)
        tmp.replace(target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
