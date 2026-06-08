# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Agent config resolution: layered merging across global, project, and CLI scopes.

Builds a `ConfigStack` from up to three
layers and returns a single merged agent-config dict that can be fed directly
into [`prepare_agent_config_dir`][terok_executor.prepare_agent_config_dir].
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from terok_util import ConfigStack
from terok_util.config_stack import ConfigScope

from terok.lib.core.config import get_global_agent_config


def build_agent_config_stack(
    project_name: str,
    *,
    agent_config: dict[str, Any] | None = None,
    project_root: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> ConfigStack:
    """Build config stack: global → project → CLI overrides.

    Args:
        project_name: Project identifier (reserved for provenance display).
        agent_config: Project-level agent config dict (from ``project.agent_config``).
        project_root: Project root path (for provenance display).
        cli_overrides: CLI-level overrides (highest priority).

    Returns the `ConfigStack` so callers can either ``.resolve()`` it
    for the merged dict or inspect ``.scopes`` for provenance display.
    """
    stack = ConfigStack()

    # 1. Global agent config
    global_cfg = get_global_agent_config()
    if global_cfg:
        stack.push(ConfigScope("global", None, global_cfg))

    # 2. Project agent config (passed in by caller)
    if agent_config:
        source = (project_root / "project.yml") if project_root else None
        stack.push(ConfigScope("project", source, agent_config))

    # 3. CLI overrides
    if cli_overrides:
        stack.push(ConfigScope("cli", None, cli_overrides))

    return stack


def resolve_agent_config(
    project_name: str,
    *,
    agent_config: dict[str, Any] | None = None,
    project_root: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build config stack and return the merged agent config dict.

    Convenience wrapper around [`build_agent_config_stack`][terok.lib.orchestration.agent_config.build_agent_config_stack] for callers
    that only need the final resolved dict (e.g. task runners).
    """
    return build_agent_config_stack(
        project_name,
        agent_config=agent_config,
        project_root=project_root,
        cli_overrides=cli_overrides,
    ).resolve()
