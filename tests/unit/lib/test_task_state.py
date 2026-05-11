# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`terok.lib.core.task_state.has_gpu`][terok.lib.core.task_state.has_gpu].

Other domain helpers from this module (``TaskState``, ``effective_status``,
``container_name``, ``CONTAINER_MODES``) are exercised by
``test_effective_status`` and ``test_task_ids`` — this file only covers the
GPU opt-in detector, whose branches were unreachable through the other suites.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from terok.lib.core.task_state import has_gpu


def _project(root: Path | None) -> Any:
    """Build a stand-in for a ``Project`` carrying just ``root``."""
    return SimpleNamespace(root=root)


def _write_project_yml(root: Path, body: str) -> None:
    """Materialise a minimal ``project.yml`` for ``has_gpu`` to read."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.yml").write_text(body)


class TestHasGpu:
    """Map every ``run.gpus`` shape to the expected boolean verdict."""

    def test_returns_false_without_root_attr(self) -> None:
        """An object without a usable ``root`` attribute never opts into GPU."""
        assert has_gpu(SimpleNamespace()) is False
        assert has_gpu(_project(root=None)) is False

    def test_returns_false_when_project_yml_missing(self, tmp_path: Path) -> None:
        """Missing ``project.yml`` — treat as no GPU rather than raising."""
        # tmp_path exists but no project.yml inside
        assert has_gpu(_project(root=tmp_path)) is False

    def test_returns_false_on_malformed_yaml(self, tmp_path: Path) -> None:
        """Parse errors are swallowed — GPU stays off."""
        _write_project_yml(tmp_path, "run: [not-a-mapping")
        assert has_gpu(_project(root=tmp_path)) is False

    def test_returns_true_when_gpus_string_all(self, tmp_path: Path) -> None:
        """``run.gpus: all`` (lowercase) opts in."""
        _write_project_yml(tmp_path, "run:\n  gpus: all\n")
        assert has_gpu(_project(root=tmp_path)) is True

    def test_string_all_is_case_insensitive(self, tmp_path: Path) -> None:
        """``run.gpus: ALL`` and friends count — the model lowercases."""
        _write_project_yml(tmp_path, "run:\n  gpus: ALL\n")
        assert has_gpu(_project(root=tmp_path)) is True

    def test_returns_false_when_gpus_string_other(self, tmp_path: Path) -> None:
        """Any other string value is treated as off (single-GPU specifiers aren't supported here)."""
        _write_project_yml(tmp_path, "run:\n  gpus: '0,1'\n")
        assert has_gpu(_project(root=tmp_path)) is False

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_gpus_passes_through(self, tmp_path: Path, value: bool) -> None:
        """``run.gpus: true`` opts in; ``false`` opts out."""
        _write_project_yml(tmp_path, f"run:\n  gpus: {str(value).lower()}\n")
        assert has_gpu(_project(root=tmp_path)) is value

    def test_returns_false_when_run_section_absent(self, tmp_path: Path) -> None:
        """No ``run`` section → no GPU."""
        _write_project_yml(tmp_path, "id: demo\n")
        assert has_gpu(_project(root=tmp_path)) is False

    def test_returns_false_when_gpus_key_absent(self, tmp_path: Path) -> None:
        """``run`` section without ``gpus`` → no GPU."""
        _write_project_yml(tmp_path, "run:\n  cmd: bash\n")
        assert has_gpu(_project(root=tmp_path)) is False
