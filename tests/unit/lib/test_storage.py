# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the storage usage aggregation domain layer.

The domain module is the bridge between raw queries (agent, sandbox)
and the CLI presentation.  Tests verify lazy measurement, classification,
aggregation, and the size parser with isolated files and mocked containers.
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest

from terok.lib.core.task_state import CONTAINER_MODES, container_name
from terok.lib.domain.image_cleanup import ImageInfo
from terok.lib.domain.storage import (
    ORPHAN_PROJECT_NAME,
    ProjectSummary,
    StorageOverview,
    _is_global_image,
    discover_project_storage,
    discover_storage_overview,
    format_bytes,
    get_project_storage_detail,
    get_storage_overview,
    parse_image_size,
)
from terok.lib.integrations.executor import SharedMountStorageInfo, TaskStorageInfo
from tests.testfs import MOCK_BASE

# ---------------------------------------------------------------------------
# parse_image_size — translating podman prose to integers
# ---------------------------------------------------------------------------


class TestParseImageSize:
    """Podman returns sizes as human-readable strings; we need bytes."""

    def test_gigabytes(self):
        assert parse_image_size("1.23GB") == 1_230_000_000

    def test_megabytes(self):
        assert parse_image_size("456MB") == 456_000_000

    def test_kilobytes(self):
        assert parse_image_size("12KB") == 12_000

    def test_bytes(self):
        assert parse_image_size("1024B") == 1024

    def test_garbage_returns_zero(self):
        assert parse_image_size("???") == 0

    def test_empty_returns_zero(self):
        assert parse_image_size("") == 0


# ---------------------------------------------------------------------------
# format_bytes — the inverse: integers to human-readable
# ---------------------------------------------------------------------------


class TestFormatBytes:
    """Symmetric companion to parse: bytes back to readable strings."""

    def test_gigabytes(self):
        assert format_bytes(1_500_000_000) == "1.5 GB"

    def test_megabytes(self):
        assert format_bytes(12_500_000) == "12.5 MB"

    def test_kilobytes(self):
        assert format_bytes(4_000) == "4.0 KB"

    def test_bytes(self):
        assert format_bytes(42) == "42 B"

    def test_zero(self):
        assert format_bytes(0) == "0 B"


# ---------------------------------------------------------------------------
# Image classification — global vs per-project
# ---------------------------------------------------------------------------


class TestImageClassification:
    """L0/L1 and dangling images are global; L2 images belong to a project."""

    def test_l0_is_global(self):
        img = ImageInfo("terok-l0", "bookworm", "sha256:abc", "1GB", "2d ago")
        assert _is_global_image(img)

    def test_l1_is_global(self):
        img = ImageInfo("terok-l1-cli", "bookworm", "sha256:def", "2GB", "3d ago")
        assert _is_global_image(img)

    def test_dangling_is_global(self):
        img = ImageInfo("<none>", "<none>", "sha256:ghi", "500MB", "1d ago")
        assert _is_global_image(img)

    def test_l2_is_not_global(self):
        img = ImageInfo("myproject", "l2-cli", "sha256:jkl", "3GB", "1d ago")
        assert not _is_global_image(img)

    def test_localhost_prefixed_l0_is_global(self):
        """Podman's ``localhost/`` registry prefix must not disqualify a base image."""
        img = ImageInfo("localhost/terok-l0", "fedora-43", "sha256:m", "1GB", "1d")
        assert _is_global_image(img)

    def test_localhost_prefixed_l1_is_global(self):
        img = ImageInfo("localhost/terok-l1-cli", "fedora-43", "sha256:n", "2GB", "1d")
        assert _is_global_image(img)


# ---------------------------------------------------------------------------
# StorageOverview properties
# ---------------------------------------------------------------------------


