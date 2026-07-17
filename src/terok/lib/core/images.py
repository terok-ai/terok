# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Container image tag conventions for the terok layer system (L0/L1/L2)."""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from typing import TYPE_CHECKING

from terok.lib.integrations.executor import AGENTS_LABEL
from terok.lib.integrations.sandbox import PodmanRuntime

if TYPE_CHECKING:
    from terok.lib.core.project_model import ProjectConfig


def _base_tag(base_image: str) -> str:
    """Derive a safe OCI tag fragment from an arbitrary *base_image* string."""
    raw = (base_image or "").strip()
    if not raw:
        raw = "ubuntu-24.04"
    tag = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-.").lower()
    if not tag:
        tag = "ubuntu-24.04"
    if len(tag) > 120:
        digest = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
        tag = f"{tag[:111]}-{digest}"
    return tag


def base_dev_image(base_image: str) -> str:
    """Return the L0 base dev image tag for *base_image*."""
    return f"terok-l0:{_base_tag(base_image)}"


def agent_cli_image(base_image: str) -> str:
    """Return the L1 CLI agent image tag for *base_image*."""
    return f"terok-l1-cli:{_base_tag(base_image)}"


def project_cli_image(project_name: str) -> str:
    """Return the L2 CLI project image tag for *project_name*."""
    return f"{project_name}:l2-cli"


def project_dev_image(project_name: str) -> str:
    """Return the L2 dev project image tag for *project_name*."""
    return f"{project_name}:l2-dev"


@lru_cache(maxsize=64)
def installed_agents(image_tag: str) -> frozenset[str]:
    """Return the set of agent names baked into *image_tag*.

    Reads the ``ai.terok.agents`` OCI label written by terok-executor's L1
    build (a sorted comma-separated list).  Derived images inherit the
    label, so an L2 tag answers for the L1 it was built on.  Result is
    cached per image tag, since the label is fixed for the life of an
    image.

    When the image is not present locally, or the label is missing
    (e.g. a legacy image built before selectable agents), returns an
    empty set — callers treat empty as "unknown / unrestricted" so older
    images keep working.
    """
    # Image label reads are runtime-agnostic — podman's image store is the
    # same regardless of which OCI runtime ends up booting it.
    csv = (PodmanRuntime().image(image_tag).labels().get(AGENTS_LABEL) or "").strip()
    if not csv:
        return frozenset()
    return frozenset(name.strip() for name in csv.split(",") if name.strip())


def installed_agents_for_project(project: ProjectConfig) -> frozenset[str]:
    """Return the agent names available in the image *project*'s tasks run.

    Reads the ``ai.terok.agents`` label from the project's L2 CLI image —
    the image a new task actually boots, which inherits the label from
    the (agent-suffixed) L1 it was built on.  The unsuffixed L1 tag
    ([`agent_cli_image`][terok.lib.core.images.agent_cli_image]) is
    deliberately not consulted: it is a default alias pointing at the
    user's *global* default selection, unrelated to what this project's
    image contains.

    When the L2 image is absent or unlabeled (not built yet, or built
    before selectable agents), falls back to resolving the project
    definition's ``image.agents`` selection — the set a rebuild would
    install.  Only an unresolvable selection yields an empty set;
    callers treat empty as "unknown / unrestricted".
    """
    return installed_agents(project_cli_image(project.name)) or _agents_from_selection(
        project.agents
    )


def _agents_from_selection(selection: str) -> frozenset[str]:
    """Resolve an ``image.agents`` selection string into concrete roster names.

    Returns an empty set when the selection names unknown roster entries
    (a stale or hand-edited project.yml) — an honest "unknown", not a
    guess.
    """
    from terok.lib.integrations.executor import AgentRoster

    roster = AgentRoster.shared()
    try:
        return frozenset(roster.resolve_selection(AgentRoster.parse_selection(selection)))
    except ValueError:
        return frozenset()


def require_agent_installed(project: ProjectConfig, name: str, *, noun: str = "Agent") -> None:
    """Fail fast if *name* is not available in the image *project*'s tasks run.

    Used at CLI / TUI / runtime entry points so the user sees a clear,
    actionable message instead of a deep ``command not found`` later.
    Checks the same source the TUI pickers offer from
    ([`installed_agents_for_project`][terok.lib.core.images.installed_agents_for_project]),
    so an agent the picker offered is never rejected here.  An empty
    (unknown) result is treated as unrestricted.
    """
    available = installed_agents_for_project(project)
    if not available or name in available:
        return
    raise SystemExit(
        f"{noun} {name!r} is not available in the image for "
        f"project {project.name!r} ({project_cli_image(project.name)}).\n"
        f"Available: {', '.join(sorted(available))}\n"
        f"Add it to image.agents and rebuild: "
        f"terok project build --agents {name} {project.name}"
    )
