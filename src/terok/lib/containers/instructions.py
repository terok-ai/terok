# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Agent instruction resolution: layered merging with bundled defaults.

Resolves the ``instructions`` config key from the merged agent-config dict,
supporting flat strings, per-provider dicts, and lists with ``_inherit``
splicing.  Falls back to a bundled default that describes the standard
terok container environment.

Two independent layers control what a task receives:

1. **YAML ``instructions`` key** — controls the inheritance chain via config
   stack.  Uses ``_inherit`` in list form to splice the bundled default at
   that position.  Absent/None ⇒ bundled default.
2. **Standalone ``instructions.md`` file** in ``project_root`` — always
   appended at the end of whatever the YAML chain resolved.  Purely additive.
   If empty or absent, nothing is appended.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Any

_INHERIT_SENTINEL = "_inherit"


def bundled_default_instructions() -> str:
    """Read and return the bundled default instructions from package resources."""
    ref = importlib.resources.files("terok.resources.instructions").joinpath("default.md")
    return ref.read_text(encoding="utf-8")


def _read_instructions_file(project_root: Path | None) -> str:
    """Read standalone instructions.md from project root, returning empty string if absent."""
    if project_root is None:
        return ""
    path = project_root / "instructions.md"
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _splice_inherit(items: list, default: str) -> str:
    """Join list items, replacing ``_inherit`` sentinels with the bundled default."""
    parts: list[str] = []
    for item in items:
        if item == _INHERIT_SENTINEL:
            parts.append(default)
        else:
            parts.append(str(item))
    return "\n\n".join(parts)


def resolve_instructions(
    config: dict[str, Any],
    provider_name: str,
    project_root: Path | None = None,
) -> str:
    """Resolve instructions from a merged config dict.

    Supports:
    - Flat string: returned as-is
    - Per-provider dict: uses :func:`resolve_provider_value`, falls back to ``_default``
    - List (with ``_inherit``): splices bundled default at each ``_inherit`` sentinel
    - Absent/None: returns bundled default

    After resolving the YAML value, appends the contents of
    ``project_root/instructions.md`` (if it exists and is non-empty).

    Returns the final instructions text.
    """
    from .agent_config import resolve_provider_value

    val = config.get("instructions")
    default = bundled_default_instructions()

    if val is None:
        base = default
    elif isinstance(val, dict):
        resolved = resolve_provider_value("instructions", config, provider_name)
        if resolved is None:
            base = default
        elif isinstance(resolved, list):
            base = _splice_inherit(resolved, default)
        elif resolved == _INHERIT_SENTINEL:
            base = default
        else:
            base = str(resolved)
    elif isinstance(val, list):
        base = _splice_inherit(val, default)
    elif val == _INHERIT_SENTINEL:
        # Bare _inherit string → same as absent (use bundled default)
        base = default
    else:
        base = str(val)

    # Append standalone instructions file (purely additive)
    file_text = _read_instructions_file(project_root)
    if file_text:
        return f"{base}\n\n{file_text}" if base else file_text
    return base


def has_custom_instructions(
    config: dict[str, Any],
    project_root: Path | None = None,
) -> bool:
    """Check if config has explicit (non-default) instructions.

    Returns True when either the YAML ``instructions`` key is set or a
    standalone ``instructions.md`` file exists under *project_root*.
    """
    if config.get("instructions") is not None:
        return True
    return bool(project_root and (project_root / "instructions.md").is_file())