class TestStorageOverviewProperties:
    """The overview dataclass computes totals from its children."""

    def test_grand_total(self):
        overview = StorageOverview(
            global_images=[ImageInfo("terok-l0", "bkwm", "id1", "1GB", "2d")],
            shared_mounts=[],
            projects=[
                ProjectSummary(
                    "proj1", image_bytes=500_000_000, workspace_bytes=200_000_000, task_count=1
                )
            ],
        )
        # 1 GB image + 500M project images + 200M workspaces
        assert overview.grand_total == 1_000_000_000 + 500_000_000 + 200_000_000


# ---------------------------------------------------------------------------
# get_storage_overview — the orchestrator
# ---------------------------------------------------------------------------


_MOCK_IMAGES = [
    ImageInfo("terok-l0", "bookworm", "id1", "1GB", "2d ago"),
    ImageInfo("myproject", "l2-cli", "id2", "3GB", "1d ago"),
]


class TestGetStorageOverview:
    """Overview wires together image listing, mount queries, and project enumeration."""

    @patch("terok.lib.domain.storage.SharedMountStorageInfo.measure_all", return_value=[])
    @patch("terok.lib.domain.storage.TaskStorageInfo.measure_all", return_value=[])
    @patch("terok.lib.domain.storage.list_projects")
    @patch("terok.lib.domain.storage.list_images", return_value=_MOCK_IMAGES)
    @patch("terok.lib.domain.storage.sandbox_live_mounts_dir", return_value=MOCK_BASE / "mounts")
    def test_classifies_images(self, _mounts, _imgs, mock_projects, _tasks, _shared):
        mock_projects.return_value = [_fake_project("myproject")]
        overview = get_storage_overview()
        assert len(overview.global_images) == 1
        assert overview.global_images[0].repository == "terok-l0"
        assert len(overview.projects) == 1
        assert overview.projects[0].project_name == "myproject"

    @patch("terok.lib.domain.storage.SharedMountStorageInfo.measure_all", return_value=[])
    @patch("terok.lib.domain.storage.TaskStorageInfo.measure_all", return_value=[])
    @patch("terok.lib.domain.storage.list_projects", return_value=[])
    @patch("terok.lib.domain.storage.list_images", return_value=[])
    @patch("terok.lib.domain.storage.sandbox_live_mounts_dir", return_value=MOCK_BASE / "mounts")
    def test_empty_system(self, _mounts, _imgs, _projects, _tasks, _shared):
        overview = get_storage_overview()
        assert overview.grand_total == 0

    @patch("terok.lib.domain.storage.SharedMountStorageInfo.measure_all", return_value=[])
    @patch("terok.lib.domain.storage.TaskStorageInfo.measure_all", return_value=[])
    @patch("terok.lib.domain.storage.list_projects")
    @patch("terok.lib.domain.storage.list_images")
    @patch("terok.lib.domain.storage.sandbox_live_mounts_dir", return_value=MOCK_BASE / "mounts")
    def test_localhost_prefix_attributes_to_project(
        self, _mounts, mock_images, mock_projects, _tasks, _shared
    ):
        """L2 images with podman's ``localhost/`` prefix roll up to the bare project name."""
        mock_images.return_value = [
            ImageInfo("localhost/myproject", "l2-cli", "id", "3GB", "1d ago"),
        ]
        mock_projects.return_value = [_fake_project("myproject")]
        overview = get_storage_overview()
        assert len(overview.projects) == 1
        assert overview.projects[0].project_name == "myproject"
        assert overview.projects[0].image_bytes == 3_000_000_000

    @patch("terok.lib.domain.storage.SharedMountStorageInfo.measure_all", return_value=[])
    @patch("terok.lib.domain.storage.TaskStorageInfo.measure_all", return_value=[])
    @patch("terok.lib.domain.storage.list_projects")
    @patch("terok.lib.domain.storage.list_images")
    @patch("terok.lib.domain.storage.sandbox_live_mounts_dir", return_value=MOCK_BASE / "mounts")
    def test_orphan_l2_image_surfaces_in_overview(
        self, _mounts, mock_images, mock_projects, _tasks, _shared
    ):
        """L2 images whose project is gone roll up under an "(orphans)" pseudo-project."""
        mock_images.return_value = [
            ImageInfo("localhost/myproject", "l2-cli", "id1", "1GB", "1d ago"),
            ImageInfo("localhost/old-renamed", "l2-cli", "id2", "2GB", "5d ago"),
        ]
        mock_projects.return_value = [_fake_project("myproject")]
        overview = get_storage_overview()
        project_names = [p.project_name for p in overview.projects]
        assert ORPHAN_PROJECT_NAME in project_names
        orphan = next(p for p in overview.projects if p.project_name == ORPHAN_PROJECT_NAME)
        assert orphan.image_bytes == 2_000_000_000
        assert orphan.task_count == 0
        # Orphan bytes contribute to the grand total.
        assert overview.grand_total == 1_000_000_000 + 2_000_000_000

    @patch("terok.lib.domain.storage.SharedMountStorageInfo.measure_all", return_value=[])
    @patch("terok.lib.domain.storage.TaskStorageInfo.measure_all", return_value=[])
    @patch("terok.lib.domain.storage.list_projects")
    @patch("terok.lib.domain.storage.list_images")
    @patch("terok.lib.domain.storage.sandbox_live_mounts_dir", return_value=MOCK_BASE / "mounts")
    def test_no_orphan_row_when_everything_matches(
        self, _mounts, mock_images, mock_projects, _tasks, _shared
    ):
        """The orphan row appears only when there's something to put in it."""
        mock_images.return_value = [
            ImageInfo("localhost/myproject", "l2-cli", "id1", "1GB", "1d ago"),
        ]
        mock_projects.return_value = [_fake_project("myproject")]
        overview = get_storage_overview()
        assert all(p.project_name != ORPHAN_PROJECT_NAME for p in overview.projects)

    @pytest.mark.parametrize("scope, credential_bytes", [("shared", 0), ("project", 20)])
    def test_counts_private_credentials_without_rebilling_shared_mounts(
        self, tmp_path, scope, credential_bytes
    ):
        """Shared credentials belong globally; only private credentials belong to a project."""
        shared_root = tmp_path / "shared"
        shared_mount = shared_root / "_claude-config"
        shared_mount.mkdir(parents=True)
        (shared_mount / "config").write_bytes(b"s" * 10)
        project = _fake_project("myproject")
        project.tasks_root = tmp_path / "tasks"
        workspace = project.tasks_root / "abc" / "workspace-dangerous"
        workspace.mkdir(parents=True)
        (workspace / "code").write_bytes(b"w" * 17)
        project.credentials_scope = scope
        project.project_mounts_dir = shared_root if scope == "shared" else tmp_path / "private"
        if scope == "project":
            private_mount = project.project_mounts_dir / "_claude-config"
            private_mount.mkdir(parents=True)
            (private_mount / "config").write_bytes(b"p" * 20)

        with (
            patch("terok.lib.domain.storage.sandbox_live_mounts_dir", return_value=shared_root),
            patch("terok.lib.domain.storage.list_images", return_value=_MOCK_IMAGES),
            patch("terok.lib.domain.storage.list_projects", return_value=[project]),
        ):
            overview = get_storage_overview()

        assert overview.shared_mounts_bytes == 10
        assert overview.projects == [
            ProjectSummary("myproject", 3_000_000_000, 17, 1, credential_bytes)
        ]
        assert overview.grand_total == 4_000_000_000 + 10 + 17 + credential_bytes


