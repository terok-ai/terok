# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Presentation tables for task lifecycle, mode, and project badges.

Maps the domain status keys produced by
[`effective_status`][terok.lib.core.task_state.effective_status] (and
the mode/security-class strings carried by project metadata) to display
attributes — emoji, color, label.

Domain logic (``TaskState``, ``effective_status``, ``container_name``,
``has_gpu``) lives in
[`task_state`][terok.lib.core.task_state].
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Display value objects ──────────────────────────────────────────────


@dataclass(frozen=True)
class StatusInfo:
    """Display attributes for a task effective status."""

    label: str
    emoji: str
    color: str


@dataclass(frozen=True)
class ModeInfo:
    """Display attributes for a task mode."""

    emoji: str
    label: str


@dataclass(frozen=True)
class ProjectBadge:
    """Display attributes for a project-level badge (security class, GPU, etc.)."""

    emoji: str
    label: str


# ── Lookup tables ──────────────────────────────────────────────────────


STATUS_DISPLAY: dict[str, StatusInfo] = {
    "running": StatusInfo(label="running", emoji="\U0001f7e2", color="green"),
    "init": StatusInfo(label="init", emoji="\U0001f7e1", color="yellow"),
    "starting": StatusInfo(label="starting", emoji="\u23f3", color="yellow"),
    "stopped": StatusInfo(label="stopped", emoji="\U0001f534", color="red"),
    "completed": StatusInfo(label="completed", emoji="\u2705", color="green"),
    "failed": StatusInfo(label="failed", emoji="\u274c", color="red"),
    "created": StatusInfo(label="created", emoji="\U0001f195", color="yellow"),
    "not found": StatusInfo(label="not found", emoji="\u2753", color="yellow"),
    "deleting": StatusInfo(label="deleting", emoji="\U0001f9f9", color="yellow"),
}

MODE_DISPLAY: dict[str | None, ModeInfo] = {
    "cli": ModeInfo(emoji="\U0001f4bb", label="CLI"),
    "run": ModeInfo(emoji="\U0001f680", label="Unattended"),
    "toad": ModeInfo(emoji="\U0001f438", label="Toad"),
    None: ModeInfo(emoji="\U0001f997", label=""),
}

SECURITY_CLASS_DISPLAY: dict[str, ProjectBadge] = {
    "gatekeeping": ProjectBadge(emoji="\U0001f6aa", label="gate"),
    "online": ProjectBadge(emoji="\U0001f310", label="online"),
}

ISOLATION_DISPLAY: dict[str, ProjectBadge] = {
    "shared": ProjectBadge(emoji="\U0001f4c2", label="shared"),
    "sealed": ProjectBadge(emoji="\U0001f512", label="sealed"),
}

GPU_DISPLAY: dict[bool, ProjectBadge] = {
    True: ProjectBadge(emoji="\U0001f3ae", label="GPU"),
    False: ProjectBadge(emoji="\U0001f4bf", label="CPU"),
}


def mode_info(mode: str | None) -> ModeInfo:
    """Return the display info for a task mode string."""
    info = MODE_DISPLAY.get(mode if isinstance(mode, str) else None)
    return info if info else MODE_DISPLAY[None]
