# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Image management commands: list and cleanup."""

from __future__ import annotations

import argparse

from ...lib.facade import cleanup_images, list_images
from ._completers import complete_project_ids as _complete_project_ids, set_completer


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``image`` subcommand group."""
    p_image = subparsers.add_parser("image", help="Manage terok container images")
    image_sub = p_image.add_subparsers(dest="image_cmd", required=True)

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


def dispatch(args: argparse.Namespace) -> bool:
    """Handle image management commands.  Returns True if handled."""
    if args.cmd != "image":
        return False

    if args.image_cmd == "list":
        _cmd_list(getattr(args, "project_id", None))
        return True
    if args.image_cmd == "cleanup":
        _cmd_cleanup(dry_run=getattr(args, "dry_run", False))
        return True
    return False


def _cmd_list(project_id: str | None) -> None:
    """List terok-managed images with sizes."""
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


def _cmd_cleanup(dry_run: bool) -> None:
    """Remove orphaned terok images."""
    result = cleanup_images(dry_run=dry_run)

    if not result.removed and not result.failed:
        print("No orphaned terok images found.")
        return

    label = "Would remove" if dry_run else "Removed"
    for name in result.removed:
        print(f"  {label}: {name}")

    if result.failed:
        for name in result.failed:
            print(f"  Failed: {name}")

    if dry_run:
        print(f"\n{len(result.removed)} image(s) would be removed.")
    else:
        removed_count = len(result.removed)
        failed_count = len(result.failed)
        msg = f"\n{removed_count} image(s) removed."
        if failed_count:
            msg += f" {failed_count} failed (may be in use)."
        print(msg)