class TestDiscoverStorageOverview:
    """Discover labels first; measure only the selected mount or project."""

    def test_defers_directory_measurements_and_keeps_rows_independent(self, tmp_path):
        shared_root = tmp_path / "shared"
        for name in ("_beta-config", "_alpha-config"):
            (shared_root / name).mkdir(parents=True)
        (shared_root / "not-a-mount").touch()
        projects = [_fake_project(name) for name in ("first", "second")]
        with (
            patch("terok.lib.domain.storage.sandbox_live_mounts_dir", return_value=shared_root),
            patch("terok.lib.domain.storage.list_images", return_value=_MOCK_IMAGES),
            patch("terok.lib.domain.storage.list_projects", return_value=projects),
            patch.object(
                SharedMountStorageInfo, "measure", wraps=SharedMountStorageInfo.measure
            ) as measure_mount,
            patch.object(TaskStorageInfo, "measure_all", return_value=[]) as measure_tasks,
        ):
            pending = discover_storage_overview()

            assert pending.global_images == [_MOCK_IMAGES[0]]
            assert [row.name for row in pending.shared_mounts] == ["Alpha config", "Beta config"]
            assert [row.name for row in pending.projects] == [
                "first",
                "second",
                ORPHAN_PROJECT_NAME,
            ]
            measure_mount.assert_not_called()
            measure_tasks.assert_not_called()

            assert pending.shared_mounts[1].measure().name == "_beta-config"
            measure_mount.assert_called_once_with(shared_root / "_beta-config")
            assert pending.shared_mounts[0].measure().name == "_alpha-config"
            assert measure_mount.call_args_list == [
                call(shared_root / "_beta-config"),
                call(shared_root / "_alpha-config"),
            ]
            assert pending.projects[1].measure().project_name == "second"
            measure_tasks.assert_called_once_with(projects[1].tasks_root)
            assert pending.projects[0].measure().project_name == "first"
            assert measure_tasks.call_args_list == [
                call(projects[1].tasks_root),
                call(projects[0].tasks_root),
            ]
            assert pending.projects[2].measure() == ProjectSummary(
                ORPHAN_PROJECT_NAME, 3_000_000_000, 0, 0
            )
            assert measure_tasks.call_count == 2


