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
        value = "disengaged" if kwargs.get("allow_all") else "down"
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
