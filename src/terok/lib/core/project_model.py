# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Project data models — DDD Value Objects.

Pure data types with no filesystem or subprocess I/O.  These are the
**value objects** in the domain model: they carry configuration data but
have no behavior beyond computed paths.

[`ProjectConfig`][terok.lib.core.project_model.ProjectConfig] is loaded from ``project.yml`` by the companion
[`projects`][terok.lib.core.projects] module and wrapped by the rich
[`Project`][terok.lib.domain.project.Project] aggregate to provide behavior.
"""

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ProjectConfig(BaseModel):
    """Resolved project configuration loaded from ``project.yml``.

    Pure value object — holds configuration fields with no behavior beyond
    computed paths.  The rich domain object [`Project`][terok.lib.domain.project.Project]
    wraps this and provides behavior.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    description: str | None = None
    """Free-text, human-readable project description (optional; display only)."""
    security_class: str  # "online" | "gatekeeping"
    isolation: str = "shared"  # "shared" | "sealed"
    upstream_url: str | None
    default_branch: str | None
    root: Path

    tasks_root: Path  # workspace dirs
    gate_path: Path  # git gate (mirror) path
    gate_enabled: bool = True  # host-side gate mirror on/off (project.yml gate.enabled)
    staging_root: Path | None  # gatekeeping only

    ssh_use_personal: bool = False
    """Opt in to the user's ``~/.ssh`` keys for host-side gate-sync (default off)."""
    expose_external_remote: bool = False
    human_name: str | None = None
    human_email: str | None = None
    git_authorship: str = "agent-human"
    upstream_polling_enabled: bool = True
    upstream_polling_interval_minutes: int = 5
    auto_sync_enabled: bool = False
    auto_sync_branches: list[str] = Field(default_factory=list)
    default_agent: str | None = None
    default_provider: str | None = None
    default_shell: str | None = None
    agent_config: dict[str, Any] = Field(default_factory=dict)
    credentials_scope: Literal["shared", "project"] = "shared"
    """Credentials isolation: ``shared`` reuses the host-wide bucket;
    ``project`` carves out a private set under ``<root>/mounts`` and
    reads/writes the vault DB under set ``<name>`` instead of
    ``"default"``.  Computed via ``credential_set`` and
    ``project_mounts_dir`` on this class."""
    shutdown_timeout: int = 10
    memory: str | None = None
    """Podman ``--memory`` value from ``run.memory`` in project.yml."""
    cpus: str | None = None
    """Podman ``--cpus`` value from ``run.cpus`` in project.yml."""
    nested_containers: bool = False
    """Project runs podman/docker inside its container (see ``run.nested_containers``)."""
    runtime: Literal["crun", "krun"] | None = None
    """OCI runtime selector from ``run.runtime``.

    ``None`` (default) means "use the global default", which itself
    falls through to ``"crun"`` — the OCI runtime podman drives by
    default on every supported distro.  ``"krun"`` selects KVM-microVM
    isolation; gated on the global ``experimental: true`` flag at
    runtime selection time so a typo never silently boots the
    experimental backend.

    Sizing reuses the standard ``memory`` / ``cpus`` knobs — podman
    writes them into the OCI spec and the runtime reads them there;
    no krun-specific knob.
    """
    timezone: str | None = None
    """IANA timezone for task containers (from ``run.timezone``).

    ``None`` lets terok-executor fall back to the host's timezone; pass an
    explicit string (``"UTC"``, ``"Europe/Prague"``) to override — including
    to pin containers to UTC for reproducible runs.
    """
    task_name_categories: list[str] | None = None
    shield_drop_on_task_run: bool = True
    shield_on_task_restart: str = "retain"
    # Lifecycle hooks (host-side commands)
    hook_pre_start: str | None = None
    hook_post_start: str | None = None
    hook_post_ready: str | None = None
    hook_post_stop: str | None = None
    # Image configuration (flattened from image: section)
    base_image: str = "ubuntu:24.04"
    family: Literal["deb", "rpm"] | None = None
    """Package family override for L0/L1 builds.

    ``None`` lets terok-executor auto-detect from *base_image*; set
    explicitly when the auto-detect allowlist doesn't recognise the
    image (rocky, alma, suse, …).
    """
    agents: str = "all"
    """Comma-separated roster entries to install in L1 (or ``"all"``)."""
    snippet_inline: str | None = None
    snippet_file: str | None = None
    # Shared task directory (multi-agent IPC)
    shared_dir: Path | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_sealed(self) -> bool:
        """Whether this project uses sealed isolation (zero bind mounts)."""
        return self.isolation == "sealed"

    # Plain ``@property`` (not ``@computed_field``) so model_dump() does NOT
    # serialise these derived values back into project.yml — round-tripping
    # them would fail validation under ``extra="forbid"``.
    @property
    def credential_set(self) -> str:
        """Vault DB namespace for this project's stored credentials.

        ``"default"`` for shared-credential projects (the host-wide bucket
        every project sees by default).  When ``credentials_scope`` is
        ``"project"``, the project's name is used as the set name so its
        logins live in their own row keyed by ``(project.name, provider)``
        and never collide with another project's tokens.
        """
        return self.name if self.credentials_scope == "project" else "default"

    @property
    def known_family(self) -> str | None:
        """Package family of the task image, when recognizable.

        The explicit ``family:`` override when set, else detected from
        ``base_image`` via [`known_family`][terok_executor.known_family] —
        ``None`` for unrecognized images.  Used to render family-aware
        agent instructions (``apt`` vs ``dnf``); image builds use the
        strict variant instead.
        """
        from terok.lib.integrations.executor import known_family

        return known_family(self.base_image, self.family)

    @property
    def project_mounts_dir(self) -> Path:
        """Per-project agent-config mount tree (``_claude-config/`` etc.).

        Only consulted when ``credentials_scope`` is ``"project"``.  The
        shared case is resolved separately via
        [`sandbox_live_mounts_dir`][terok.lib.core.config.sandbox_live_mounts_dir]
        — keeping the global path off the value object avoids a domain →
        config import cycle.
        """
        return self.root / "mounts"


