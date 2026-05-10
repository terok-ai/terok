# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for ``tests/unit/cli/`` test modules."""

from __future__ import annotations

from collections.abc import Callable


def which_factory(present: set[str]) -> Callable[[str], str | None]:
    """Build a ``shutil.which`` side-effect that finds only ``present`` names.

    Tests that need to assert on whether a binary is "on PATH" mock
    ``shutil.which`` with this; ``set()`` matches today's
    ``_which_nothing`` and any superset matches today's
    ``_which_everything``.
    """

    def _which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    return _which
