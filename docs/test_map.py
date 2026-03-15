#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Generate a Markdown map of integration tests from pytest collection."""

from __future__ import annotations

import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DIR = ROOT / "tests" / "integration"
_VENV_BIN = Path(sys.executable).parent

_DIR_ORDER = [
    "cli",
    "projects",
    "tasks",
    "setup",
    "gate",
    "launch",
]


def collect_tests() -> list[str]:
    """Return collected integration test node IDs."""
    result = subprocess.run(
        [
            str(_VENV_BIN / "pytest"),
            "--collect-only",
            "-qq",
            "-p",
            "no:tach",
            str(INTEGRATION_DIR),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        msg = (result.stdout + result.stderr).strip()
        raise RuntimeError(f"pytest collection failed (exit {result.returncode}):\n{msg}")
    return [line.strip() for line in result.stdout.splitlines() if "::" in line]


def _group_by_directory(test_ids: list[str]) -> dict[str, list[str]]:
    """Group collected node IDs by integration subdirectory."""
    groups: dict[str, list[str]] = defaultdict(list)
    for test_id in test_ids:
        rel_path = test_id.split("::", 1)[0].removeprefix("tests/integration/")
        subdir = rel_path.split("/", 1)[0] if "/" in rel_path else "(root)"
        groups[subdir].append(test_id)
    return groups


def _sorted_dirs(groups: dict[str, list[str]]) -> list[str]:
    """Return known directories first and append unknown ones alphabetically."""
    known = [name for name in _DIR_ORDER if name in groups]
    unknown = sorted(name for name in groups if name not in _DIR_ORDER)
    return known + unknown


def _dir_description(subdir: str) -> str:
    """Return the README summary for *subdir*, if present."""
    readme = INTEGRATION_DIR / subdir / "README.md"
    if not readme.is_file():
        return ""
    lines = [line.strip() for line in readme.read_text(encoding="utf-8").splitlines()]
    return " ".join(line for line in lines[1:] if line)


def _format_test_row(test_id: str) -> str:
    """Return a Markdown table row for a collected node ID."""
    parts = test_id.split("::")
    file_path = parts[0]
    class_name = parts[1] if len(parts) > 2 else ""
    test_name = parts[-1]
    return f"| `{test_name}` | `{class_name}` | `{file_path}` |"


def generate_test_map(test_ids: list[str] | None = None) -> str:
    """Return the integration test map as Markdown."""
    if test_ids is None:
        test_ids = collect_tests()

    groups = _group_by_directory(test_ids)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Integration Test Map\n\n",
        f"*Generated: {now}*\n\n",
        f"**{len(test_ids)} tests** across **{len(groups)} directories**\n\n",
    ]
    for subdir in _sorted_dirs(groups):
        lines.append(f"## `{subdir}/`\n\n")
        description = _dir_description(subdir)
        if description:
            lines.append(f"{description}\n\n")
        lines.append("| Test | Class | File |\n")
        lines.append("|---|---|---|\n")
        for test_id in sorted(groups[subdir]):
            lines.append(_format_test_row(test_id) + "\n")
        lines.append("\n")
    return "".join(lines)


if __name__ == "__main__":
    output = generate_test_map()
    out_path = ROOT / "docs" / "test_map.md"
    out_path.write_text(output, encoding="utf-8")
    print(f"Wrote {out_path}")
