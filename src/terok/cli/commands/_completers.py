# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared argcomplete completers and helpers for CLI commands."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from ...lib.core.projects import list_projects


def complete_project_ids(
    prefix: str, parsed_args: argparse.Namespace, **kwargs: object
) -> list[str]:  # pragma: no cover
    """Return project IDs matching *prefix* for argcomplete."""
    try:
        ids = [p.id for p in list_projects()]
    except Exception:
        return []
    if prefix:
        ids = [i for i in ids if str(i).startswith(prefix)]
    return ids


def set_completer(action: argparse.Action, fn: Callable[..., Any]) -> None:
    """Attach an argcomplete completer to *action*, ignoring missing argcomplete."""
    action.completer = fn  # type: ignore[attr-defined]
