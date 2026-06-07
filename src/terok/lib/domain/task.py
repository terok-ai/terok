# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Rich Task domain object — DDD Entity.

Wraps a [`TaskMeta`][terok.lib.orchestration.tasks.TaskMeta] value object with
lifecycle behavior (run, stop, delete, rename) and observation methods
(logs, login, workspace diff).

Tasks are always obtained through a [`Project`][terok.lib.domain.project.Project]::

    project = get_project("myproj")
    task = project.create_task(name="fix-bug")
    task.run_cli()
    task.logs(LogViewOptions(follow=True))
    task.stop()

**Snapshot semantics:** a ``Task`` captures a point-in-time snapshot of
[`TaskMeta`][terok.lib.domain.task.TaskMeta] at construction.  Mutations (``rename()``, ``run_cli()``,
``stop()``) modify the underlying storage but do *not* update the in-memory
snapshot.  To observe the new state after a mutation, obtain a fresh
``Task`` via ``project.get_task(id)``.  This keeps the entity free of
implicit I/O and consistent with how ``TaskMeta`` is used throughout the
codebase.

See Also:
    [`terok.lib.domain.project`][terok.lib.domain.project] — the ``Project`` aggregate that contains tasks
    [`terok.lib.orchestration.tasks`][terok.lib.orchestration.tasks] — ``TaskMeta`` value object and
        low-level task functions
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..orchestration.task_runners import (
    task_followup_headless,
    task_restart,
    task_run_cli,
)
from ..orchestration.tasks import (
    TaskMeta,
    get_login_command,
    get_workspace_git_diff,
    task_delete,
    task_login,
    task_rename,
    task_stop,
)
from .task_logs import LogViewOptions, task_logs

if TYPE_CHECKING:
    from ..core.project_model import ProjectConfig


