# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``project`` subcommand group — all per-project operations."""

from __future__ import annotations

import argparse
from typing import Any

from ...lib.api import (
    build_images,
    delete_project,
    derive_project,
    find_projects_sharing_gate,
    generate_dockerfiles,
    get_project,
    set_project_image_agents,
    summarize_ssh_init,
)
from ...lib.core.projects import list_projects, load_project, normalize_project_name
from ...lib.domain.project import make_git_gate
from ...lib.domain.wizards.new_project import offer_edit_then_init, run_wizard
from ._completers import complete_project_names as _complete_project_names, set_completer
from .setup import cmd_project_init


def _add_project_arg(parser: argparse.ArgumentParser, **kwargs: Any) -> None:
    """Add a ``project_name`` positional with project name completion."""
    set_completer(parser.add_argument("project_name", **kwargs), _complete_project_names)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``project`` subcommand group."""
    p = subparsers.add_parser("project", help="Create, configure, and manage projects")
    sub = p.add_subparsers(dest="project_cmd", required=True)

    # list
    sub.add_parser("list", help="List all known projects")

    # wizard
    sub.add_parser(
        "wizard",
        help="Interactive wizard to create a new project configuration",
    )

    # derive
    p_derive = sub.add_parser(
        "derive",
        help="Create a new project derived from an existing one (shared infra, fresh agent config)",
    )
    set_completer(
        p_derive.add_argument("source_id", help="Source project name to derive from"),
        _complete_project_names,
    )
    p_derive.add_argument("new_id", help="New project name")

    # normalize-name
    p_normalize = sub.add_parser(
        "normalize-name",
        help="Repair project.yml so project.name matches the project directory name",
    )
    _add_project_arg(
        p_normalize,
        help=(
            "Project directory name to write into project.yml "
            "(works even when that project is currently broken)"
        ),
    )

    # delete
    p_delete = sub.add_parser(
        "delete",
        help="Delete a project and all its associated data (non-recoverable)",
    )
    _add_project_arg(p_delete, help="Project name to delete")
    p_delete.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # init — full setup
    p_init = sub.add_parser(
        "init",
        help="Full project setup: ssh-init + generate + build + gate-sync",
    )
    _add_project_arg(p_init)

    # generate
    p_gen = sub.add_parser("generate", help="Generate Dockerfiles for a project")
    _add_project_arg(p_gen)

    # build
    p_build = sub.add_parser("build", help="Build images for a project")
    _add_project_arg(p_build)
    p_build.add_argument(
        "--refresh-agents",
        dest="refresh_agents",
        action="store_true",
        help="Rebuild from L0 with fresh agent installs (cache bust)",
    )
    p_build.add_argument(
        "--agents",
        dest="agents",
        default=None,
        metavar="LIST",
        help=(
            'Comma-separated roster entries to install in L1, or "all". '
            "Overrides the project's image.agents for this build only."
        ),
    )
    p_build.add_argument(
        "--full-rebuild",
        action="store_true",
        help="Rebuild from L0 (no cache) (includes base image pull and apt packages)",
    )
    p_build.add_argument(
        "--dev",
        action="store_true",
        help="Also build a manual dev image from L0 (tagged as <project>:l2-dev)",
    )

    # ssh-init
    p_ssh = sub.add_parser(
        "ssh-init",
        help="Generate a vault-managed SSH keypair for a project",
    )
    _add_project_arg(p_ssh)
    p_ssh.add_argument(
        "--key-type",
        choices=["ed25519", "rsa"],
        default="ed25519",
        help="Key algorithm (default: ed25519)",
    )
    p_ssh.add_argument(
        "--comment",
        default=None,
        help=(
            "Comment embedded in the public key "
            "(default: tk-main:<project> for the project's first key, "
            "tk-side:<project>:<n> for subsequent additive inits)"
        ),
    )
    p_ssh.add_argument(
        "--force",
        action="store_true",
        help="Rotate — unassign existing keys from the project and generate fresh",
    )

    # gate-path
    p_gate_path = sub.add_parser(
        "gate-path",
        help=(
            "Print the project's host-side git gate mirror as a file:// URL "
            "(suitable for IDEs and host-side git tools)"
        ),
    )
    _add_project_arg(p_gate_path)

    # gate-sync
    p_gate = sub.add_parser(
        "gate-sync",
        help=(
            "Sync the host-side git gate for a project (creates it if missing). "
            "For SSH upstreams this uses ONLY the vault-managed key from "
            "'project ssh-init' — never the user's ~/.ssh — unless "
            "--use-personal-ssh is passed."
        ),
    )
    _add_project_arg(p_gate)
    p_gate.add_argument(
        "--force-reinit",
        dest="force_reinit",
        action="store_true",
        help="Recreate the mirror from scratch",
    )
    p_gate.add_argument(
        "--use-personal-ssh",
        dest="use_personal_ssh",
        action="store_true",
        help="Fall through to the user's ~/.ssh keys instead of the vault",
    )

    # agents — subgroup mirroring `terok agents` but scoped per-project
    p_agents = sub.add_parser(
        "agents",
        help="Inspect or set the per-project image.agents override",
    )
    agents_sub = p_agents.add_subparsers(dest="agents_cmd", required=True)
    p_agents_set = agents_sub.add_parser(
        "set",
        help="Write image.agents in the project's project.yml (interactive when no arg)",
        description=(
            "Set the agent selection baked into this project's L1 image, "
            "overriding the global default.  Validated against the installed "
            "roster.  Interactive picker when SELECTION is omitted."
        ),
    )
    _add_project_arg(p_agents_set)
    p_agents_set.add_argument(
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
    """Handle the ``project`` group.  Returns True if handled."""
    if args.cmd != "project":
        return False
    match args.project_cmd:
        case "list":
            _cmd_project_list()
        case "wizard":
            run_wizard(init_fn=cmd_project_init)
        case "derive":
            _cmd_project_derive(args.source_id, args.new_id)
        case "normalize-name":
            _cmd_project_normalize_name(args.project_name)
        case "delete":
            _cmd_project_delete(args.project_name, force=args.force)
        case "init":
            cmd_project_init(args.project_name)
        case "generate":
            generate_dockerfiles(args.project_name)
        case "build":
            build_images(
                args.project_name,
                include_dev=getattr(args, "dev", False),
                refresh_agents=getattr(args, "refresh_agents", False),
                full_rebuild=getattr(args, "full_rebuild", False),
                agents=getattr(args, "agents", None),
            )
        case "ssh-init":
            _cmd_ssh_init(args)
        case "gate-path":
            _cmd_gate_path(args.project_name)
        case "gate-sync":
            _cmd_gate_sync(args)
        case "agents":
            if args.agents_cmd == "set":
                _cmd_agents_set(args.project_name, getattr(args, "selection", None))
        case _:  # pragma: no cover — required=True makes argparse enforce this
            return False
    return True


# ── Handlers ───────────────────────────────────────────────────────────


def _cmd_project_list() -> None:
    """List all known projects."""
    projs = list_projects()
    if not projs:
        print("No projects found")
        return
    print("Known projects:")
    for p in projs:
        upstream = p.upstream_url or "-"
        shared = f" shared={p.shared_dir}" if p.shared_dir else ""
        print(f"- {p.name} [{p.security_class}] upstream={upstream}{shared} config_root={p.root}")
        if p.description:
            print(f"    {p.description}")


def _cmd_project_derive(source_id: str, new_id: str) -> None:
    """Derive a new project from an existing one."""
    project = derive_project(source_id, new_id)
    config_path = project.config.root / "project.yml"
    print(
        f"Derived project '{new_id}' from '{source_id}' — "
        f"shares git gate and SSH key with source.\n"
        f"Config: {config_path}"
    )
    offer_edit_then_init(config_path, new_id, init_fn=cmd_project_init)


def _cmd_project_normalize_name(project_name: str) -> None:
    """Rewrite project.yml so ``project.name`` matches *project_name*."""
    path = normalize_project_name(project_name)
    print(f"Updated {path}: project.name = {project_name!r}")


def _cmd_project_delete(project_name: str, *, force: bool = False) -> None:
    """Delete a project after confirmation (unless --force)."""
    project = load_project(project_name)
    pid = project.name

    print(f"Project: {pid}")
    print(f"  Config root: {project.root}")
    print(f"  Security class: {project.security_class}")
    if project.upstream_url:
        print(f"  Upstream: {project.upstream_url}")

    sharing = find_projects_sharing_gate(project.gate_path, exclude_project=pid)
    if sharing:
        names = ", ".join(p for p, _ in sharing)
        print(f"\n  Note: gate is shared with: {names} (will NOT be deleted)")

    from ...lib.core.config import archive_dir as _archive_dir

    archive_path = _archive_dir()
    print("\nWARNING: All project data will be permanently deleted.")
    print("Project config, task data, and build artifacts will be archived at:")
    print(f"{archive_path}")

    if not force:
        try:
            answer = input(f"\nType '{pid}' to confirm deletion: ").strip()
        except EOFError:
            print("Deletion cancelled (no interactive stdin). Use --force to skip confirmation.")
            return
        if answer != pid:
            print("Deletion cancelled.")
            return

    result = delete_project(pid)

    print(f"\nProject '{pid}' deleted.")
    if result.get("archive"):
        print(f"Archive: {result['archive']}")
    if result["deleted"]:
        print("Removed:")
        for path in result["deleted"]:
            print(f"  - {path}")
    if result["skipped"]:
        print("Skipped:")
        for reason in result["skipped"]:
            print(f"  - {reason}")


def _cmd_ssh_init(args: argparse.Namespace) -> None:
    """Provision a vault-managed SSH keypair for the project."""
    result = get_project(args.project_name).provision_ssh_key(
        key_type=getattr(args, "key_type", "ed25519"),
        comment=getattr(args, "comment", None),
        force=getattr(args, "force", False),
    )
    summarize_ssh_init(result)


def _cmd_gate_path(project_name: str) -> None:
    """Print the project's host-side git gate mirror as a ``file://`` URL.

    Plain stdout so callers can paste the URL into an IDE or a host-side
    git remote.  The gate's bare mirror lives under ``cfg.gate_base_path``;
    a ``file://`` URL reaches it from any tool running on the same host::

        # On the host running terok:
        $ terok project gate-path myproj
        file:///home/you/.local/share/terok/core/gate/myproj.git

        # Then, on the same host:
        $ git remote add spark file:///home/you/.local/share/terok/core/gate/myproj.git

    The path is computed deterministically from project config — the URL
    is printed even if the bare repo doesn't exist yet (run ``gate-sync``
    to create it).
    """
    print(load_project(project_name).gate_path.as_uri())


def _cmd_gate_sync(args: argparse.Namespace) -> None:
    """Sync the host-side git gate for a project."""
    from terok.lib.api.gate import GateAuthNotConfigured

    project = load_project(args.project_name)
    if not project.gate_enabled:
        raise SystemExit(
            f"Project {project.name!r} has gate.enabled: false — refusing to sync.\n"
            "Either set gate.enabled: true in project.yml, or drop the "
            "gate-sync step entirely (the container clones directly from "
            "upstream, if any)."
        )

    use_personal = bool(getattr(args, "use_personal_ssh", False)) or None
    try:
        res = make_git_gate(project, use_personal_ssh=use_personal).sync(
            force_reinit=getattr(args, "force_reinit", False)
        )
    except GateAuthNotConfigured as exc:
        raise SystemExit(
            f"{exc}\n\nEither:\n"
            f"  * terok project ssh-init {args.project_name}  "
            "(generate a key, then register it with the remote), or\n"
            "  * pass --use-personal-ssh to fall through to ~/.ssh."
        ) from exc
    if not res["success"]:
        raise SystemExit(f"Gate sync failed: {', '.join(res['errors'])}")
    cache_note = " (clone cache refreshed)" if res.get("cache_refreshed") else ""
    upstream_label = res["upstream_url"] or "(none — local-only bare repo)"
    print(
        f"Gate ready at {res['path']} "
        f"(upstream: {upstream_label}; created: {res['created']}){cache_note}"
    )
    if res.get("cache_error"):
        print(f"Warning: clone cache refresh failed: {res['cache_error']}")
        print("New tasks fall back to a full clone until the next successful sync.")


def _cmd_agents_set(project_name: str, selection: str | None) -> None:
    """Validate *selection* and write it to the project's ``image.agents``."""
    from terok.lib.api.agents import AgentRoster

    roster = AgentRoster.shared()
    raw = selection if selection is not None else roster.prompt_selection()
    roster.validate_selection(raw)
    path = set_project_image_agents(project_name, raw)
    print(f"Wrote image.agents = {raw!r} to {path}")
