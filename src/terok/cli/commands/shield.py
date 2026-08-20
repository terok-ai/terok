# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shield egress firewall management commands.

Uses the ``terok_shield`` command registry to build subcommands.
Commands that need a container take positional ``project_name task_id``
(same convention as ``terok task …``), which are resolved to a
container name + task directory for the registry handler.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from terok.lib.api import shield as _shield_api
from terok.lib.api.shield import (
    SHIELD_COMMANDS as COMMANDS,
    ArgDef,
    ExecError,
    ShieldCommandDef as CommandDef,
    ShieldManager,
    shield_needs_container,
    shield_standalone_only,
)

from ...lib.core.config import make_sandbox_config
from ...lib.orchestration.tasks import resolve_task_id


def _add_arg(parser: argparse.ArgumentParser, arg: ArgDef) -> None:
    """Register an `ArgDef` with an argparse parser."""
    kwargs: dict = {}
    if arg.help:
        kwargs["help"] = arg.help
    for field in ("type", "default", "action", "dest", "nargs"):
        val = getattr(arg, field)
        if val is not None:
            kwargs[field] = val
    parser.add_argument(arg.name, **kwargs)


def _resolve_task(project_name: str, task_id: str) -> tuple[str, Path]:
    """Resolve project+task to (container_name, task_dir).

    Returns:
        Tuple of (container_name, task_dir) for constructing a Shield.

    Raises:
        ValueError: If the task has never been run (no container exists).
    """
    from ...lib.core.projects import load_project
    from ...lib.orchestration.tasks import container_name, load_task_meta

    project = load_project(project_name)
    meta, _ = load_task_meta(project.name, task_id)
    mode = meta.get("mode")
    if mode is None:
        raise ValueError(
            f"Task {task_id} in project {project_name!r} has never been run — no container exists"
        )
    cname = container_name(project.name, mode, task_id)
    task_dir = project.tasks_root / str(task_id)
    return cname, task_dir


def _extract_handler_kwargs(args: argparse.Namespace, cmd_def: CommandDef) -> dict:
    """Extract keyword arguments for a registry handler from parsed args.

    Skips the positional ``container`` arg (the CLI resolves it from
    ``project_name`` + ``task_id``) and ``--container-id``
    (the orchestrator resolves it from the container's UUID — see
    [`resolve_container_uuid`][terok.lib.orchestration.task_runners.shield.resolve_container_uuid]).
    """
    kwargs: dict = {}
    for arg in cmd_def.args:
        if arg.name in {"container", "--container-id"}:
            continue
        key = arg.dest or arg.name.lstrip("-").replace("-", "_")
        if hasattr(args, key):
            kwargs[key] = getattr(args, key)
    return kwargs


_DESIRED_STATE_FILENAME = "shield_desired_state"


def _persist_desired_state(cmd_name: str, task_dir: Path, kwargs: dict) -> None:
    """Write desired shield state after a successful ``up`` or ``down`` command.

    Persists the operator's intent so ``on_task_restart: retain`` can
    restore the correct state after a container stop/start cycle.
    Best-effort: OSError is logged but swallowed so the shield command
    itself stays successful.
    """
    if cmd_name == "up":
        value = "up"
    elif cmd_name == "down":
        value = "disengaged" if kwargs.get("disengaged") else "down"
    else:
        return
    try:
        (task_dir / _DESIRED_STATE_FILENAME).write_text(f"{value}\n")
    except OSError as exc:
        print(
            f"Warning: could not persist {_DESIRED_STATE_FILENAME} to {task_dir}: {exc}",
            file=sys.stderr,
        )


def _resolved_commands() -> tuple[CommandDef, ...]:
    """Materialise the (now lazy) terok-shield command registry.

    terok-shield ships lazy [`CommandDef`][terok_util.cli_types.CommandDef]s
    (``source`` set) whose ``args`` / ``handler`` / ``extras`` (which
    ``needs_container`` / ``standalone_only`` read) populate only on
    ``resolve()``.  Both register and dispatch read those fields, so resolve
    up front — this runs only for an actual ``terok shield`` invocation (or
    the full ``--help`` surface), never for another verb.
    """
    return tuple(cmd.resolve() for cmd in COMMANDS)


def _print_set_registry() -> None:
    """List every curated set with the hosts it grants."""
    from terok.lib.api import EGRESS_SETS, OS_PACKAGES_SUMMARY

    print("Curated egress sets (project.yml shield.sets; unset = all):")
    for name, hosts in EGRESS_SETS.items():
        print(f"  {name}: {', '.join(hosts) or OS_PACKAGES_SUMMARY}")


def _print_project_sets(project_name: str) -> None:
    """Show a project's effective selection and where it comes from."""
    from terok.lib.api import load_project, selected_egress_sets

    sets = load_project(project_name).shield_sets
    origin = "default: all sets" if sets is None else "from project.yml"
    print(f"Active egress sets for {project_name} ({origin}):")
    print("  " + (", ".join(selected_egress_sets(sets)) or "none (curated content disabled)"))


def _parse_set_selection(selection: str) -> tuple[str, ...] | None:
    """Map a ``--set`` value onto ``shield.sets``: 'default' → unset, 'none' → empty."""
    word = selection.strip().lower()
    if word == "default":
        return None
    if word == "none":
        return ()
    return tuple(s.strip() for s in selection.split(",") if s.strip())


def _write_project_sets(project_name: str, selection: str) -> None:
    """Replace a project's ``shield.sets`` and report the new selection."""
    from terok.lib.api import describe_egress_sets, set_project_shield_sets

    chosen = _parse_set_selection(selection)
    path = set_project_shield_sets(project_name, chosen)
    print(f"shield.sets for {project_name}: {describe_egress_sets(chosen)}\nWritten to {path}")
    print("Tasks pick the new selection up at their next start or restart.")


