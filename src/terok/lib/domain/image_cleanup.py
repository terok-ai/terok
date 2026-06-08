# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Image listing and cleanup for terok-managed container images."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from terok.lib.integrations.sandbox import PodmanRuntime

from ..core.projects import list_projects

# Image-management ops are runtime-agnostic — podman's image store is the
# same regardless of which OCI runtime ends up booting any given image —
# so every call here uses plain ``PodmanRuntime``.  Constructor is cheap;
# we instantiate per-call so test fixtures can patch ``PodmanRuntime``
# without having to chase a module-level singleton.

if TYPE_CHECKING:
    from collections.abc import Iterable

    from terok.lib.integrations.sandbox import Image


@dataclass
class ImageInfo:
    """A single container image with its metadata."""

    repository: str
    tag: str
    image_id: str
    size: str
    created: str

    @property
    def full_name(self) -> str:
        """Return ``repository:tag`` or ``<none> (<short-id>)`` for dangling images."""
        if self.repository == "<none>" and self.tag == "<none>":
            return f"<none> ({self.image_id[:12]})"
        return f"{self.repository}:{self.tag}"

    @property
    def project_key(self) -> str:
        """Normalised repository, stripped of podman's ``localhost/`` prefix.

        Podman labels every locally-built image with a ``localhost/`` registry
        prefix.  terok's classification (global vs L2) and project lookups key
        off bare project names, so callers compare against this property rather
        than the raw [`repository`][terok.lib.domain.image_cleanup.ImageInfo.repository] string.
        """
        return self.repository.removeprefix("localhost/")

    @classmethod
    def from_image(cls, image: Image) -> ImageInfo:
        """Lift a sandbox `Image` handle into terok's display type."""
        return cls(
            repository=image.repository,
            tag=image.tag,
            image_id=image.ref,
            size=image.size,
            created=image.created,
        )


@dataclass
class CleanupResult:
    """Summary of an image cleanup operation."""

    removed: list[str]
    failed: list[str]
    dry_run: bool


def _known_project_names() -> set[str] | None:
    """Return the set of currently configured project names, or None on failure.

    Returning None (rather than an empty set) lets callers distinguish
    "no projects configured" from "project discovery failed", preventing
    accidental deletion of valid L2 images.
    """
    try:
        return {p.name for p in list_projects()}
    except Exception as exc:
        from ..util.logging_utils import log_warning

        log_warning(f"Project discovery failed during image cleanup: {exc}. Skipping cleanup.")
        return None


def _terok_image_prefixes() -> tuple[str, ...]:
    """Return repository prefixes that identify terok-managed images."""
    return ("terok-l0", "terok-l1-cli")


def _is_terok_l2_image(repo: str, tag: str) -> bool:
    """Return True if the image looks like a terok L2 project image."""
    return tag in ("l2-cli", "l2-dev")


def _is_terok_image(repo: str, tag: str) -> bool:
    """Return True if the image is a terok-managed image (any layer)."""
    if repo.startswith(_terok_image_prefixes()):
        return True
    return _is_terok_l2_image(repo, tag)


def list_images(project_name: str | None = None) -> list[ImageInfo]:
    """List terok-managed images, optionally filtered by project.

    Args:
        project_name: If given, only show images for this project.

    Returns:
        List of ImageInfo objects for matching images.
    """
    images: list[ImageInfo] = []
    for image in PodmanRuntime().images():
        repo = image.repository.removeprefix("localhost/")
        if not _is_terok_image(repo, image.tag):
            continue
        if project_name is not None:
            # Filter: L2 images must match the project; L0/L1 always shown
            if _is_terok_l2_image(repo, image.tag) and repo != project_name:
                continue
        images.append(ImageInfo.from_image(image))
    return images


def find_orphaned_images() -> list[ImageInfo]:
    """Find terok images that are orphaned and safe to remove.

    Orphaned images include:
    - Dangling images (``<none>:<none>``) from terok layer rebuilds
    - L2 project images whose project no longer exists in the config
    """
    known_ids = _known_project_names()

    # Dangling images that descended from terok base layers
    dangling = _find_dangling_terok_images()

    # L2 images for projects that no longer exist (skip if discovery failed)
    orphaned_l2: list[ImageInfo] = []
    if known_ids is not None:
        all_images = list_images()
        orphaned_l2 = [
            img
            for img in all_images
            if _is_terok_l2_image(img.repository, img.tag)
            and img.project_key not in known_ids
            and _is_terok_built_image(img.image_id)
        ]

    # Combine, dedup by image ID
    seen_ids: set[str] = set()
    result: list[ImageInfo] = []
    for img in [*dangling, *orphaned_l2]:
        if img.image_id not in seen_ids:
            seen_ids.add(img.image_id)
            result.append(img)
    return result


def _find_dangling_terok_images() -> list[ImageInfo]:
    """Find dangling (untagged) images that were built by terok.

    Walks the runtime's ``images(dangling_only=True)`` enumeration and
    keeps only images whose ancestry matches `_is_terok_built_image`
    (build-context-hash label or terok layer name in history).
    """
    return [
        ImageInfo.from_image(image)
        for image in PodmanRuntime().images(dangling_only=True)
        if _is_terok_built_image(image.ref)
    ]


def _is_terok_built_image(image_id: str) -> bool:
    """Check if an image originated from a terok build.

    Inspects the ``terok.build_context_hash`` label and image history
    for terok layer names.
    """
    image = PodmanRuntime().image(image_id)
    if image.labels().get("terok.build_context_hash"):
        return True
    return any("terok-l0" in line or "terok-l1" in line for line in image.history())


def remove_images(images: Iterable[ImageInfo], *, dry_run: bool = False) -> CleanupResult:
    """Remove a pre-computed set of images (or just report under *dry_run*).

    Split out from [`cleanup_images`][terok.lib.domain.image_cleanup.cleanup_images]
    so the CLI can present the orphan list, prompt for confirmation, and only
    then act — without paying the discovery cost twice.

    Args:
        images: The images to remove.  Iterated once.
        dry_run: If True, only report names without invoking the runtime.

    Returns:
        CleanupResult with lists of removed and failed image display names.
    """
    removed: list[str] = []
    failed: list[str] = []
    for img in images:
        if dry_run:
            removed.append(img.full_name)
            continue
        try:
            if PodmanRuntime().image(img.image_id).remove():
                removed.append(img.full_name)
            else:
                failed.append(img.full_name)
        except Exception as exc:  # noqa: BLE001 — one bad image shouldn't abort the sweep
            from ..util.logging_utils import log_warning

            log_warning(f"Image cleanup failed for {img.full_name}: {exc}")
            failed.append(img.full_name)
    return CleanupResult(removed=removed, failed=failed, dry_run=dry_run)


def cleanup_images(dry_run: bool = False) -> CleanupResult:
    """Find and remove orphaned terok images in one shot.

    Thin convenience over [`find_orphaned_images`][terok.lib.domain.image_cleanup.find_orphaned_images]
    + [`remove_images`][terok.lib.domain.image_cleanup.remove_images] for callers that
    don't need to inspect the list first.

    Args:
        dry_run: If True, only report what would be removed without removing.

    Returns:
        CleanupResult with lists of removed and failed image display names.
    """
    return remove_images(find_orphaned_images(), dry_run=dry_run)
