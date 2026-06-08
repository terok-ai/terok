# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task name sanitization, validation, and random-name generation."""

import re

from ...core.projects import load_project

TASK_NAME_MAX_LEN = 60
"""Maximum length of a sanitized task name."""


def sanitize_task_name(raw: str | None) -> str | None:
    """Sanitize a raw task name into a slug-style identifier.

    Strips whitespace, lowercases, replaces spaces with hyphens,
    removes characters outside ``[a-z0-9_-]``, collapses consecutive
    hyphens, strips trailing hyphens, and truncates to
    ``TASK_NAME_MAX_LEN``.  Returns ``None`` if the result is empty.

    Leading hyphens are preserved so callers can detect and reject them
    (a name starting with ``-`` looks like a CLI flag).
    """
    if raw is None:
        return None
    name = raw.strip().lower()
    name = name.replace(" ", "-")
    name = re.sub(r"[^a-z0-9_-]", "", name)
    name = re.sub(r"-{2,}", "-", name)
    name = name.rstrip("-")
    name = name[:TASK_NAME_MAX_LEN]
    return name or None


def validate_task_name(sanitized: str) -> str | None:
    """Return an error message if *sanitized* is not a valid task name, else ``None``.

    A name is invalid if it starts with a hyphen (looks like a CLI flag).
    Callers should first check for ``None`` from [`sanitize_task_name`][terok.lib.orchestration.tasks.sanitize_task_name]
    (which indicates the name was empty after sanitization).
    """
    if sanitized.startswith("-"):
        return "name must not start with a hyphen"
    return None


def generate_task_name(project_name: str | None = None) -> str:
    """Generate a random human-readable task name (e.g. ``talented-toucan``).

    When *project_name* is given, name categories are resolved from config:
    project ``tasks.name_categories`` → global ``tasks.name_categories``
    → deterministic 3-category selection based on project name hash.
    """
    import namer

    categories = _resolve_name_categories(project_name) if project_name else None
    return namer.generate(separator="-", category=categories)


def _resolve_name_categories(project_name: str) -> list[str] | None:
    """Resolve task-name categories: project config → global config → hash default."""
    from ...core.config import get_task_name_categories

    # 1. Per-project override
    try:
        project = load_project(project_name)
        if project.task_name_categories:
            return project.task_name_categories
    except SystemExit:
        pass

    # 2. Global config
    global_cats = get_task_name_categories()
    if global_cats:
        return global_cats

    # 3. Hash-based default: pick 3 categories deterministically from project name
    return _default_categories_for_project(project_name)


def _default_categories_for_project(project_name: str) -> list[str]:
    """Pick 3 categories deterministically based on a hash of the project name."""
    import hashlib
    import random

    import namer

    categories = sorted(namer.list_categories())
    seed = int(hashlib.md5(project_name.encode(), usedforsecurity=False).hexdigest(), 16)
    rng = random.Random(seed)
    return rng.sample(categories, min(3, len(categories)))


__all__ = [
    "TASK_NAME_MAX_LEN",
    "generate_task_name",
    "sanitize_task_name",
    "validate_task_name",
]
