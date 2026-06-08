# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Pydantic v2 models mirroring the raw YAML structure of project.yml and config.yml.

These are **Tier 1** models: they validate types, enums, and unknown-key typos
(``extra="forbid"``) but do *not* resolve paths or merge config layers.  The
companion modules [`projects`][terok.lib.core.projects] and
[`config`][terok.lib.core.config] transform these into resolved runtime objects.

Sections owned by lower-level packages live in those packages' own
``config_schema`` modules and are imported here for composition:

- [`terok_sandbox.config_schema`][terok_sandbox.config_schema] owns ``paths``, ``credentials``,
  ``vault``, ``gate_server``, ``services``, ``shield``, ``network``,
  ``ssh`` (eight sandbox-consumed sections).
- [`terok_executor.config_schema`][terok_executor.config_schema] owns ``image``.

terok itself owns the remaining four global sections (``tui``,
``logs``, ``tasks``-global, ``git``-global) plus every
``project.yml``-only section.  Task-lifecycle hooks (``run.hooks``)
and the ``run:`` section as a whole live in sandbox and inherit
through to both ``RawProjectYaml`` and ``RawGlobalConfig`` — same
schema both levels, project values override globals.
[`RawGlobalConfig`][terok.lib.core.yaml_schema.RawGlobalConfig]
inherits from [`ExecutorConfigView`][terok_executor.config_schema.ExecutorConfigView]
and flips back to ``extra="forbid"`` because terok knows the full
ecosystem section set — a typo at the top level (``tuii:``) is
caught here.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from terok.lib.integrations.executor import ExecutorConfigView, RawImageSection
from terok.lib.integrations.sandbox import RawRunSection, RawSSHSection

# ---------------------------------------------------------------------------
# Shared reusable validators / annotated types
# ---------------------------------------------------------------------------


def _coerce_name_categories(v: object) -> list[str] | None:
    """Normalize ``name_categories``: single string → list, empty → None.

    Raises [`ValueError`][ValueError] for non-string, non-list inputs (e.g. ``42``).
    """
    if v is None:
        return None
    if isinstance(v, str):
        return [v.strip()] if v.strip() else None
    if isinstance(v, list):
        if not v:
            return None
        if not all(isinstance(item, str) for item in v):
            raise ValueError("name_categories items must be strings")
        return v
    raise ValueError(f"name_categories must be a string or list of strings, got {type(v).__name__}")


NameCategories = Annotated[list[str] | None, BeforeValidator(_coerce_name_categories)]
"""Reusable type: ``list[str] | str | None`` coerced to ``list[str] | None``."""


def _coerce_none_sections(data: Any, section_keys: frozenset[str]) -> Any:
    """Pre-process raw YAML: coerce ``None`` section values to ``{}``.

    Only keys listed in *section_keys* are coerced — leaf keys that are
    legitimately ``None`` (e.g. ``upstream_url``) are left untouched.
    """
    if not isinstance(data, dict):
        return data
    return {k: ({} if k in section_keys and v is None else v) for k, v in data.items()}


# ---------------------------------------------------------------------------
# Project YAML section models
# ---------------------------------------------------------------------------