_PROJECT_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")

#: Reserved names that would collide with vault namespace conventions.
#: ``"default"`` is the shared credential bucket every project starts on;
#: a project literally named ``default`` with ``credentials_scope: project``
#: would silently overwrite the host-wide row.
_RESERVED_PROJECT_NAMES: frozenset[str] = frozenset({"default"})


def is_valid_project_name(project_name: str) -> bool:
    """Return whether *project_name* matches the ``[a-z0-9][a-z0-9_-]*`` contract.

    Pattern-only — this is the structural/path-safety predicate used by
    discovery and task-path guards.  Reserved-name policy (e.g.
    ``"default"``) lives in [`validate_project_name`][terok.lib.core.project_model.validate_project_name]
    instead: a legacy on-disk project named ``default`` must still be
    *discoverable* so [`discover_projects`][terok.lib.core.projects.discover_projects]
    can surface it as broken (with a rename hint) rather than silently
    dropping it from the listing.
    """
    return bool(project_name) and _PROJECT_NAME_RE.fullmatch(project_name) is not None


def validate_project_name(project_name: str) -> None:
    """Ensure a project name is safe for use as a directory and OCI image name.

    Raises SystemExit if the name is empty, contains uppercase letters, path
    separators or traversal sequences, uses characters outside
    ``[a-z0-9_-]``, or collides with a reserved name (currently only
    ``"default"`` — the shared vault credential bucket).
    """
    if not project_name or _PROJECT_NAME_RE.fullmatch(project_name) is None:
        raise SystemExit(
            f"Invalid project name '{project_name}': "
            "must start with a lowercase letter or digit, followed by lowercase letters, "
            "digits, hyphens, or underscores"
        )
    if project_name in _RESERVED_PROJECT_NAMES:
        raise SystemExit(
            f"Project name '{project_name}' is reserved (it collides with the shared "
            "credential bucket).  Pick a different name."
        )
