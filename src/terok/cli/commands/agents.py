# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Lists installed AI coding agents, sets the default, and locates their mounts.

Three leaf verbs:

- ``terok agents list [--all]`` — print the roster (agents only, or
  agents + tools and endpoint providers when ``--all`` is passed).
- ``terok agents set [SELECTION]`` — write the global default to
  ``config.yml`` under ``image.agents``.  Interactive picker when
  ``SELECTION`` is omitted; same comma-list grammar that
  ``terok image build --agents`` and the new-project wizard accept.
- ``terok agents dir [AGENT]`` — print the shared agent-config mounts
  directory (or one agent's subdirectory), surfacing the otherwise-hidden
  ``~/.local/share/terok/…/mounts/`` where skills and per-agent settings live.
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
            "List installable AI coding agents. With --all, also list tools "
            "and LLM endpoint providers. Do not put endpoint rows in "
            "image.agents; select them at runtime with --provider."
        ),
    )
    p_list.add_argument(
        "--all",
        action="store_true",
        help="Include tools and runtime LLM endpoint providers",
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

    p_dir = sub.add_parser(
        "dir",
        help="Print the shared agent-config mounts directory (or one agent's subdir)",
        description=(
            "Print the host directory that holds the per-agent config mounts "
            "bind-mounted into task containers.  With an AGENT, print that "
            "agent's config subdirectory (e.g. _claude-config) instead."
        ),
    )
    p_dir.add_argument(
        "agent",
        nargs="?",
        default=None,
        help="Optional agent name; print its config-mount subdirectory",
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle ``terok agents …``.  Returns True if handled."""
    if args.cmd != "agents":
        return False

    sub = getattr(args, "agents_cmd", None)
    if sub is None:
        # Bare ``terok agents`` — print the group's help so users see the verbs.
        print(
            "usage: terok agents {list,set,dir} ...\n\n"
            "  list  List available AI coding agents\n"
            "  set   Set the global image.agents default in config.yml\n"
            "  dir   Print the shared agent-config mounts directory\n",
            file=sys.stderr,
        )
        return True

    if sub == "list":
        _print_roster(show_all=getattr(args, "all", False))
        return True
    if sub == "set":
        _set_global_default(selection=getattr(args, "selection", None))
        return True
    if sub == "dir":
        _print_mounts_dir(agent=getattr(args, "agent", None))
        return True
    return False


def _print_roster(*, show_all: bool) -> None:
    """Print agents by default, or every known roster entry when *show_all*."""
    from terok.lib.api.agents import AgentRoster

    roster = AgentRoster.shared()
    names = roster.all_names if show_all else roster.agent_names

    if not names:
        print("No agents registered.", file=sys.stderr)
        return

    agents = roster.agents
    providers = roster.providers
    installs = roster.installs
    auth_providers = roster.auth_providers
    rows: list[tuple[str, str, str]] = []
    for name in sorted(names):
        agent = agents.get(name)
        provider = providers.get(name)
        auth = auth_providers.get(name)
        if agent is not None:
            label = agent.label
        elif auth is not None:
            label = auth.label
        else:
            label = name

        if provider is not None and provider.serves:
            entry_type = "harness" if name in installs else "endpoint"
        elif agent is not None and agent.protocol and agent.provider_binding is None:
            entry_type = "harness"
        elif agent is not None:
            entry_type = "agent"
        else:
            entry_type = "tool"
        rows.append((name, entry_type, label))

    w_name = max(len("NAME"), max(len(r[0]) for r in rows))
    w_type = max(len("TYPE"), max(len(r[1]) for r in rows))
    print(f"{'NAME':<{w_name}}  {'TYPE':<{w_type}}  LABEL")
    for name, entry_type, label in rows:
        print(f"{name:<{w_name}}  {entry_type:<{w_type}}  {label}")

    if any(entry_type == "endpoint" for _, entry_type, _ in rows):
        print("\nEndpoint entries are runtime-only (--provider), not image.agents selections.")


def _set_global_default(*, selection: str | None) -> None:
    """Validate *selection* and write it to the global ``image.agents`` field."""
    from terok.lib.api.agents import AgentRoster, ExecutorConfigView

    roster = AgentRoster.shared()
    raw = selection if selection is not None else roster.prompt_selection()
    roster.validate_selection(raw)
    path = ExecutorConfigView.set_image_agents(raw)
    print(f"Wrote image.agents = {raw!r} to {path}")


def _print_mounts_dir(*, agent: str | None) -> None:
    """Print the shared agent-config mounts directory, or one agent's subdir.

    The mounts directory holds the per-agent config trees (``_claude-config/``,
    ``_codex-config/``, …) terok bind-mounts into task containers — the place to
    drop skills or other per-agent settings.  It is
    otherwise undiscoverable; this verb surfaces it.

    With *agent*, the agent's config subdirectory is resolved from the roster;
    an unknown agent exits ``2`` with the list of agents that have a mount.
    """
    from terok.lib.core.config import sandbox_live_mounts_dir

    root = sandbox_live_mounts_dir()
    if agent is None:
        print(root)
        return

    from terok.lib.api.agents import AgentRoster

    roster = AgentRoster.shared()
    auth = roster.auth_providers.get(agent)
    if auth is None:
        available = ", ".join(sorted(roster.auth_providers)) or "(none)"
        print(
            f"Unknown agent {agent!r}.  Agents with a config mount: {available}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    print(root / auth.host_dir_name)