def _handle_sets(project_name: str | None, selection: str | None) -> None:
    """List the curated egress sets; show or replace a project's selection.

    Without a project: the registry with each set's hosts.  With a project:
    its effective selection (the generous default when ``shield.sets`` is
    unset).  With ``--set``: replace the selection (``none`` → explicit
    empty list) and remind that running containers pick it up on restart.
    """
    if selection is not None and project_name is None:
        print("Error: --set requires a project name", file=sys.stderr)
        sys.exit(1)
    if selection is not None and project_name is not None:
        _write_project_sets(project_name, selection)
    elif project_name is not None:
        _print_project_sets(project_name)
    else:
        _print_set_registry()


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``shield`` subcommand group from the registry."""
    p = subparsers.add_parser("shield", help="Manage egress firewall (terok-shield)")
    sub = p.add_subparsers(dest="shield_cmd", required=True)

    for cmd in _resolved_commands():
        if shield_standalone_only(cmd):
            continue

        sp = sub.add_parser(cmd.name, help=cmd.help)

        # Commands that need a container get positional project_name + task_id,
        # matching the ``terok task …`` convention.  Commands with an
        # *optional* container arg (like ``status``) get nargs="?" so they
        # work both with and without a task target.  Completers attach
        # either way so tab-complete works in both forms.
        from ._completers import add_project_name, add_task_id

        if shield_needs_container(cmd):
            add_project_name(sp, help="Project name")
            add_task_id(sp, help="Task ID")
        elif any(a.name == "container" for a in cmd.args):
            add_project_name(sp, nargs="?", help="Project name")
            add_task_id(sp, nargs="?", help="Task ID")

        # ``--container-id`` is the per-container hub socket routing
        # key; terok always knows the UUID at the call site (it
        # invokes ``ShieldManager.up`` / ``down`` directly from the
        # task runner), so we don't surface it as a CLI flag — the
        # dispatch function injects it from a ``podman inspect`` lookup.
        for arg in cmd.args:
            if arg.name in {"container", "--container-id"}:
                continue
            _add_arg(sp, arg)

    # Manually register install-hooks (standalone_only in registry, needs
    # subprocess passthrough).  Named explicitly so it doesn't shadow the
    # top-level ``terok setup`` which installs *all* host services — this
    # one touches only the shield OCI hooks.
    sub.add_parser("install-hooks", help="Install global OCI hooks for shield")

    # Manually registered: the curated-set chooser is a terok concept
    # (authored t40 content), not a terok-shield registry command.
    sets_p = sub.add_parser("sets", help="List curated egress sets; show or set a project's choice")
    from ._completers import add_project_name

    add_project_name(sets_p, nargs="?", help="Project whose selection to show or change")
    sets_p.add_argument(
        "--set",
        dest="sets_selection",
        metavar="SET[,SET…]",
        help=(
            "Replace the project's shield.sets with this comma-separated selection "
            "('none' disables every curated set, 'default' restores the generous "
            "default; requires a project name)"
        ),
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle shield commands.  Returns True if handled."""
    if args.cmd != "shield":
        return False

    cmd_name = args.shield_cmd

    # install-hooks is standalone_only and needs subprocess passthrough
    # (no registry handler).  Single layout: descriptors, scripts, and
    # ballast all land in the canonical terok-owned dir under
    # ``paths.root``.
    if cmd_name == "install-hooks":
        # Module-attribute access so the test ``@patch("...ShieldHooks.install")``
        # intercepts the call.
        _shield_api.ShieldHooks.install()
        return True

    if cmd_name == "sets":
        _handle_sets(getattr(args, "project_name", None), getattr(args, "sets_selection", None))
        return True

    cmd_lookup = {cmd.name: cmd for cmd in _resolved_commands() if not shield_standalone_only(cmd)}
    cmd_def = cmd_lookup.get(cmd_name)
    if cmd_def is None or cmd_def.handler is None:
        return False

    project_name = getattr(args, "project_name", None)
    task_id = getattr(args, "task_id", None)
    if (project_name is None) != (task_id is None):
        print("Error: provide both <project_name> and <task_id>, or neither", file=sys.stderr)
        sys.exit(1)
    has_task = project_name is not None and task_id is not None

    try:
        # mypy narrows the inner pair via the explicit check; ``has_task``
        # is kept around for the except branch's error wording below.
        if project_name is not None and task_id is not None:
            task_id = resolve_task_id(project_name, task_id)
            cname, task_dir = _resolve_task(project_name, task_id)
            shield = ShieldManager(task_dir, make_sandbox_config()).shield
            kwargs = _extract_handler_kwargs(args, cmd_def)
            if cmd_name in {"up", "down"}:
                # ``container_id`` is the per-container hub socket
                # routing key — resolved from the live container at
                # dispatch time so the operator never needs to think
                # about UUIDs.
                from terok.lib.orchestration.task_runners import resolve_container_uuid

                kwargs["container_id"] = resolve_container_uuid(cname)
            if shield_needs_container(cmd_def):
                cmd_def.handler(shield, cname, **kwargs)
                _persist_desired_state(cmd_name, task_dir, kwargs)
            else:
                # Optional container arg (e.g. ``status <project> <task>``)
                kwargs["container"] = cname
                cmd_def.handler(shield, **kwargs)
        else:
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                shield = ShieldManager(Path(tmp), make_sandbox_config()).shield
                kwargs = _extract_handler_kwargs(args, cmd_def)
                cmd_def.handler(shield, **kwargs)
    except ExecError as exc:
        print(
            f"Error: shield operation failed for task {task_id}: {exc}"
            if has_task
            else f"Error: shield operation failed: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    return True
