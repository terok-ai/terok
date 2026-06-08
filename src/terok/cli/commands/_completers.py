# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared argcomplete completers and helpers for CLI commands.

All completers assume the standard ``project_name`` / ``task_id`` dest
names.  Parsers whose positionals display as ``<project>`` / ``<task>``
(e.g. ``sickbay``) should set ``dest="project_name"`` / ``dest="task_id"``
with a custom ``metavar=`` for display, so completers and argparse help
stay decoupled.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from ...lib.api import get_tasks
from ...lib.core.projects import list_projects
from ...lib.orchestration.tasks import normalize_task_id_input


def complete_project_names(
    prefix: str, parsed_args: argparse.Namespace, **kwargs: object
) -> list[str]:  # pragma: no cover
    """Return project names matching *prefix* for argcomplete."""
    try:
        ids = [p.name for p in list_projects()]
    except Exception:
        return []
    if prefix:
        ids = [i for i in ids if str(i).startswith(prefix)]
    return ids


def complete_task_ids(
    prefix: str, parsed_args: argparse.Namespace, **kwargs: object
) -> list[str]:  # pragma: no cover
    """Return task IDs matching *prefix* within ``parsed_args.project_name``.

    Returns an empty list when the project arg hasn't been typed yet —
    argcomplete uses the partially-parsed namespace, which is exactly
    what we want to scope task-ID suggestions.

    The prefix is run through [`normalize_task_id_input`][terok.lib.orchestration.tasks.normalize_task_id_input], so
    ``K3V<TAB>`` or ``k3-v<TAB>`` rewrite to the canonical lowercase
    form — the same surface-form tolerance ``resolve_task_id`` gives
    at dispatch time.
    """
    project_name = getattr(parsed_args, "project_name", None)
    if not project_name:
        return []
    try:
        tids = [t.task_id for t in get_tasks(project_name) if t.task_id]
    except Exception:
        return []
    normalized = normalize_task_id_input(prefix)
    if normalized:
        tids = [t for t in tids if t.startswith(normalized)]
    return tids


def set_completer(action: argparse.Action, fn: Callable[..., Any]) -> None:
    """Attach an argcomplete completer to *action*, ignoring missing argcomplete."""
    action.completer = fn  # type: ignore[attr-defined]


def add_project_name(parser: argparse.ArgumentParser, **kwargs: Any) -> argparse.Action:
    """Add a ``project_name`` positional with the project name completer attached.

    Returns the argparse action so callers can further customise it.
    Accepts any argparse kwargs (``nargs``, ``metavar``, ``help``, etc.).
    """
    action = parser.add_argument("project_name", **kwargs)
    set_completer(action, complete_project_names)
    return action


def add_task_id(parser: argparse.ArgumentParser, **kwargs: Any) -> argparse.Action:
    """Add a ``task_id`` positional with the task-ID completer attached.

    Returns the argparse action.  Callers should typically precede this
    with `add_project_name` so argcomplete has a project scope to
    look up tasks under.
    """
    action = parser.add_argument("task_id", **kwargs)
    set_completer(action, complete_task_ids)
    return action
