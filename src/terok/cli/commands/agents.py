# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Lists installed AI coding agents and sets the global default selection.

Two leaf verbs:

- ``terok agents list [--all]`` — print the roster (agents only, or
  agents + tools when ``--all`` is passed).
- ``terok agents set [SELECTION]`` — write the global default to
  ``config.yml`` under ``image.agents``.  Interactive picker when
  ``SELECTION`` is omitted; same comma-list grammar that
  ``terok image build --agents`` and the new-project wizard accept.
"""

from __future__ import annotations

import argparse
import sys


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``agents`` group with ``list`` + ``set`` subverbs."""
    p = subparsers.add_parser(
        "agents",
        help="Inspect the agent roster and set the global default selection",
        description=(
            "List the AI coding agents and tools the executor knows about, "
            "or set the global default selection in config.yml under "
            "image.agents."
        ),
    )
    sub = p.add_subparsers(dest="agents_cmd")

    p_list = sub.add_parser(
        "list",
        help="List available AI coding agents",
        description=(
            "List the AI coding agents and tools the executor knows about. "
            "Use the printed names with ``image.agents`` in project.yml or "
            "``--agents`` on ``terok task run``."
        ),
    )
    p_list.add_argument(
        "--all",
        action="store_true",
        help="Include non-agent tool entries (e.g. gh, glab, sidecar tools)",
    )

    p_set = sub.add_parser(
        "set",
        help="Set the global image.agents default (interactive when no arg)",
        description=(
            "Write the agent selection to the global config.yml under "
            "image.agents.  Validated against the installed roster before "
            "the file is touched.  Interactive picker when SELECTION is "
            "omitted."
        ),
    )
    p_set.add_argument(
        "selection",
        nargs="?",
        default=None,
        help=(
            'Agent selection in the executor\'s canonical grammar: "all", '
            'a comma list ("claude,vibe"), or "all,-name" to exclude one '
            '("all,-vibe").  Interactive picker when omitted.'
        ),
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle ``terok agents …``.  Returns True if handled."""
    if args.cmd != "agents":
        return False

    sub = getattr(args, "agents_cmd", None)
    if sub is None:
        # Bare ``terok agents`` — print the group's help so users see the verbs.
        print(
            "usage: terok agents {list,set} ...\n\n"
            "  list  List available AI coding agents\n"
            "  set   Set the global image.agents default in config.yml\n",
            file=sys.stderr,
        )
        return True

    if sub == "list":
        _print_roster(show_all=getattr(args, "all", False))
        return True
    if sub == "set":
        _set_global_default(selection=getattr(args, "selection", None))
        return True
    return False


def _print_roster(*, show_all: bool) -> None:
    """Print the installed roster — agents only by default, agents + tools when *show_all*."""
    from terok.lib.integrations.executor import get_roster

    roster = get_roster()
    names = roster.all_names if show_all else roster.agent_names

    if not names:
        print("No agents registered.", file=sys.stderr)
        return

    rows: list[tuple[str, str]] = []
    for name in sorted(names):
        provider = roster.providers.get(name)
        auth = roster.auth_providers.get(name)
        if provider is not None:
            label = provider.label
        elif auth is not None:
            label = auth.label
        else:
            label = name
        rows.append((name, label))

    w_name = max(len("NAME"), max(len(r[0]) for r in rows))
    print(f"{'NAME':<{w_name}}  LABEL")
    for name, label in rows:
        print(f"{name:<{w_name}}  {label}")


def _set_global_default(*, selection: str | None) -> None:
    """Validate *selection* and write it to the global ``image.agents`` field."""
    from terok.lib.integrations.executor import (
        prompt_agents_selection,
        set_global_image_agents,
        validate_agent_selection,
    )

    raw = selection if selection is not None else prompt_agents_selection()
    validate_agent_selection(raw)
    path = set_global_image_agents(raw)
    print(f"Wrote image.agents = {raw!r} to {path}")