# ---------------------------------------------------------------------------
# get_project_storage_detail — zooming in
# ---------------------------------------------------------------------------


class TestGetProjectStorageDetail:
    """Detail mode queries per-task sizes and overlay data."""

    @patch("terok.lib.domain.storage.list_images", return_value=[])
    @patch("terok.lib.core.projects.load_project")
    def test_returns_project_detail(self, mock_load, _imgs, mock_runtime, tmp_path):
        container = MagicMock()
        container.name = container_name("myproject", "cli", "abc")
        container.rw_size = 1024
        mock_runtime.containers_with_prefix.return_value = [container]
        project = _fake_project("myproject")
        project.tasks_root = tmp_path / "missing-tasks"
        mock_load.return_value = project
        detail = get_project_storage_detail("myproject")
        assert detail.project_name == "myproject"
        assert detail.overlays == {"abc": 1024}
        assert detail.tasks == [TaskStorageInfo("abc", 0, 0)]
        assert detail.total_bytes == 1024

    def test_collects_workspace_and_overlay_bytes_under_the_same_task_id(
        self, tmp_path, mock_runtime
    ):
        """Every displayed task row contributes the same bytes to the aggregate totals."""
        project = _fake_project("myproject")
        project.tasks_root = tmp_path / "tasks"
        workspace = project.tasks_root / "abc" / "workspace-dangerous"
        workspace.mkdir(parents=True)
        (workspace / "code").write_bytes(b"w" * 17)
        agent_config = project.tasks_root / "abc" / "agent-config"
        agent_config.mkdir()
        (agent_config / "config").write_bytes(b"c" * 5)
        containers = []
        for mode in CONTAINER_MODES:
            container = MagicMock()
            container.name = container_name(project.name, mode, "abc")
            container.rw_size = 100
            containers.append(container)
        mock_runtime.containers_with_prefix.return_value = containers
        images = [ImageInfo("myproject", "l2-cli", "id", "3GB", "1d ago")]

        with (
            patch("terok.lib.core.projects.load_project", return_value=project),
            patch("terok.lib.domain.storage.list_images", return_value=images),
        ):
            detail = get_project_storage_detail(project.name)

        assert detail.images == images
        assert detail.tasks == [TaskStorageInfo("abc", 17, 5)]
        assert detail.overlays == {"abc": 100 * len(CONTAINER_MODES)}
        assert detail.workspace_bytes == 17
        assert detail.overlay_bytes == 100 * len(CONTAINER_MODES)
        assert detail.total_bytes == 3_000_000_000 + 17 + 100 * len(CONTAINER_MODES)


