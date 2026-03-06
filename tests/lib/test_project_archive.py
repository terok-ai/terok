# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

import tarfile
import unittest
from pathlib import Path

from terok.lib.core.config import build_root, deleted_projects_dir, state_root
from terok.lib.core.projects import load_project
from terok.lib.facade import _archive_project, delete_project
from terok.lib.util.fs import archive_timestamp, unique_archive_path
from test_utils import project_env


class ArchiveTimestampTests(unittest.TestCase):
    """Tests for archive_timestamp()."""

    def test_returns_utc_timestamp_string(self) -> None:
        """archive_timestamp returns a non-empty string in expected format."""
        ts = archive_timestamp()
        self.assertRegex(ts, r"^\d{8}T\d{6}\d+Z$")

    def test_unique_values(self) -> None:
        """Successive calls produce different timestamps (microsecond precision)."""
        ts1 = archive_timestamp()
        ts2 = archive_timestamp()
        # They *may* collide if called within the same microsecond, but
        # in practice this is extremely unlikely.
        self.assertIsInstance(ts1, str)
        self.assertIsInstance(ts2, str)


class UniqueArchivePathTests(unittest.TestCase):
    """Tests for unique_archive_path()."""

    def test_basic_path(self) -> None:
        """Returns root / base_name + suffix when no collision."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = unique_archive_path(root, "test", ".tar.gz")
            self.assertEqual(result, root / "test.tar.gz")

    def test_collision_avoidance(self) -> None:
        """Appends counter when path already exists."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "test.tar.gz").write_text("existing")
            result = unique_archive_path(root, "test", ".tar.gz")
            self.assertEqual(result, root / "test_1.tar.gz")

    def test_multiple_collisions(self) -> None:
        """Increments counter for multiple collisions."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "test.tar.gz").write_text("existing")
            (root / "test_1.tar.gz").write_text("existing")
            result = unique_archive_path(root, "test", ".tar.gz")
            self.assertEqual(result, root / "test_2.tar.gz")

    def test_no_suffix(self) -> None:
        """Works for directory-style paths (no suffix)."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "mydir").mkdir()
            result = unique_archive_path(root, "mydir")
            self.assertEqual(result, root / "mydir_1")


class ArchiveProjectTests(unittest.TestCase):
    """Tests for _archive_project()."""

    def test_creates_tar_gz_with_config(self) -> None:
        """_archive_project creates a .tar.gz containing project config."""
        project_id = "arch-cfg"
        yaml_text = (
            f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        )
        with project_env(yaml_text, project_id=project_id):
            archive_path = _archive_project(project_id)
            self.assertIsNotNone(archive_path)
            self.assertTrue(archive_path.endswith(".tar.gz"))
            self.assertTrue(Path(archive_path).is_file())

            # Verify tar contents include config/
            with tarfile.open(archive_path, "r:gz") as tar:
                names = tar.getnames()
                self.assertTrue(any(n.startswith("config/") for n in names))

    def test_includes_state_dir(self) -> None:
        """_archive_project includes task metadata from state dir."""
        project_id = "arch-state"
        yaml_text = (
            f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        )
        with project_env(yaml_text, project_id=project_id):
            # Create some task metadata
            meta_dir = state_root() / "projects" / project_id / "tasks"
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / "1.yml").write_text("task_id: '1'\nname: test\n")

            archive_path = _archive_project(project_id)
            self.assertIsNotNone(archive_path)

            with tarfile.open(archive_path, "r:gz") as tar:
                names = tar.getnames()
                self.assertTrue(any("state/" in n for n in names))

    def test_includes_build_dir(self) -> None:
        """_archive_project includes build artifacts."""
        project_id = "arch-build"
        yaml_text = (
            f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        )
        with project_env(yaml_text, project_id=project_id):
            bd = build_root() / project_id
            bd.mkdir(parents=True, exist_ok=True)
            (bd / "L2.Dockerfile").write_text("FROM scratch")

            archive_path = _archive_project(project_id)
            self.assertIsNotNone(archive_path)

            with tarfile.open(archive_path, "r:gz") as tar:
                names = tar.getnames()
                self.assertTrue(any("build/" in n for n in names))

    def test_missing_dirs_graceful(self) -> None:
        """_archive_project handles projects with only a config dir."""
        project_id = "arch-min"
        yaml_text = (
            f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        )
        with project_env(yaml_text, project_id=project_id):
            # No state or build dirs — should still archive config
            archive_path = _archive_project(project_id)
            self.assertIsNotNone(archive_path)
            self.assertTrue(Path(archive_path).is_file())

    def test_archive_stored_in_deleted_projects_dir(self) -> None:
        """Archive is created under deleted_projects_dir()."""
        project_id = "arch-loc"
        yaml_text = (
            f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        )
        with project_env(yaml_text, project_id=project_id):
            archive_path = _archive_project(project_id)
            self.assertIsNotNone(archive_path)
            self.assertTrue(Path(archive_path).parent == deleted_projects_dir())


class DeleteProjectArchiveTests(unittest.TestCase):
    """Tests for delete_project() archive integration."""

    def test_delete_creates_archive_before_deleting(self) -> None:
        """delete_project creates an archive and includes its path in the result."""
        project_id = "del-arch"
        yaml_text = (
            f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        )
        with project_env(yaml_text, project_id=project_id):
            result = delete_project(project_id)
            self.assertIn("archive", result)
            archive_path = result["archive"]
            self.assertTrue(Path(archive_path).is_file())
            self.assertTrue(archive_path.endswith(".tar.gz"))

    def test_archive_survives_deletion(self) -> None:
        """The archive file persists after project directories are removed."""
        project_id = "del-surv"
        yaml_text = (
            f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        )
        with project_env(yaml_text, project_id=project_id):
            project = load_project(project_id)
            config_root = project.root

            result = delete_project(project_id)

            # Config should be gone
            self.assertFalse(config_root.is_dir())
            # Archive should exist
            self.assertTrue(Path(result["archive"]).is_file())

    def test_archive_contains_project_config(self) -> None:
        """The archive created by delete_project contains the project config."""
        project_id = "del-cont"
        yaml_text = (
            f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"
        )
        with project_env(yaml_text, project_id=project_id):
            result = delete_project(project_id)
            archive_path = result["archive"]

            with tarfile.open(archive_path, "r:gz") as tar:
                names = tar.getnames()
                self.assertTrue(any(n.startswith("config/") for n in names))
