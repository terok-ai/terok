# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared test constants: filesystem paths.

Matches terok-shield's ``testfs.py`` convention by keeping test-owned path
fragments and synthetic filesystem locations in one place.
"""

from pathlib import Path

# ── Placeholder directories used in mocked tests ─────────────────────────────

MOCK_BASE = Path("/tmp/terok-testing")
"""Root for synthetic filesystem paths used by mocked tests."""

MOCK_TASK_DIR = MOCK_BASE / "tasks" / "42"
"""Fake per-task directory used by shield adapter tests."""

MOCK_TASK_DIR_1 = MOCK_BASE / "tasks" / "1"
"""Alternate fake per-task directory used by CLI shield tests."""

MOCK_CONFIG_ROOT = Path("/home/user/.config/terok")
"""Fake XDG-style config root used by path-related tests."""

# ── Well-known integration environment path fragments ────────────────────────

HOME_DIR_NAME = "home"
"""Temporary HOME directory name used by integration fixtures."""

XDG_CONFIG_HOME_NAME = "xdg-config"
"""Temporary XDG config root name used by integration fixtures."""

CONFIG_ROOT_NAME = "config"
"""Temporary terok system-config root name used by integration fixtures."""

STATE_ROOT_NAME = "state"
"""Temporary terok state root name used by integration fixtures."""
