# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Agent-config assembly shared by the task runners.

``_prepare_agent_config`` runs the resolve → instructions → prepare-dir
sequence; ``_str_to_bool`` / ``_apply_unrestricted_env`` are the small
helpers each runner uses to resolve unrestricted mode and stamp the
auto-approve env onto the container.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from terok.lib.integrations.executor import (
    AgentConfigSpec,
    prepare_agent_config_dir,
    resolve_instructions,
)

from ...core.config import sandbox_live_mounts_dir
from ..agent_config import resolve_agent_config

if TYPE_CHECKING:
    from pathlib import Path

    from ...core.project_model import ProjectConfig

_FALSE_STRINGS = frozenset({"false", "0", "no", "off"})


def _str_to_bool(value: object) -> bool:
    """Strictly coerce a config value to bool, treating string ``"false"`` as ``False``.

    String values are stripped and lowercased before the check, so a
    blank/whitespace-only value reads as ``False`` rather than silently
    flipping unrestricted mode on.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized != "" and normalized not in _FALSE_STRINGS
    return bool(value)


def _apply_unrestricted_env(env: dict[str, str]) -> None:
    """Set ``TEROK_UNRESTRICTED`` and all agent auto-approve env vars.

    Each agent reads its own env var (``VIBE_AUTO_APPROVE``,
    ``OPENCODE_PERMISSION``, ``COPILOT_ALLOW_ALL``) regardless of how
    it is launched (CLI wrapper or ACP).  Setting them at the container
    level provides a single, unified permission mechanism.
    """
    from terok.lib.integrations.executor import collect_all_auto_approve_env

    env["TEROK_UNRESTRICTED"] = "1"
    env.update(collect_all_auto_approve_env())


def _prepare_agent_config(
    project: ProjectConfig,
    project_id: str,
    task_id: str,
    agents: list[str] | None,
    preset: str | None,
    *,
    provider_name: str | None = None,
) -> Path:
    """Resolve agent config, instructions, and prepare the agent-config dir.

    Shared by task runners to avoid duplicating the resolve → instructions →
    prepare sequence.  *provider_name* overrides the auto-detected provider
    (e.g. explicit provider selection).
    """
    effective = resolve_agent_config(
        project_id,
        agent_config=project.agent_config,
        project_root=project.root,
        preset=preset,
    )
    subagents = tuple(effective.get("subagents") or ())
    from terok.lib.integrations.executor import get_provider as _get_provider

    resolved = _get_provider(provider_name, default_agent=project.default_agent)
    instr_text = resolve_instructions(effective, resolved.name, project_root=project.root)
    return prepare_agent_config_dir(
        AgentConfigSpec(
            tasks_root=project.tasks_root,
            task_id=task_id,
            subagents=subagents,
            selected_agents=tuple(agents) if agents is not None else None,
            provider=resolved.name,
            instructions=instr_text,
            default_agent=project.default_agent,
            mounts_base=sandbox_live_mounts_dir(),
        )
    )


__all__ = [
    "_apply_unrestricted_env",
    "_prepare_agent_config",
    "_str_to_bool",
]
