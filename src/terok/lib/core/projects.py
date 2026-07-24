# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Project discovery and loading."""

import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from terok_util import ConfigStack
from terok_util.config_stack import ConfigScope

from terok.lib.integrations.sandbox import gate_use_personal_ssh_default

from ..integrations.executor import ExecutorConfigView
from ..util.yaml import YAMLError, dump as _yaml_dump, load as _yaml_load
from .config import (
    build_dir,
    gate_repos_dir,
    get_global_default_agent,
    get_global_default_provider,
    get_global_default_shell,
    get_global_hooks,
    get_global_section,
    get_shield_drop_on_task_run,
    get_shield_on_task_restart,
    projects_dir,
    sandbox_live_dir,
    user_projects_dir,
)
from .project_model import (  # noqa: F401 — re-exported public API
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
            ["git", "config", "--global", "--get", key], capture_output=True, text=True, check=False
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
    """Parse and validate a project.yml file, returning a typed model.

    Any error reading or parsing the file — including internal crashes
    from the YAML library itself — is converted into a single
    ``SystemExit`` with the path embedded in the message.  The narrow
    catch list (OSError, UnicodeDecodeError, YAMLError) would let
    ruamel.yaml's own quirks (``IndexError`` on certain inputs,
    ``AttributeError`` mid-scan, etc.) escape and crash whatever
    called us — the TUI's project-list keypress handler was one such
    path.  ``discover_projects`` already treats ``SystemExit`` from
    this module as "broken project" and surfaces the entry in-UI, so
    the robust policy is: no matter what goes wrong per file, the app
    keeps running and the user sees a damaged project in the list.
    """
    try:
        raw = _yaml_load(cfg_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        raise SystemExit(f"Failed to read {cfg_path}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — YAML parsers can raise anything; quarantine it
        raise SystemExit(f"Failed to read {cfg_path}: {type(exc).__name__}: {exc}") from exc
    try:
        return RawProjectYaml.model_validate(raw)
    except ValidationError as exc:
        raise SystemExit(_format_validation_error(exc, cfg_path)) from exc
    except Exception as exc:  # noqa: BLE001 — defensive against non-Validation pydantic surprises
        raise SystemExit(f"Failed to validate {cfg_path}: {type(exc).__name__}: {exc}") from exc


def _resolve_shield_config(raw: RawProjectYaml) -> tuple[bool, str]:
    """Resolve shield settings with project-overrides-global fallback."""
    drop = (
        raw.shield.drop_on_task_run
        if raw.shield.drop_on_task_run is not None
        else get_shield_drop_on_task_run()
    )
    restart = raw.shield.on_task_restart or get_shield_on_task_restart()
    return drop, restart


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
            p = Path(s).expanduser()
            if not p.is_absolute():
                raise SystemExit(f"shared_dir must be an absolute path, got: {s!r}")
            shared_dir = p.resolve()
        case _:
            shared_dir = None

    agent_cfg = dict(raw.agent)

    shield_drop, shield_restart = _resolve_shield_config(raw)
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
        podman_args=raw.run.podman_args,
        runtime=raw.run.runtime,
        timezone=raw.run.timezone,
        task_name_categories=raw.tasks.name_categories,
        shield_drop_on_task_run=shield_drop,
        shield_on_task_restart=shield_restart,
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
        snippet_file=raw.image.user_snippet_file,
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

    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / _PROJECT_YML).write_text(
        _yaml_dump(source_cfg),
        encoding="utf-8",
    )

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
    cfg_path.write_text(_yaml_dump(cfg), encoding="utf-8")
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


# ---------- Project listing ----------


@dataclass(frozen=True)
class BrokenProject:
    """A project directory whose ``project.yml`` failed to load.

    Carries just enough context for the TUI to render a row and show the
    validation error in the details pane, without forcing callers to
    re-run the failing ``load_project`` to rediscover the message.
    """

    name: str
    config_path: Path
    error: str


def discover_projects() -> tuple[list[ProjectConfig], list[BrokenProject]]:
    """Load every project on disk, splitting successes from config-level failures.

    The broken list lets the TUI render damaged projects alongside healthy
    ones (issue #565) — silently hiding them turns "project vanished" into
    a mystery.  ``_parse_project_yaml`` wraps every config error (bad YAML,
    schema drift, filesystem issues) in ``SystemExit`` with a human-readable
    message; anything else propagates as a genuine bug.
    """
    paths_by_id = _discover_project_paths()
    valid: list[ProjectConfig] = []
    broken: list[BrokenProject] = []
    for pid in sorted(paths_by_id):
        try:
            valid.append(load_project(pid))
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
        logger.warning("Skipping broken project '%s': %s", bp.name, bp.error.replace("\n", "\\n"))
        print(f"warning: skipping broken project '{bp.name}': {bp.error}", file=sys.stderr)
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

    raw = _parse_project_yaml(cfg_path)

    # Git identity resolved via ConfigStack: git-global → terok-global → project.yml
    git_dict = raw.git.model_dump(exclude_none=True)
    identity_stack = ConfigStack()
    identity_stack.push(ConfigScope("git-global", None, _git_global_identity()))
    identity_stack.push(ConfigScope("terok-global", None, _validated_global_git_section()))
    identity_stack.push(ConfigScope("project", cfg_path, git_dict))
    identity = identity_stack.resolve()

    try:
        return _build_project_config(raw, identity, root, project_name)
    except ValidationError as exc:
        # Identity values come from merged sources (git config, global config,
        # project.yml).  Include provenance in the error so the user knows
        # where to look.
        sources = ", ".join(s.level for s in identity_stack.scopes if s.data)
        raise SystemExit(
            _format_validation_error(exc, cfg_path) + f"\n  (git identity merged from: {sources})"
        )


def set_project_image_agents(project_name: str, selection: str) -> Path:
    """Write *selection* into the project's ``project.yml`` under ``image.agents``.

    Caller validates *selection* up-front; on success returns the
    project.yml path written.
    """
    from terok.lib.integrations.sandbox import yaml_update_section

    cfg_path = _find_project_root(project_name) / _PROJECT_YML
    yaml_update_section(cfg_path, "image", {"agents": selection})
    return cfg_path
