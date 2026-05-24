# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Image management commands: build, list, cleanup, usage."""

from __future__ import annotations

import argparse

from ...lib.api import find_orphaned_images, list_images, remove_images, require_project_exists
from . import _storage_view
from ._completers import complete_project_ids as _complete_project_ids, set_completer


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``image`` subcommand group."""
    p_image = subparsers.add_parser("image", help="Manage terok container images")
    image_sub = p_image.add_subparsers(dest="image_cmd", required=True)

    # image build — L0+L1 build; defaults from config.image.* or a named project
    p_build = image_sub.add_parser(
        "build",
        help="Build the L0+L1 image chain (host-wide default, or a named project)",
        description=(
            "Build the L0+L1 images.  With no project arg, uses the agent set "
            "and base image declared in ~/.config/terok/config.yml "
            "(image.agents, image.base_image), falling back to ``all`` agents "
            "on ubuntu:24.04.  With a project arg, uses that project's "
            "configured base image + agent roster instead.  Use --rebuild to "
            "refresh agent versions; --full-rebuild also re-pulls the base OS "
            "and rebuilds L0 from scratch."
        ),
    )
    set_completer(
        p_build.add_argument(
            "project_id",
            nargs="?",
            default=None,
            help="Project whose base + agents to build for (optional)",
        ),
        _complete_project_ids,
    )
    p_build.add_argument(
        "--base",
        default=None,
        help="Override the configured base image for this build only.",
    )
    p_build.add_argument(
        "--agents",
        default=None,
        help='Comma-separated roster entries to install, or "all".  Overrides config.',
    )
    p_build.add_argument(
        "--family",
        default=None,
        help="Override package family (deb/rpm) for unknown bases.",
    )
    p_build.add_argument(
        "--rebuild",
        action="store_true",
        help="Cache-bust the agent install layers — refreshes agent versions.",
    )
    p_build.add_argument(
        "--full-rebuild",
        action="store_true",
        help="Force --no-cache --pull=always — re-pulls base OS, rebuilds L0+L1.",
    )
    p_build.add_argument(
        "--sidecar",
        action="store_true",
        help="Also build the sidecar L1 image (used by CodeRabbit).",
    )

    # image list
    p_list = image_sub.add_parser("list", help="List terok images with sizes")
    set_completer(
        p_list.add_argument("project_id", nargs="?", default=None, help="Filter by project"),
        _complete_project_ids,
    )

    # image cleanup
    p_cleanup = image_sub.add_parser("cleanup", help="Remove orphaned and dangling terok images")
    p_cleanup.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without removing",
    )
    p_cleanup.add_argument(
        "-y",
        "--yes",
        action="store_true",
        dest="assume_yes",
        help="Skip the confirmation prompt and remove immediately.",
    )

    # image usage — disk usage summary (was top-level `storage`)
    p_usage = image_sub.add_parser(
        "usage",
        help="Show storage usage summary (global and per-project)",
    )
    set_completer(
        p_usage.add_argument(
            "--project",
            default=None,
            help="Show detailed per-task breakdown for one project",
        ),
        _complete_project_ids,
    )
    p_usage.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Machine-readable JSON output",
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle image management commands.  Returns True if handled."""
    if args.cmd != "image":
        return False

    match args.image_cmd:
        case "build":
            _cmd_build(
                project_id=getattr(args, "project_id", None),
                base=getattr(args, "base", None),
                agents=getattr(args, "agents", None),
                family=getattr(args, "family", None),
                rebuild=getattr(args, "rebuild", False),
                full_rebuild=getattr(args, "full_rebuild", False),
                sidecar=getattr(args, "sidecar", False),
            )
        case "list":
            _cmd_list(getattr(args, "project_id", None))
        case "cleanup":
            _cmd_cleanup(
                dry_run=getattr(args, "dry_run", False),
                assume_yes=getattr(args, "assume_yes", False),
            )
        case "usage":
            _cmd_usage(
                project_id=getattr(args, "project", None),
                json_output=getattr(args, "json_output", False),
            )
        case _:  # pragma: no cover — required=True makes argparse enforce this
            return False
    return True


