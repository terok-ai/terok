# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``terok image usage`` (argparse + dispatch + render).

Parsing and dispatch live in [`terok.cli.commands.image`][terok.cli.commands.image]; the
overview/detail rendering lives in
`terok.cli.commands._storage_view`.  This test module covers
both layers.
"""

from __future__ import annotations

import argparse
import json
import re
from functools import partial
from io import StringIO
from unittest.mock import patch

import pytest

from terok.cli.commands._storage_view import cmd_detail, cmd_overview
from terok.cli.commands.image import dispatch, register
from terok.lib.domain.image_cleanup import ImageInfo
from terok.lib.domain.storage import (
    OverviewMeasurements,
    ProjectDetail,
    ProjectMeasurements,
    ProjectSummary,
    StorageMeasurement,
    StorageOverview,
)
from terok.lib.integrations.executor import SharedMountStorageInfo, TaskStorageInfo

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    """Build a parser with the image command registered."""
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    register(sub)
    return p


def _make_overview(**kwargs) -> StorageOverview:
    """Build a minimal StorageOverview for testing."""
    defaults = {
        "global_images": [ImageInfo("terok-l0", "bkwm", "id1", "1GB", "2d ago")],
        "shared_mounts": [],
        "projects": [ProjectSummary("proj1", 500_000_000, 200_000_000, 2)],
    }
    defaults.update(kwargs)
    return StorageOverview(**defaults)


def _make_detail(**kwargs) -> ProjectDetail:
    """Build a minimal ProjectDetail for testing."""
    defaults = {
        "project_name": "myproject",
        "images": [ImageInfo("myproject", "l2-cli", "id2", "3GB", "1d ago")],
        "tasks": [],
        "overlays": {},
    }
    defaults.update(kwargs)
    return ProjectDetail(**defaults)


def _overview_measurements(overview: StorageOverview) -> OverviewMeasurements:
    """Turn a finished fixture into independently measurable rows."""
    return OverviewMeasurements(
        overview.global_images,
        [StorageMeasurement(m.label, partial(_identity, m)) for m in overview.shared_mounts],
        [StorageMeasurement(p.project_name, partial(_identity, p)) for p in overview.projects],
    )


def _identity[T](value: T) -> T:
    """Return the fixture value when its measurement is requested."""
    return value


class _FlushRecorder(StringIO):
    """Record precisely what a user can see after every explicit flush."""

    def __init__(self) -> None:
        super().__init__()
        self.flushed: list[str] = []

    def flush(self) -> None:
        self.flushed.append(self.getvalue())
        super().flush()


def _observed_row[T](
    output: _FlushRecorder,
    name: str,
    width: int,
    value: T,
    previous_line: str | None = None,
) -> StorageMeasurement[T]:
    """Require a flushed label and completed predecessor before counting."""

    def measure() -> T:
        assert output.flushed[-1].endswith(f"  {name:<{width}}  ")
        assert output.getvalue() == output.flushed[-1]
        if previous_line is not None:
            assert any(snapshot.endswith(previous_line + "\n") for snapshot in output.flushed)
        return value

    return StorageMeasurement(name, measure)


def _right_edges_align(header: str, row: str, *columns: tuple[str, str]) -> None:
    """Compare visible right edges rather than ANSI escape-string lengths."""
    for title, value in columns:
        assert header.index(title) + len(title) == row.index(value) + len(value)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class TestRegister:
    """The ``image usage`` subcommand registers cleanly."""

    def test_default_mode(self):
        args = _parser().parse_args(["image", "usage"])
        assert args.cmd == "image"
        assert args.image_cmd == "usage"
        assert args.project is None
        assert args.json_output is False

    def test_project_flag(self):
        args = _parser().parse_args(["image", "usage", "--project", "myproj"])
        assert args.project == "myproj"

    def test_json_flag(self):
        args = _parser().parse_args(["image", "usage", "--json"])
        assert args.json_output is True


# ---------------------------------------------------------------------------
# Dispatch routing
# ---------------------------------------------------------------------------


class TestDispatch:
    """Dispatch routes ``image usage`` into the storage-view pipelines."""

    def test_overview_mode(self):
        args = argparse.Namespace(cmd="image", image_cmd="usage", project=None, json_output=False)
        with patch("terok.cli.commands.image._storage_view.cmd_overview") as mock:
            assert dispatch(args) is True
            mock.assert_called_once_with(json_output=False)

    def test_detail_mode_with_project(self):
        args = argparse.Namespace(
            cmd="image", image_cmd="usage", project="myproj", json_output=False
        )
        with patch("terok.cli.commands.image._storage_view.cmd_detail") as mock:
            assert dispatch(args) is True
            mock.assert_called_once_with("myproj", json_output=False)

    def test_ignores_unrelated_commands(self):
        args = argparse.Namespace(cmd="task")
        assert dispatch(args) is False


# ---------------------------------------------------------------------------
# Overview output (render layer)
# ---------------------------------------------------------------------------


class TestOverviewOutput:
    """``cmd_overview`` prints a human-readable summary (or JSON)."""

    @patch("terok.cli.commands._storage_view.supports_color", return_value=False)
    def test_prints_without_error(self, _color, capsys):
        with patch(
            "terok.lib.domain.storage.discover_storage_overview",
            return_value=_overview_measurements(_make_overview()),
        ):
            cmd_overview()
        output = capsys.readouterr().out
        assert "Global" in output
        assert "terok-l0" in output
        assert "Grand total" in output

    @patch("terok.cli.commands._storage_view.supports_color", return_value=False)
    def test_empty_system(self, _color, capsys):
        with patch(
            "terok.lib.domain.storage.discover_storage_overview",
            return_value=OverviewMeasurements([], [], []),
        ):
            cmd_overview()
        output = capsys.readouterr().out
        assert "Grand total" in output

    def test_json_output(self, capsys):
        with patch(
            "terok.lib.domain.storage.discover_storage_overview",
            return_value=_overview_measurements(_make_overview()),
        ):
            cmd_overview(json_output=True)
        data = json.loads(capsys.readouterr().out)
        assert "global" in data
        assert "projects" in data
        assert data["grand_total_bytes"] == 1_700_000_000

    def test_json_projects_use_project_name_key(self, capsys):
        """Overview JSON uses the renamed project_name field consistently."""
        with patch(
            "terok.lib.domain.storage.get_storage_overview",
            return_value=_make_overview(),
        ):
            cmd_overview(json_output=True)
        data = json.loads(capsys.readouterr().out)
        assert data["projects"][0]["project_name"] == "proj1"
        assert "id" not in data["projects"][0]

    @patch("terok.cli.commands._storage_view.supports_color", return_value=False)
    def test_text_projects_render_project_names(self, _color, capsys):
        """Overview text renders the project table with project names."""
        overview = _make_overview(
            global_images=[],
            projects=[
                ProjectSummary("proj1", 500_000_000, 200_000_000, 2),
                ProjectSummary("longer-project", 100_000_000, 50_000_000, 1),
            ],
        )
        with patch(
            "terok.lib.domain.storage.discover_storage_overview",
            return_value=_overview_measurements(overview),
        ):
            cmd_overview(json_output=False)
        output = capsys.readouterr().out
        assert "Projects" in output
        assert "proj1" in output
        assert "longer-project" in output

    @patch("terok.cli.commands._storage_view.supports_color", return_value=False)
    def test_flushes_each_mount_and_project_before_counting(self, _color):
        """Every slow row exposes its name; prior rows are already complete."""
        output = _FlushRecorder()
        first_mount = f"  {'Claude config':<13}  {'3.0 GB':>10}"
        second_mount = f"  {'Codex config':<13}  {'4.0 GB':>10}"
        first_project = f"  {'a':<7}  {'1.0 GB':>10}  {'2.0 GB':>10}  {1:>5}"
        pending = OverviewMeasurements(
            [ImageInfo("terok-l0", "base", "image", "5GB", "today")],
            [
                _observed_row(
                    output,
                    "Claude config",
                    13,
                    SharedMountStorageInfo("_claude-config", "Claude config", 3_000_000_000),
                    f"  {'':<13}  {'5.0 GB':>6}",
                ),
                _observed_row(
                    output,
                    "Codex config",
                    13,
                    SharedMountStorageInfo("_codex-config", "Codex config", 4_000_000_000),
                    first_mount,
                ),
            ],
            [
                _observed_row(
                    output,
                    "a",
                    7,
                    ProjectSummary("a", 1_000_000_000, 2_000_000_000, 1),
                    second_mount,
                ),
                _observed_row(output, "b", 7, ProjectSummary("b", 0, 0, 2), first_project),
            ],
        )
        with (
            patch("terok.lib.domain.storage.discover_storage_overview", return_value=pending),
            patch("sys.stdout", output),
        ):
            cmd_overview()
        assert output.flushed[-1].splitlines()[-1] == f"Grand total{'15.0 GB':>39}"

    @pytest.mark.parametrize(
        "project_name,image_name,tag",
        [
            ("p", "a", "t"),
            (
                "ultimate-container",
                "localhost/terok-l1-cli",
                "fedora-44-blablador-caddy-claude-codex-copilot-gh-glab-kisski-"
                "opencode-openrouter-opper-pi-sonar-toad-vibe",
            ),
        ],
    )
    def test_columns_align_with_and_without_color(self, project_name, image_name, tag, capsys):
        """Headers, rows, and totals share columns even with short names."""
        overview = _make_overview(
            global_images=[ImageInfo(image_name, tag, "image", "500MB", "today")],
            shared_mounts=[SharedMountStorageInfo("_claude-config", "Claude config", 3000)],
            projects=[ProjectSummary(project_name, 1_000_000_000, 2_000_000_000_000, 3)],
        )
        rendered = []
        for color_enabled in (False, True):
            with (
                patch(
                    "terok.cli.commands._storage_view.supports_color", return_value=color_enabled
                ),
                patch(
                    "terok.lib.domain.storage.discover_storage_overview",
                    return_value=_overview_measurements(overview),
                ),
            ):
                cmd_overview()
            rendered.append(capsys.readouterr().out)
        assert "\x1b[" in rendered[1]
        assert rendered[0] == re.sub(r"\x1b\[[\d;]*m", "", rendered[1])
        lines = rendered[0].splitlines()
        image_header = next(line for line in lines if "CREATED" in line)
        image_row = next(line for line in lines if f"{image_name}:{tag}" in line)
        _right_edges_align(image_header, image_row, ("SIZE", "500MB"))
        assert image_header.index("CREATED") == image_row.index("today")
        image_total = lines[lines.index(image_row) + 2]
        _right_edges_align(image_header, image_total, ("SIZE", "500.0 MB"))
        mount_row = next(line for line in lines if "Claude config" in line)
        mount_total = lines[lines.index(mount_row) + 2]
        assert mount_row.index("3.0 KB") == mount_total.index("3.0 KB")
        project_header = next(line for line in lines if "WORKSPACES" in line)
        project_row = next(line for line in lines if line.lstrip().startswith(project_name + " "))
        _right_edges_align(
            project_header,
            project_row,
            ("IMAGES", "1.0 GB"),
            ("WORKSPACES", "2.0 TB"),
            ("TASKS", "3"),
        )
        project_total = lines[lines.index(project_row) + 2]
        _right_edges_align(
            project_header,
            project_total,
            ("IMAGES", "1.0 GB"),
            ("WORKSPACES", "2.0 TB"),
            ("TASKS", "3"),
        )


# ---------------------------------------------------------------------------
# Detail output (render layer)
# ---------------------------------------------------------------------------


class TestDetailOutput:
    """``cmd_detail`` prints per-task breakdown for one project."""

    @patch("terok.cli.commands._storage_view.supports_color", return_value=False)
    def test_prints_without_error(self, _color, capsys):
        pending = ProjectMeasurements(_make_detail().images, [], lambda: 0)
        with patch("terok.lib.domain.storage.discover_project_storage", return_value=pending):
            cmd_detail("myproject")
        output = capsys.readouterr().out
        assert "myproject" in output
        assert "Project total" in output

    def test_json_output(self, capsys):
        with patch(
            "terok.lib.domain.storage.get_project_storage_detail", return_value=_make_detail()
        ):
            cmd_detail("myproject", json_output=True)
        data = json.loads(capsys.readouterr().out)
        assert data["project_name"] == "myproject"
        assert "images" in data
        assert "tasks" in data

    @patch("terok.cli.commands._storage_view.supports_color", return_value=False)
    def test_flushes_tasks_and_project_total_before_counting(self, _color):
        """Task rows and private credential counting never hide their labels."""
        output = _FlushRecorder()
        first_task = f"  {'a':<2}  {'2.0 GB':>10}  {'3.0 GB':>10}"
        second_task = f"  {'b':<2}  {'4.0 GB':>10}  {'5.0 GB':>10}"

        def credentials() -> int:
            assert output.flushed[-1].endswith("Project total  ")
            assert any(snapshot.endswith(second_task + "\n") for snapshot in output.flushed)
            return 6_000_000_000

        pending = ProjectMeasurements(
            [],
            [
                _observed_row(
                    output, "a", 2, (TaskStorageInfo("a", 2_000_000_000, 0), 3_000_000_000)
                ),
                _observed_row(
                    output,
                    "b",
                    2,
                    (TaskStorageInfo("b", 4_000_000_000, 0), 5_000_000_000),
                    first_task,
                ),
            ],
            credentials,
        )
        with (
            patch("terok.lib.domain.storage.discover_project_storage", return_value=pending),
            patch("sys.stdout", output),
        ):
            cmd_detail("myproject")
        assert output.flushed[-1].splitlines()[-1] == f"Project total{'20.0 GB':>37}"

    @pytest.mark.parametrize("task_id", ["a", "j24kk"])
    def test_task_columns_align_with_and_without_color(self, task_id, capsys):
        """Task ID and numeric headers align even when IDs are one character."""
        pending = ProjectMeasurements(
            [],
            [StorageMeasurement(task_id, lambda: (TaskStorageInfo(task_id, 2_000_000, 0), 3000))],
            lambda: 0,
        )
        rendered = []
        for color_enabled in (False, True):
            with (
                patch(
                    "terok.cli.commands._storage_view.supports_color", return_value=color_enabled
                ),
                patch("terok.lib.domain.storage.discover_project_storage", return_value=pending),
            ):
                cmd_detail("myproject")
            rendered.append(capsys.readouterr().out)
        assert "\x1b[" in rendered[1]
        assert rendered[0] == re.sub(r"\x1b\[[\d;]*m", "", rendered[1])
        lines = rendered[0].splitlines()
        header = next(line for line in lines if "WORKSPACE" in line)
        row = next(line for line in lines if line.lstrip().startswith(task_id + " "))
        _right_edges_align(header, row, ("WORKSPACE", "2.0 MB"), ("OVERLAY", "3.0 KB"))
        total = lines[lines.index(row) + 2]
        _right_edges_align(header, total, ("WORKSPACE", "2.0 MB"), ("OVERLAY", "3.0 KB"))

    @pytest.mark.parametrize("json_output", [False, True])
    @patch("terok.cli.commands._storage_view.supports_color", return_value=False)
    def test_nonzero_overlays_appear_in_task_rows_and_total(self, _color, json_output, capsys):
        """Collected overlays stay keyed by task ID in both output formats."""
        pending = ProjectMeasurements(
            [],
            [StorageMeasurement("j24kk", lambda: (TaskStorageInfo("j24kk", 1000, 20), 3000))],
            lambda: 500,
        )
        with patch("terok.lib.domain.storage.discover_project_storage", return_value=pending):
            cmd_detail("myproject", json_output=json_output)
        output = capsys.readouterr().out
        if json_output:
            data = json.loads(output)
            assert data["tasks"] == [
                {
                    "task_id": "j24kk",
                    "workspace_bytes": 1000,
                    "agent_config_bytes": 20,
                    "overlay_bytes": 3000,
                }
            ]
            assert data["total_bytes"] == 4500
        else:
            assert f"  j24kk  {'1.0 KB':>10}  {'3.0 KB':>10}" in output.splitlines()
            assert output.splitlines()[-1] == f"Project total{'4.5 KB':>37}"

    @pytest.mark.parametrize("error", [OSError("unavailable"), KeyboardInterrupt()])
    @pytest.mark.parametrize("credentials", [False, True])
    @patch("terok.cli.commands._storage_view.supports_color", return_value=False)
    def test_failed_measurement_terminates_partial_line(self, _color, credentials, error):
        """Errors and interruption cannot leave the next prompt on a partial row."""
        output = _FlushRecorder()
        prefix = "Project total  " if credentials else "  j24kk  "

        def fail():
            assert output.flushed[-1].endswith(prefix)
            raise error

        pending = ProjectMeasurements(
            [],
            [] if credentials else [StorageMeasurement("j24kk", fail)],
            fail if credentials else lambda: 0,
        )
        with (
            patch("terok.lib.domain.storage.discover_project_storage", return_value=pending),
            patch("sys.stdout", output),
            pytest.raises(type(error)),
        ):
            cmd_detail("myproject")
        assert output.flushed[-1].endswith(prefix + "\n")
        if not credentials:
            assert "Project total" not in output.getvalue()