class RawProjectSection(BaseModel):
    """The ``project:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None, description="Unique project name / slug (lowercase, ``[a-z0-9_-]``)"
    )
    description: str | None = Field(
        default=None, description="Free-text, human-readable project description (display only)"
    )
    security_class: str = Field(
        default="gatekeeping",
        description="Security mode: ``gatekeeping`` (gated mirror, default) or ``online`` (direct push)",
    )
    isolation: str = Field(
        default="shared", description="shared (bind mounts) or sealed (no mounts)"
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_keys(cls, data: Any) -> Any:
        """Read pre-rename ``id`` / ``name`` keys from older ``project.yml`` files.

        Before the rename, ``id`` was the slug and ``name`` a free-text display
        label.  Now ``name`` is the slug and ``description`` the label.  When the
        legacy ``id`` key is present we treat the file as pre-rename: its ``id``
        becomes ``name`` and any old ``name`` becomes ``description``.  New files
        (no ``id`` key) pass through untouched.
        """
        if isinstance(data, dict) and "id" in data:
            data = dict(data)
            legacy_slug = data.pop("id")
            if data.get("name") is not None and data.get("description") is None:
                data["description"] = data["name"]
            data["name"] = legacy_slug
        return data

    @field_validator("security_class")
    @classmethod
    def _validate_security_class(cls, v: str) -> str:
        """Normalize and validate the security class enum."""
        v = v.strip().lower()
        if v not in ("online", "gatekeeping"):
            raise ValueError(f"must be 'online' or 'gatekeeping', got {v!r}")
        return v

    @field_validator("isolation")
    @classmethod
    def _validate_isolation(cls, v: str) -> str:
        """Normalize and validate the isolation mode enum."""
        v = v.strip().lower()
        if v not in ("shared", "sealed"):
            raise ValueError(f"must be 'shared' or 'sealed', got {v!r}")
        return v


class RawGitSection(BaseModel):
    """The ``git:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    upstream_url: str | None = Field(
        default=None, description="Repository URL to clone into task containers"
    )
    default_branch: str | None = Field(
        default=None, description="Default branch name (e.g. ``main``)"
    )
    human_name: str | None = Field(
        default=None, description="Human name for git committer identity"
    )
    human_email: str | None = Field(
        default=None, description="Human email for git committer identity"
    )
    authorship: str | None = Field(
        default=None,
        description=(
            "How agent/human map to git author/committer."
            " Values: ``agent-human``, ``human-agent``, ``agent``, ``human``"
        ),
    )


class RawGlobalGitSection(BaseModel):
    """The ``git:`` section of global config.yml (identity fields only)."""

    model_config = ConfigDict(extra="forbid")

    human_name: str | None = Field(
        default=None, description="Human name for git committer identity"
    )
    human_email: str | None = Field(
        default=None, description="Human email for git committer identity"
    )
    authorship: str | None = Field(
        default=None,
        description=(
            "How agent/human map to git author/committer."
            " Values: ``agent-human``, ``human-agent``, ``agent``, ``human``"
        ),
    )


class RawTasksSection(BaseModel):
    """The ``tasks:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    root: str | None = Field(default=None, description="Override task workspace root directory")
    name_categories: NameCategories = Field(
        default=None,
        description="Word categories for auto-generated task names (string or list of strings)",
    )


class RawGateSection(BaseModel):
    """The ``gate:`` section of project.yml.

    ``enabled`` and ``upstream_url`` are orthogonal knobs.  Four combinations:

    - ``enabled=True`` + upstream set → host mirrors upstream; container
      clones from the mirror (the default; current behaviour).
    - ``enabled=True`` + no upstream → host initialises a remoteless bare
      repo; the container still gets a remote to push to.
    - ``enabled=False`` + upstream set → host never touches the remote;
      the container fetches directly from upstream.  Useful when the host
      has no path to the upstream but the container does (firewall,
      corporate proxy), or when the mirror is simply unwanted.
    - ``enabled=False`` + no upstream → no git plumbing; the container
      starts with an empty workspace.

    When upstream is absent, ``security_class`` collapses: ``online`` and
    ``gatekeeping`` describe the same act because there's nothing to push
    to beyond the gate.  Both values are accepted and behave identically.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Enable the host-side git gate mirror for this project",
    )
    path: str | None = Field(default=None, description="Override git gate (mirror) path")


class RawUpstreamPolling(BaseModel):
    """Nested ``gatekeeping.upstream_polling`` settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Poll upstream for new commits")
    interval_minutes: int = Field(default=5, description="Polling interval in minutes")


class RawAutoSync(BaseModel):
    """Nested ``gatekeeping.auto_sync`` settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Auto-sync branches from upstream to gate")
    branches: list[str] = Field(default_factory=list, description="Branch names to auto-sync")


