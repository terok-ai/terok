# SPDX-FileCopyrightText: 2026 terok contributors
# SPDX-License-Identifier: Apache-2.0

"""Shield firewall management commands: setup, status, allow, deny, rules, logs."""

from __future__ import annotations

import argparse
import json
import sys

from ...lib.security.shield import (
    list_log_files,
    shield_allow_domain,
    shield_deny_domain,
    shield_rules,
    shield_setup,
    shield_status,
    tail_log,
)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``shield`` subcommand group."""
    p = subparsers.add_parser("shield", help="Manage container network firewall")
    sub = p.add_subparsers(dest="shield_cmd", required=True)

    p_setup = sub.add_parser("setup", help="Install shield hook or verify bridge")
    p_setup.add_argument(
        "--hardened",
        action="store_true",
        default=False,
        help="Use hardened mode (bridge network + rootless-netns)",
    )

    sub.add_parser("status", help="Show shield status")

    p_allow = sub.add_parser("allow", help="Live-allow a domain or IP for a task")
    p_allow.add_argument("project", help="Project ID")
    p_allow.add_argument("task", help="Task ID")
    p_allow.add_argument("target", help="Domain name or IP address to allow")

    p_deny = sub.add_parser("deny", help="Live-deny a domain or IP for a task")
    p_deny.add_argument("project", help="Project ID")
    p_deny.add_argument("task", help="Task ID")
    p_deny.add_argument("target", help="Domain name or IP address to deny")

    p_rules = sub.add_parser("rules", help="Show current nft rules for a task")
    p_rules.add_argument("project", help="Project ID")
    p_rules.add_argument("task", help="Task ID")

    p_logs = sub.add_parser("logs", help="Show audit log")
    p_logs.add_argument("--container", default=None, help="Filter by container name")
    p_logs.add_argument("-n", type=int, default=50, help="Number of recent entries")


def dispatch(args: argparse.Namespace) -> bool:
    """Handle shield commands.  Returns True if handled."""
    if args.cmd != "shield":
        return False

    cmd = args.shield_cmd
    if cmd == "setup":
        _cmd_setup(hardened=args.hardened)
    elif cmd == "status":
        _cmd_status()
    elif cmd == "allow":
        _cmd_allow(args.project, args.task, args.target)
    elif cmd == "deny":
        _cmd_deny(args.project, args.task, args.target)
    elif cmd == "rules":
        _cmd_rules(args.project, args.task)
    elif cmd == "logs":
        _cmd_logs(container=args.container, n=args.n)
    else:
        return False
    return True


def _cmd_setup(hardened: bool) -> None:
    """Run shield setup."""
    shield_setup(hardened=hardened)
    mode = "hardened" if hardened else "standard"
    print(f"Shield setup complete (mode: {mode}).")
    print("Containers will now start with network firewall enabled.")


def _cmd_status() -> None:
    """Show shield status."""
    status = shield_status()
    print(f"Mode:     {status['mode']}")
    print(f"Audit:    {'enabled' if status['audit_enabled'] else 'disabled'}")
    print(f"Profiles: {', '.join(status['profiles']) or '(none)'}")
    if status["log_files"]:
        print(f"Logs:     {len(status['log_files'])} container(s)")


def _cmd_allow(project_id: str, task_id: str, target: str) -> None:
    """Live-allow a domain or IP."""
    from ...lib.containers.runtime import container_name

    # Try all container modes
    for mode in ("cli", "web", "run"):
        cname = container_name(project_id, mode, task_id)
        try:
            ips = shield_allow_domain(cname, target)
            print(f"Allowed {target} -> {', '.join(ips) or '(unresolvable)'} for {cname}")
            return
        except Exception:
            continue
    print(f"Error: no running container found for {project_id}/{task_id}", file=sys.stderr)
    sys.exit(1)


def _cmd_deny(project_id: str, task_id: str, target: str) -> None:
    """Live-deny a domain or IP."""
    from ...lib.containers.runtime import container_name

    for mode in ("cli", "web", "run"):
        cname = container_name(project_id, mode, task_id)
        try:
            ips = shield_deny_domain(cname, target)
            print(f"Denied {target} ({', '.join(ips) or '(unresolvable)'}) for {cname}")
            return
        except Exception:
            continue
    print(f"Error: no running container found for {project_id}/{task_id}", file=sys.stderr)
    sys.exit(1)


def _cmd_rules(project_id: str, task_id: str) -> None:
    """Show current nft rules for a task."""
    from ...lib.containers.runtime import container_name

    for mode in ("cli", "web", "run"):
        cname = container_name(project_id, mode, task_id)
        rules = shield_rules(cname)
        if rules.strip():
            print(rules)
            return
    print(f"No rules found for {project_id}/{task_id}")


def _cmd_logs(container: str | None, n: int) -> None:
    """Show audit log entries."""
    if container:
        for entry in tail_log(container, n):
            print(json.dumps(entry))
    else:
        files = list_log_files()
        if not files:
            print("No audit logs found.")
            return
        for ctr in files:
            for entry in tail_log(ctr, n):
                print(json.dumps(entry))
