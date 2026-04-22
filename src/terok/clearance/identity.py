# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Turn a podman container ID into a task-aware :class:`ContainerIdentity`.

Composes :class:`terok_sandbox.PodmanInspector` (podman metadata +
OCI annotations) with terok's task-metadata store so clearance clients
can render "Task: project/task_id · name" bodies instead of raw short
IDs.

Sandbox knows nothing about terok's annotation keys on purpose — this
is where the two halves meet.  The annotation constants here
(``ai.terok.project`` / ``ai.terok.task``) are written on
``podman run`` by :func:`terok.lib.orchestration.task_runners._run_container`
and read back here; a rename just needs both sides updated.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from terok_dbus import ContainerIdentity
from terok_sandbox import ContainerInfo, PodmanInspector

from terok.lib.orchestration.tasks import load_task_meta

_log = logging.getLogger(__name__)

#: OCI annotations written by terok at ``podman run`` time — read
#: back here to recognise task containers.  Shared constants; changing
#: either requires an in-sync edit to ``task_runners._run_container``.
ANNOTATION_PROJECT = "ai.terok.project"
ANNOTATION_TASK = "ai.terok.task"


class IdentityResolver:
    """Compose podman inspect + task metadata into :class:`ContainerIdentity`.

    Callable: ``resolver(container_id) -> ContainerIdentity``.

    The low layer (podman inspect) carries stable facts: container
    name, state, OCI annotations.  The high layer (task metadata) adds
    the one mutable piece we can't pin on the container itself —
    ``task_name`` lives in ``tasks/meta/<id>.yml`` and can be renamed
    at any time; reading it live means a popup that fires AFTER an
    operator rename shows the new label.

    Soft-fails:

    * Missing podman metadata → empty :class:`ContainerIdentity` (the
      clearance client's ``container_id`` fallback path).
    * Missing task annotations → container-name-only identity (for
      non-terok containers that happen to hit the firewall).
    * ``load_task_meta`` failure → identity with name/project/task_id
      set but ``task_name`` empty; the subscriber renders "Task:
      project/task_id" without the suffix.

    The inspector instance's cache is shared across all calls; there's
    no caching at the task-meta layer because the file-system read is
    cheap and the mutability is the reason this resolver exists.
    """

    def __init__(self, inspector: PodmanInspector | None = None) -> None:
        """Initialise with an injected inspector (default: a fresh one)."""
        self._inspector = inspector or PodmanInspector()

    def __call__(self, container_id: str) -> ContainerIdentity:
        """Return the task-aware identity for *container_id*."""
        info: ContainerInfo = self._inspector(container_id)
        if not info.container_id:
            return ContainerIdentity()
        project = info.annotations.get(ANNOTATION_PROJECT, "")
        task_id = info.annotations.get(ANNOTATION_TASK, "")
        base = ContainerIdentity(
            container_name=info.name,
            project=project,
            task_id=task_id,
        )
        if not (project and task_id):
            return base
        return replace(base, task_name=_resolve_task_name(project, task_id))


def _resolve_task_name(project: str, task_id: str) -> str:
    """Return the human-readable task name, or ``""`` on any lookup failure."""
    try:
        meta, _ = load_task_meta(project, task_id)
    except SystemExit:
        # ``load_task_meta`` raises SystemExit for unknown tasks — harmless
        # here (the task might have been deleted since the block fired).
        return ""
    except Exception:
        _log.debug("load_task_meta failed for %s/%s", project, task_id, exc_info=True)
        return ""
    name = meta.get("name", "")
    return name if isinstance(name, str) else ""