def _cmd_build(
    *,
    project_id: str | None,
    base: str | None,
    agents: str | None,
    family: str | None,
    rebuild: bool,
    full_rebuild: bool,
    sidecar: bool,
) -> None:
    """Build the L0+L1 images via the executor primitive.

    With *project_id* set, derives the base image, family, and agent roster
    from the project's config (CLI overrides still win for any of the three).
    Without it, falls back to the user's global ``image.*`` defaults.
    """
    from terok.lib.api.agents import (
        DEFAULT_BASE_IMAGE,
        AgentRoster,
        BuildError,
        ExecutorConfigView,
        ImageBuilder,
    )

    from ...lib.core.config import get_global_image_agents
    from ...lib.core.projects import load_project

    project = load_project(project_id) if project_id else None

    try:
        if project is not None:
            resolved_base = base or project.base_image or DEFAULT_BASE_IMAGE
            resolved_family = family or project.family
            resolved_agents = AgentRoster.parse_selection(agents or ",".join(project.agents))
        else:
            resolved_base = base or ExecutorConfigView.image_base_image() or DEFAULT_BASE_IMAGE
            resolved_family = family
            resolved_agents = AgentRoster.parse_selection(agents or get_global_image_agents())
        builder = ImageBuilder(resolved_base, family=resolved_family)
        images = builder.build_base(
            agents=resolved_agents,
            rebuild=rebuild,
            full_rebuild=full_rebuild,
            tag_as_default=project is None,
        )
    except (BuildError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"\nL0: {images.l0}")
    print(f"L1: {images.l1}")

    if sidecar:
        try:
            sidecar_tag = builder.build_sidecar(
                rebuild=rebuild,
                full_rebuild=full_rebuild,
            )
        except BuildError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"L1 (sidecar): {sidecar_tag}")


def _cmd_usage(*, project_id: str | None, json_output: bool) -> None:
    """Dispatch usage display to the appropriate render mode."""
    if project_id:
        _storage_view.cmd_detail(project_id, json_output=json_output)
    else:
        _storage_view.cmd_overview(json_output=json_output)


def _cmd_list(project_id: str | None) -> None:
    """List terok-managed images with sizes."""
    if project_id is not None:
        require_project_exists(project_id)
    images = list_images(project_id)
    if not images:
        scope = f" for project '{project_id}'" if project_id else ""
        print(f"No terok images found{scope}.")
        return

    # Column widths
    name_w = max(len(img.full_name) for img in images)
    size_w = max(len(img.size) for img in images)
    header_name = "IMAGE"
    header_size = "SIZE"
    header_created = "CREATED"
    name_w = max(name_w, len(header_name))
    size_w = max(size_w, len(header_size))

    print(f"{header_name:<{name_w}}  {header_size:>{size_w}}  {header_created}")
    for img in images:
        print(f"{img.full_name:<{name_w}}  {img.size:>{size_w}}  {img.created}")

    print(f"\n{len(images)} image(s)")


def _cmd_cleanup(*, dry_run: bool, assume_yes: bool) -> None:
    """List orphaned terok images and ask before removing them.

    ``--dry-run`` lists only and never prompts.  ``--yes`` (``-y``) skips the
    prompt for non-interactive use.  Default: list, then confirm.
    """
    orphaned = find_orphaned_images()
    if not orphaned:
        print("No orphaned terok images found.")
        return

    for img in orphaned:
        print(f"  {img.full_name}")

    if dry_run:
        print(f"\n{len(orphaned)} image(s) would be removed.")
        return

    if not assume_yes:
        try:
            answer = input(f"\nRemove {len(orphaned)} image(s)? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return

    result = remove_images(orphaned)
    for name in result.removed:
        print(f"  Removed: {name}")
    for name in result.failed:
        print(f"  Failed: {name}")

    msg = f"\n{len(result.removed)} image(s) removed."
    if result.failed:
        msg += f" {len(result.failed)} failed (may be in use)."
    print(msg)
