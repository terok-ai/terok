# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for hex task ID generation, resolution, and container naming."""

from __future__ import annotations

import unittest.mock

import pytest

from terok.lib.core.task_display import CONTAINER_MODES, container_name
from terok.lib.orchestration.tasks import (
    _generate_unique_id,
    resolve_task_id,
    tasks_meta_dir,
)
from terok.lib.util.yaml import dump as yaml_dump
from tests.test_utils import assert_hex_id, project_env

MINIMAL_PROJECT = """
project:
  id: test-proj
  security_class: online
git:
  upstream_url: https://example.com/repo.git
"""


# ---------- _generate_unique_id ----------


class TestGenerateUniqueId:
    """Tests for the internal hex ID generator."""

    def test_produces_8_char_hex(self) -> None:
        """Generated IDs must be exactly 8 lowercase hex characters."""
        result = _generate_unique_id(set())
        assert_hex_id(result)

    def test_avoids_existing_ids(self) -> None:
        """Generated ID must not collide with any member of *existing*."""
        existing = {_generate_unique_id(set()) for _ in range(20)}
        new_id = _generate_unique_id(existing)
        assert new_id not in existing
        assert_hex_id(new_id)

    def test_uniqueness_across_calls(self) -> None:
        """Multiple generated IDs should all be distinct."""
        ids = {_generate_unique_id(set()) for _ in range(50)}
        assert len(ids) == 50

    def test_raises_after_exhaustion(self) -> None:
        """Should raise RuntimeError if it can't find a unique ID in 100 tries."""
        with unittest.mock.patch("terok.lib.orchestration.tasks.secrets") as mock_secrets:
            mock_secrets.token_hex.return_value = "deadbeef"
            with pytest.raises(RuntimeError, match="Failed to generate unique task ID"):
                _generate_unique_id({"deadbeef"})


# ---------- resolve_task_id ----------


class TestResolveTaskId:
    """Tests for CLI prefix-matching task ID resolution."""

    def _write_meta(self, project_id: str, task_id: str) -> None:
        """Write a minimal task metadata file."""
        meta_dir = tasks_meta_dir(project_id)
        meta_dir.mkdir(parents=True, exist_ok=True)
        meta = {"task_id": task_id, "name": "test-task", "mode": None, "workspace": "/tmp/ws"}
        (meta_dir / f"{task_id}.yml").write_text(yaml_dump(meta))

    def test_exact_match(self) -> None:
        """Full 8-char ID should resolve immediately."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", "abcd1234")
            assert resolve_task_id("test-proj", "abcd1234") == "abcd1234"

    def test_prefix_match(self) -> None:
        """A unique prefix shorter than 8 chars should resolve to the full ID."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", "abcd1234")
            assert resolve_task_id("test-proj", "abcd") == "abcd1234"

    def test_single_char_prefix(self) -> None:
        """Even a 1-char prefix resolves if it uniquely matches."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", "f1234567")
            assert resolve_task_id("test-proj", "f") == "f1234567"

    def test_ambiguous_prefix(self) -> None:
        """Should raise SystemExit listing the matches when prefix is ambiguous."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", "abcd1111")
            self._write_meta("test-proj", "abcd2222")
            with pytest.raises(SystemExit, match="Ambiguous task ID 'abcd'"):
                resolve_task_id("test-proj", "abcd")

    def test_no_match(self) -> None:
        """Should raise SystemExit when no task matches the prefix."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            self._write_meta("test-proj", "abcd1234")
            with pytest.raises(SystemExit, match="No task matching 'ffff'"):
                resolve_task_id("test-proj", "ffff")

    def test_no_tasks_dir(self) -> None:
        """Should raise SystemExit when the project has no tasks directory."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            with pytest.raises(SystemExit, match="No tasks found"):
                resolve_task_id("test-proj", "abcd")

    def test_rejects_non_hex_input(self) -> None:
        """Should reject prefixes containing non-hex characters."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            with pytest.raises(SystemExit, match="Invalid task ID prefix"):
                resolve_task_id("test-proj", "../../etc")

    def test_rejects_uppercase_hex(self) -> None:
        """Should reject uppercase hex characters."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            with pytest.raises(SystemExit, match="Invalid task ID prefix"):
                resolve_task_id("test-proj", "ABCD")

    def test_rejects_empty_string(self) -> None:
        """Should reject an empty prefix."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            with pytest.raises(SystemExit, match="Invalid task ID prefix"):
                resolve_task_id("test-proj", "")

    def test_rejects_too_long_prefix(self) -> None:
        """Should reject prefixes longer than 8 characters."""
        with project_env(MINIMAL_PROJECT) as _ctx:
            with pytest.raises(SystemExit, match="Invalid task ID prefix"):
                resolve_task_id("test-proj", "abcdef0123")


# ---------- container_name ----------


class TestContainerName:
    """Tests for the centralized container naming function."""

    def test_format(self) -> None:
        """Container name should be {project}-{mode}-{task_id}."""
        assert container_name("myproj", "cli", "a1b2c3d4") == "myproj-cli-a1b2c3d4"

    def test_all_modes(self) -> None:
        """Every declared mode should produce a valid container name."""
        for mode in CONTAINER_MODES:
            result = container_name("proj", mode, "deadbeef")
            assert result == f"proj-{mode}-deadbeef"

    def test_container_modes_tuple(self) -> None:
        """CONTAINER_MODES must include the four known modes."""
        assert set(CONTAINER_MODES) == {"cli", "web", "run", "toad"}


# ---------- assert_hex_id ----------


class TestAssertHexId:
    """Tests for the test utility itself."""

    def test_valid_id(self) -> None:
        """Should pass for a valid 8-char hex string."""
        assert_hex_id("abcd1234")

    def test_rejects_none(self) -> None:
        """Should raise AssertionError for None."""
        with pytest.raises(AssertionError, match="Expected task ID string"):
            assert_hex_id(None)

    def test_rejects_short_id(self) -> None:
        """Should raise AssertionError for IDs shorter than 8 chars."""
        with pytest.raises(AssertionError, match="Expected 8-char hex ID"):
            assert_hex_id("abcd")

    def test_rejects_non_hex(self) -> None:
        """Should raise AssertionError for non-hex characters."""
        with pytest.raises(AssertionError, match="Not a hex string"):
            assert_hex_id("abcdXYZW")
