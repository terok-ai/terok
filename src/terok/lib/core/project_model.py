# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Project and preset data models — DDD Value Objects.

Pure data types with no filesystem or subprocess I/O.  These are the
**value objects** in the domain model: they carry configuration data but
have no behavior beyond computed paths.

[`ProjectConfig`][terok.lib.core.project_model.ProjectConfig] is loaded from ``project.yml`` by the companion
[`projects`][terok.lib.core.projects] module and wrapped by the rich
[`Project`][terok.lib.domain.project.Project] aggregate to provide behavior.
"""

import re
from dataclasses import dataclass
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

    id: str
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
    default_shell: str | None = None
    agent_config: dict[str, Any] = Field(default_factory=dict)
    credentials_scope: Literal["shared", "project"] = "shared"
    """Credentials isolation: ``shared`` reuses the host-wide bucket;
    ``project`` carves out a private set under ``<root>/mounts`` and
    reads/writes the vault DB under set ``<id>`` instead of
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def presets_dir(self) -> Path:
        """Directory for preset config files for this project."""
        return self.root / "presets"

    # Plain ``@property`` (not ``@computed_field``) so model_dump() does NOT
    # serialise these derived values back into project.yml — round-tripping
    # them would fail validation under ``extra="forbid"``.
    @property
    def credential_set(self) -> str:
        """Vault DB namespace for this project's stored credentials.

        ``"default"`` for shared-credential projects (the host-wide bucket
        every project sees by default).  When ``credentials_scope`` is
        ``"project"``, the project's id is used as the set name so its
        logins live in their own row keyed by ``(project.id, provider)``
        and never collide with another project's tokens.
        """
        return self.id if self.credentials_scope == "project" else "default"

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


@dataclass
class PresetInfo:
    """Metadata about a discovered preset."""

    name: str
    source: str  # "project" | "global" | "bundled"
    path: Path


_PROJECT_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")

#: Reserved IDs that would collide with vault namespace conventions.
#: ``"default"`` is the shared credential bucket every project starts on;
#: a project literally named ``default`` with ``credentials_scope: project``
#: would silently overwrite the host-wide row.
_RESERVED_PROJECT_IDS: frozenset[str] = frozenset({"default"})


def is_valid_project_id(project_id: str) -> bool:
    """Return whether *project_id* matches the ``[a-z0-9][a-z0-9_-]*`` contract."""
    if not project_id or _PROJECT_ID_RE.fullmatch(project_id) is None:
        return False
    return project_id not in _RESERVED_PROJECT_IDS


def validate_project_id(project_id: str) -> None:
    """Ensure a project ID is safe for use as a directory and OCI image name.

    Raises SystemExit if the ID is empty, contains uppercase letters, path
    separators or traversal sequences, uses characters outside
    ``[a-z0-9_-]``, or collides with a reserved name (currently only
    ``"default"`` — the shared vault credential bucket).
    """
    if not project_id or _PROJECT_ID_RE.fullmatch(project_id) is None:
        raise SystemExit(
            f"Invalid project ID '{project_id}': "
            "must start with a lowercase letter or digit, followed by lowercase letters, "
            "digits, hyphens, or underscores"
        )
    if project_id in _RESERVED_PROJECT_IDS:
        raise SystemExit(
            f"Project ID '{project_id}' is reserved (it collides with the shared "
            "credential bucket).  Pick a different name."
        )
