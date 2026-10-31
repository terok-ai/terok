# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The global instructions file, and what each instructions layer inherits.

A task's instructions build up in layers: the bundled default, the global
layer from ``config.yml``, then the project layer from ``project.yml``.  The
``agent.instructions`` key of a layer decides what it keeps of the layers
below it.  A list holding ``_inherit`` keeps them, a list without it replaces
them, and no key at all keeps them.

Each layer also has a file for its own text.  The executor appends the
project's ``instructions.md`` last, whatever the key says.  The global
``instructions.md`` is different: it joins the global layer, so a project
that replaces the layers below it drops the file with the rest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..util.yaml import YAMLError, load as _yaml_load
from .config import global_config_path

INHERIT = "_inherit"
"""The ``agent.instructions`` list item that stands for the layers below."""

_PER_AGENT_FALLBACK = "_default"


def global_instructions_path() -> Path:
    """Return the global ``instructions.md``, which sits next to the user config file."""
    return global_config_path().parent / "instructions.md"


def with_global_instructions(agent_config: dict[str, Any]) -> dict[str, Any]:
    """Return the global *agent_config* with the global ``instructions.md`` as its last entry."""
    text = _read_global_instructions()
    if not text:
        return agent_config
    return {**agent_config, "instructions": _appended(agent_config.get("instructions"), text)}


def inherits_instructions(config_file: Path) -> bool | None:
    """Return whether the layer in *config_file* keeps the layers below it.

    ``None`` means the setting cannot be switched: the key is a flat text
    or a per-agent mapping, or the file does not load.
    """
    try:
        value = _read_instructions_key(config_file)
    except (OSError, UnicodeDecodeError, YAMLError, ValueError):
        return None
    match value:
        case None | "_inherit":
            return True
        case list():
            return INHERIT in value
        case _:
            return None


def set_instructions_inherit(config_file: Path, inherit: bool) -> None:
    """Make the layer in *config_file* keep or replace the layers below it.

    Entries already in the list stay.  A flat text or a per-agent mapping
    has no place for the sentinel, so it raises ``ValueError``: the
    operator decides how such a form should change.
    """
    from terok.lib.integrations.sandbox import yaml_update_section

    value = _read_instructions_key(config_file)
    match value:
        case None | "_inherit":
            entries: list[Any] = []
        case list():
            entries = [entry for entry in value if entry != INHERIT]
        case _:
            raise ValueError(
                f"agent.instructions in {config_file} is not a list; edit the file to change it"
            )
    if inherit and value is None:
        return
    updated = [INHERIT, *entries] if inherit else entries
    yaml_update_section(config_file, "agent", {"instructions": updated})


def _read_global_instructions() -> str:
    """Return the text of the global ``instructions.md``, or an empty string without one."""
    try:
        return global_instructions_path().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _read_instructions_key(config_file: Path) -> Any:
    """Return ``agent.instructions`` from *config_file*, or ``None`` when it is not set."""
    if not config_file.is_file():
        return None
    document = _yaml_load(config_file.read_text(encoding="utf-8")) or {}
    if not isinstance(document, dict):
        raise ValueError(f"{config_file}: the top level must be a mapping of settings")
    agent = document.get("agent")
    return agent.get("instructions") if isinstance(agent, dict) else None


def _appended(value: Any, text: str) -> Any:
    """Return the ``agent.instructions`` *value* with *text* at its end, in every form it takes."""
    match value:
        case None:
            return [INHERIT, text]
        case list():
            return [*value, text]
        case dict():
            per_agent = {
                agent: None if entry is None else _appended(entry, text)
                for agent, entry in value.items()
            }
            fallback = _appended(value.get(_PER_AGENT_FALLBACK), text)
            return per_agent | {_PER_AGENT_FALLBACK: fallback}
        case _:
            return [value, text]