class RawGatekeepingSection(BaseModel):
    """The ``gatekeeping:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    staging_root: str | None = Field(
        default=None, description="Staging directory for gatekeeping builds"
    )
    expose_external_remote: bool = Field(
        default=False,
        description="Add upstream URL as ``external`` remote in gatekeeping containers",
    )
    upstream_polling: RawUpstreamPolling = Field(default_factory=RawUpstreamPolling)
    auto_sync: RawAutoSync = Field(default_factory=RawAutoSync)

    @model_validator(mode="before")
    @classmethod
    def _coerce_none_subsections(cls, data: Any) -> Any:
        """Coerce None sub-sections to empty dicts."""
        if isinstance(data, dict):
            for key in ("upstream_polling", "auto_sync"):
                if data.get(key) is None:
                    data[key] = {}
        return data


class RawShieldProjectSection(BaseModel):
    """The ``shield:`` section of project.yml.

    Both fields default to ``None`` (inherit from global ``config.yml``).
    """

    model_config = ConfigDict(extra="forbid")

    drop_on_task_run: bool | None = Field(
        default=None,
        description="Drop shield (bypass firewall) when task container is created",
    )
    on_task_restart: Literal["retain", "up"] | None = Field(
        default=None,
        description="Shield policy on container restart: ``retain`` or ``up``",
    )


class RawCredentialsSection(BaseModel):
    """The ``credentials:`` section of project.yml.

    Controls whether the project shares the host-wide credential bucket
    (the default — Claude, Codex, gh, etc. logins are reused across every
    project) or carves out its own isolated set.  Opting in is destructive
    for first-run UX: the project starts with no stored credentials and
    has to be authenticated from scratch via ``terok auth --project``.
    """

    model_config = ConfigDict(extra="forbid")

    scope: Literal["shared", "project"] = Field(
        default="shared",
        description=(
            "``shared`` (default) reuses the host-wide credential bucket and "
            "the global agent-config mount tree.  ``project`` carves out a "
            "private set under the project's own state directory — agent "
            "logins, OAuth tokens, and shared config files live separately "
            "from every other project and must be re-authenticated."
        ),
    )


# ---------------------------------------------------------------------------
# Top-level project YAML
# ---------------------------------------------------------------------------


class RawProjectYaml(BaseModel):
    """Validated structure of a ``project.yml`` file."""

    model_config = ConfigDict(extra="forbid")

    project: RawProjectSection = Field(default_factory=RawProjectSection)
    git: RawGitSection = Field(default_factory=RawGitSection)
    ssh: RawSSHSection = Field(default_factory=RawSSHSection)
    tasks: RawTasksSection = Field(default_factory=RawTasksSection)
    gate: RawGateSection = Field(default_factory=RawGateSection)
    gatekeeping: RawGatekeepingSection = Field(default_factory=RawGatekeepingSection)
    run: RawRunSection = Field(default_factory=RawRunSection)
    shield: RawShieldProjectSection = Field(default_factory=RawShieldProjectSection)
    image: RawImageSection = Field(default_factory=RawImageSection)
    credentials: RawCredentialsSection = Field(default_factory=RawCredentialsSection)
    default_agent: str | None = Field(
        default=None, description="Default agent provider (e.g. ``claude``, ``codex``)"
    )
    default_provider: str | None = Field(
        default=None,
        description="Default LLM endpoint provider the agent routes to (e.g. ``openrouter``)",
    )
    default_shell: str | None = None
    shared_dir: bool | str | None = Field(
        default=None,
        description="Shared directory for multi-agent IPC (``true`` = auto-create under tasks root, or absolute path)",
    )
    agent: dict[str, Any] = Field(
        default_factory=dict,
        description="Agent configuration dict (model, timeout, instructions, etc.)",
    )

    _SECTION_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "project",
            "git",
            "ssh",
            "tasks",
            "gate",
            "gatekeeping",
            "run",
            "shield",
            "image",
            "credentials",
            "agent",
        }
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_none_to_defaults(cls, data: Any) -> Any:
        """Coerce top-level ``None`` section values to ``{}``."""
        return _coerce_none_sections(data, cls._SECTION_KEYS)


# ---------------------------------------------------------------------------
# Global config section models — terok-owned only
#
# Sandbox-owned sections (paths, credentials, vault, gate_server, services,
# shield, network, ssh) live in [`terok_sandbox.config_schema`][terok_sandbox.config_schema]; the
# executor-owned ``image`` section lives in [`terok_executor.config_schema`][terok_executor.config_schema].
# Both are pulled in by [`RawGlobalConfig`][terok.lib.core.yaml_schema.RawGlobalConfig] via inheritance from
# [`ExecutorConfigView`][terok_executor.config_schema.ExecutorConfigView].
# ---------------------------------------------------------------------------


class RawTUISection(BaseModel):
    """Global ``tui:`` section."""

    model_config = ConfigDict(extra="forbid")

    default_tmux: bool = Field(
        default=False, description="Default to tmux mode when launching the TUI"
    )
    external_editor: bool = Field(
        default=True,
        description=(
            "Open instruction-editing actions in ``$EDITOR`` when it is set, "
            "instead of the integrated text editor.  Honoured only on a "
            "local-terminal TUI — the web TUI (``terok-web`` / textual-serve) "
            "always uses the integrated editor, as there is no terminal to "
            "suspend to.  Set ``false`` to always use the integrated editor."
        ),
    )
    desktop_entry: Literal["auto", "skip", "install"] = Field(
        default="auto",
        description=(
            "XDG desktop-entry install policy for ``terok setup`` "
            "(default: ``auto``).  ``auto`` installs only when "
            "``xdg-utils`` is on PATH and otherwise skips with a hint.  "
            "``skip`` always skips silently — recommended for headless "
            "hosts that will never resolve the launcher.  ``install`` "
            "always installs, using the built-in fallback writer when "
            "``xdg-utils`` is missing."
        ),
    )
    container_resync_seconds: int = Field(
        default=14400,
        ge=0,
        description=(
            "Full container-state resync interval, in seconds (default: "
            "14400 = 4 hours).  The task list is driven by events — inotify on "
            "task metadata plus a podman event stream — so this periodic resync "
            "is only insurance against a missed event, and is deliberately slow "
            "(à la a Kubernetes informer resync).  Set it low (e.g. ``2``) on a "
            "monitor where inotify can't be trusted (network filesystem, or no "
            "podman event stream), trading disk activity for fault tolerance; "
            "set ``0`` to disable the resync entirely and rely purely on events."
        ),
    )


class RawLogsSection(BaseModel):
    """Global ``logs:`` section."""

    model_config = ConfigDict(extra="forbid")

    partial_streaming: bool = Field(
        default=True, description="Enable typewriter-effect streaming for log viewing"
    )


class RawTasksGlobalSection(BaseModel):
    """Global ``tasks:`` section."""

    model_config = ConfigDict(extra="forbid")

    name_categories: NameCategories = Field(
        default=None,
        description="Word categories for auto-generated task names (string or list of strings)",
    )


# ---------------------------------------------------------------------------
# Top-level global config YAML
# ---------------------------------------------------------------------------


class RawGlobalConfig(ExecutorConfigView):
    """Validated structure of the global ``config.yml`` file.

    Composed from the ecosystem's per-package schemas:

    - Sandbox-owned sections (``paths``, ``credentials``, ``vault``,
      ``gate_server``, ``services``, ``shield``, ``network``, ``ssh``)
      come from [`SandboxConfigView`][terok_sandbox.config_schema.SandboxConfigView]
      via [`ExecutorConfigView`][terok_executor.config_schema.ExecutorConfigView].
    - Executor-owned ``image`` comes from
      [`ExecutorConfigView`][terok_executor.config_schema.ExecutorConfigView].
    - The ``run`` section (including the nested ``hooks``) is
      inherited transparently from sandbox via the same chain — the
      same schema applies at both project and global level, with
      project values overriding globals per the resolver below.
    - The four terok-owned global sections (``tui``, ``logs``,
      ``tasks``-global, ``git``-global) are added explicitly.

    ``extra="forbid"`` flips back on at this top-of-stack layer because
    terok knows every legitimate section.  A typo at the top level
    (``tuii:``) is caught here, even though sandbox / executor would
    have tolerated it via their ``extra="allow"`` posture.
    """

    model_config = ConfigDict(extra="forbid")

    tui: RawTUISection = Field(default_factory=RawTUISection)
    logs: RawLogsSection = Field(default_factory=RawLogsSection)
    tasks: RawTasksGlobalSection = Field(default_factory=RawTasksGlobalSection)
    git: RawGlobalGitSection = Field(default_factory=RawGlobalGitSection)
    default_agent: str | None = None
    default_provider: str | None = None
    default_shell: str | None = None
    agent: dict[str, Any] = Field(default_factory=dict)

    _SECTION_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "credentials",
            "paths",
            "tui",
            "logs",
            "shield",
            "services",
            "vault",
            "gate_server",
            "network",
            "ssh",
            "tasks",
            "git",
            "run",
            "image",
            "agent",
        }
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_none_to_defaults(cls, data: Any) -> Any:
        """Coerce top-level ``None`` section values to ``{}``."""
        return _coerce_none_sections(data, cls._SECTION_KEYS)