class Task:
    """Rich task entity — DDD Entity with identity and lifecycle behavior.

    Each task has a unique identity within its project, defined by the tuple
    ``(project_id, task_id)``.  Two ``Task`` instances are equal iff they
    share this identity, regardless of metadata differences.

    Obtained via [`get_task`][terok.lib.domain.project.Project.get_task],
    [`create_task`][terok.lib.domain.project.Project.create_task], or
    [`list_tasks`][terok.lib.domain.project.Project.list_tasks].  Delegates lifecycle
    operations to the underlying task service functions in
    [`tasks`][terok.lib.orchestration.tasks] and
    [`task_runners`][terok.lib.orchestration.task_runners].
    """

    __slots__ = ("_config", "_meta")

    def __init__(self, config: ProjectConfig, meta: TaskMeta) -> None:
        """Initialize with project config and task metadata snapshot."""
        self._config = config
        self._meta = meta

    # --- Identity ---

    @property
    def id(self) -> str:
        """Return the task's ID."""
        return self._meta.task_id

    @property
    def name(self) -> str:
        """Return the task's human-readable name."""
        return self._meta.name

    @property
    def mode(self) -> str | None:
        """Return the task's mode ('cli', 'run', 'toad') or ``None``."""
        return self._meta.mode

    @property
    def status(self) -> str:
        """Return the effective status computed from container state + metadata."""
        return self._meta.status

    @property
    def container_state(self) -> str | None:
        """Live container state — ``running`` / ``exited`` / … or ``None``.

        Hydrated when the task is loaded; reflects what ``podman
        inspect`` reported at construction time.  Refresh by reloading
        the parent ``Project`` view.
        """
        return self._meta.container_state

    @property
    def meta(self) -> TaskMeta:
        """Return the underlying metadata value object."""
        return self._meta

    def __eq__(self, other: object) -> bool:
        """Two tasks are equal iff they belong to the same project and share the same ID."""
        return (
            isinstance(other, Task) and self._config.id == other._config.id and self.id == other.id
        )

    def __hash__(self) -> int:
        """Hash by (project_id, task_id) for use in sets and dicts."""
        return hash((self._config.id, self.id))

    # --- Lifecycle ---

    def run_cli(self, *, preset: str | None = None) -> None:
        """Launch a CLI-mode task container."""
        task_run_cli(self._config.id, self.id, preset=preset)

    def stop(self, *, timeout: int | None = None) -> None:
        """Gracefully stop the task container."""
        task_stop(self._config.id, self.id, timeout=timeout)

    def restart(self) -> None:
        """Restart the task container."""
        task_restart(self._config.id, self.id)

    def delete(self) -> None:
        """Delete the task (workspace, metadata, containers)."""
        task_delete(self._config.id, self.id)

    def rename(self, new_name: str) -> None:
        """Rename the task."""
        task_rename(self._config.id, self.id, new_name)

    def followup(self, prompt: str, follow: bool = True) -> None:
        """Send a follow-up prompt to a completed headless task."""
        task_followup_headless(self._config.id, self.id, prompt, follow=follow)

    # --- Observation ---

    def logs(self, options: LogViewOptions | None = None) -> None:
        """View task logs."""
        task_logs(self._config.id, self.id, options or LogViewOptions())

    def login(self) -> None:
        """Open an interactive shell in the task container."""
        task_login(self._config.id, self.id)

    def get_login_command(self) -> list[str]:
        """Return the podman exec command for login."""
        return get_login_command(self._config.id, self.id)

    def get_workspace_diff(self, against: str = "HEAD") -> str | None:
        """Get git diff from the task's workspace."""
        return get_workspace_git_diff(self._config.id, self.id, against=against)

    def show_status(self) -> None:
        """Print live task status with container state diagnostics.

        CLI-flavoured: writes to stdout.  Domain callers that want a
        structured snapshot should read :attr:`meta` directly.
        """
        from ..orchestration.tasks import task_status

        task_status(self._config.id, self.id)

    def wait_for_exit(self, *, timeout: int = 7200) -> tuple[int | None, str | None]:
        """Wait for this task's container to exit; record exit code in metadata.

        Returns ``(exit_code, error_message)``.  See
        [`wait_for_container_exit`][terok.lib.orchestration.tasks.lifecycle.wait_for_container_exit]
        for the underlying timeout semantics.
        """
        from ..core.task_state import container_name
        from ..orchestration.tasks import wait_for_container_exit

        if not self._meta.mode:
            raise RuntimeError(f"Task {self.id} has no mode — never started")
        cname = container_name(self._config.id, self._meta.mode, self.id)
        return wait_for_container_exit(cname, self._config.id, self.id, timeout=timeout)

    def capture_logs(self) -> Path | None:
        """Capture this task's container logs to disk; return the log path.

        Returns ``None`` if the task has no mode (never started) or the
        executor reports a capture failure.
        """
        from ..orchestration.tasks import capture_task_logs

        if not self._meta.mode:
            return None
        return capture_task_logs(self._config, self.id, self._meta.mode)

    def image_is_old(self) -> bool | None:
        """Return whether the task's container image is outdated.

        Compares the running container's image build-context hash against
        the project's current expected hash.  Returns ``None`` when the
        comparison is indeterminate (image deleted, container not
        running, or mode is not ``cli``) so callers can distinguish
        "definitely current" from "can't tell".
        """
        from .project_state import is_task_image_old

        return is_task_image_old(self._config.id, self._meta)

    # --- Diagnostics ---

    def doctor(
        self,
        *,
        fix: bool = False,
        reporter: object | None = None,
        label_prefix: str = "",
    ) -> list[tuple[str, str, str]]:
        """Run every layered in-container health check against this task.

        Convenience wrapper around
        [`ContainerDoctor.run`][terok.lib.orchestration.container_doctor.ContainerDoctor.run]
        — see that method for the full streaming / fix / label-prefix
        semantics.
        """
        from ..orchestration.container_doctor import ContainerDoctor

        return ContainerDoctor(self._config.id, self.id).run(
            fix=fix,
            reporter=reporter,  # type: ignore[arg-type]
            label_prefix=label_prefix,
        )

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return f"Task(id={self.id!r}, name={self.name!r}, mode={self.mode!r})"