class TestDiscoverProjectStorage:
    """Task discovery leaves filesystem walks and container size queries for each row."""

    def test_defers_measurements_and_inspects_each_container_once(self, tmp_path, mock_runtime):
        project = _fake_project("myproject")
        project.tasks_root = tmp_path / "tasks"
        for task_id in ("beta", "alpha"):
            (project.tasks_root / task_id).mkdir(parents=True)
        (project.tasks_root / "not-a-task").touch()
        project.credentials_scope = "project"
        project.project_mounts_dir = tmp_path / "private"
        containers, size_queries = [], []
        for mode in CONTAINER_MODES:
            container = MagicMock()
            container.name = container_name(project.name, mode, "alpha")
            size_query = PropertyMock(return_value=100)
            type(container).rw_size = size_query
            containers.append(container)
            size_queries.append(size_query)
        beta = MagicMock()
        beta.name = container_name(project.name, "cli", "beta")
        beta_size = PropertyMock(return_value=None)
        type(beta).rw_size = beta_size
        containers.append(beta)
        mock_runtime.containers_with_prefix.return_value = containers
        with (
            patch("terok.lib.core.projects.load_project", return_value=project),
            patch("terok.lib.domain.storage.list_images", return_value=[]),
            patch.object(TaskStorageInfo, "measure", wraps=TaskStorageInfo.measure) as measure_task,
            patch.object(
                SharedMountStorageInfo,
                "measure_all",
                return_value=[SharedMountStorageInfo("_claude-config", "Claude config", 20)],
            ) as measure_credentials,
        ):
            pending = discover_project_storage(project.name)

            assert [row.name for row in pending.tasks] == ["alpha", "beta"]
            measure_task.assert_not_called()
            measure_credentials.assert_not_called()
            for query in (*size_queries, beta_size):
                query.assert_not_called()

            assert pending.tasks[0].measure() == (
                TaskStorageInfo("alpha", 0, 0),
                100 * len(CONTAINER_MODES),
            )
            measure_task.assert_called_once_with(project.tasks_root / "alpha")
            for query in size_queries:
                query.assert_called_once_with()
            beta_size.assert_not_called()
            assert pending.tasks[1].measure() == (TaskStorageInfo("beta", 0, 0), 0)
            beta_size.assert_called_once_with()
            measure_credentials.assert_not_called()
            assert pending.measure_credentials() == 20
            measure_credentials.assert_called_once_with(project.project_mounts_dir)

    @pytest.mark.parametrize(
        "name",
        [
            "myproject-other-cli-abc",
            "myproject-cli-cli-abc",
            "myproject-auth",
            "myproject-cli-..",
            "myproject-cli-.",
            "myproject-cli-",
            "other-cli-abc",
        ],
    )
    def test_ignores_non_task_containers(self, name, tmp_path, mock_runtime):
        container = MagicMock()
        container.name = name
        size_query = PropertyMock(side_effect=AssertionError("Unrelated container was measured"))
        type(container).rw_size = size_query
        mock_runtime.containers_with_prefix.return_value = [container]
        project = _fake_project("myproject")
        project.tasks_root = tmp_path / "missing-tasks"

        with (
            patch("terok.lib.core.projects.load_project", return_value=project),
            patch("terok.lib.domain.storage.list_images", return_value=[]),
        ):
            detail = get_project_storage_detail(project.name)

        assert detail.tasks == []
        assert detail.overlays == {}
        size_query.assert_not_called()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_project(pid: str):
    """Minimal stand-in for ProjectConfig."""
    p = MagicMock()
    p.name = pid
    p.tasks_root = MOCK_BASE / "tasks" / pid
    p.credentials_scope = "shared"
    return p
