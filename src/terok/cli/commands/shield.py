# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shield egress firewall management commands.

Uses the ``terok_shield`` command registry to build subcommands.
Each command that ``needs_container`` resolves the project + task to
construct a per-task :class:`Shield` and calls the registry handler.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from terok_shield import COMMANDS, ArgDef, CommandDef, ExecError

from ...lib.facade import make_shield


def _add_project_task_args(parser: argparse.ArgumentParser) -> None:
    """Add --project and --task arguments to a subparser."""
    parser.add_argument("--project", "-p", required=True, help="Project ID")
    parser.add_argument("--task", "-t", required=True, help="Task ID")


def _add_arg(parser: argparse.ArgumentParser, arg: ArgDef) -> None:
    """Register an :class:`ArgDef` with an argparse parser.

    Local helper mirroring ``ArgDef.add_to()`` (proposed for terok-shield).
    """
    kwargs: dict = {}
    if arg.help:
        kwargs["help"] = arg.help
    for field in ("type", "default", "action", "dest", "nargs"):
        val = getattr(arg, field)
        if val is not None:
            kwargs[field] = val
    parser.add_argument(arg.name, **kwargs)


def _resolve_task(project_id: str, task_id: str) -> tuple[str, Path]:
    """Resolve project+task to (container_name, task_dir).

    Returns:
        Tuple of (container_name, task_dir) for constructing a Shield.

    Raises:
        ValueError: If the task has never been run (no container exists).
    """
    from ...lib.containers.runtime import container_name
    from ...lib.containers.tasks import load_task_meta
    from ...lib.core.projects import load_project

    project = load_project(project_id)
    meta, _ = load_task_meta(project.id, task_id)
    mode = meta.get("mode")
    if mode is None:
        raise ValueError(
            f"Task {task_id} in project {project_id!r} has never been run — no container exists"
        )
    cname = container_name(project.id, mode, task_id)
    task_dir = project.tasks_root / str(task_id)
    return cname, task_dir


def _extract_handler_kwargs(args: argparse.Namespace, cmd_def: CommandDef) -> dict:
    """Extract keyword arguments for a registry handler from parsed args."""
    kwargs: dict = {}
    for arg in cmd_def.args:
        key = arg.dest or arg.name.lstrip("-").replace("-", "_")
        if hasattr(args, key):
            kwargs[key] = getattr(args, key)
    return kwargs


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``shield`` subcommand group from the registry."""
    p = subparsers.add_parser("shield", help="Manage egress firewall (terok-shield)")
    sub = p.add_subparsers(dest="shield_cmd", required=True)

    for cmd in COMMANDS:
        if cmd.standalone_only:
            continue

        sp = sub.add_parser(cmd.name, help=cmd.help)

        if cmd.needs_container:
            _add_project_task_args(sp)

        for arg in cmd.args:
            _add_arg(sp, arg)


def dispatch(args: argparse.Namespace) -> bool:
    """Handle shield commands.  Returns True if handled."""
    if args.cmd != "shield":
        return False

    cmd_name = args.shield_cmd
    cmd_lookup = {cmd.name: cmd for cmd in COMMANDS if not cmd.standalone_only}
    cmd_def = cmd_lookup.get(cmd_name)
    if cmd_def is None or cmd_def.handler is None:
        return False

    try:
        if cmd_def.needs_container:
            cname, task_dir = _resolve_task(args.project, args.task)
            shield = make_shield(task_dir)
            kwargs = _extract_handler_kwargs(args, cmd_def)
            cmd_def.handler(shield, cname, **kwargs)
        else:
            # Non-container commands (status, profiles, preview) use the
            # registry handler directly.  For ``status`` this deliberately
            # shows *available* profiles (filesystem scan via Shield.status)
            # rather than *configured* profiles from the terok config.
            # The config-aware view lives in shield.status() (facade) and
            # is used by the TUI; the CLI shows runtime reality instead.
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                shield = make_shield(Path(tmp))
                kwargs = _extract_handler_kwargs(args, cmd_def)
                cmd_def.handler(shield, **kwargs)
    except ExecError as exc:
        if cmd_def.needs_container:
            print(f"Error: container for task {args.task} is not running", file=sys.stderr)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    return True
