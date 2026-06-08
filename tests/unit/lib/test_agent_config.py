# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for layered agent config resolution."""

import os
import tempfile
import unittest.mock
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

import pytest

from terok.lib.core.projects import load_project
from terok.lib.orchestration.agent_config import resolve_agent_config
from tests.test_utils import mock_git_config, write_project


def _env(
    config_root: Path,
    state_root: Path,
    global_config: Path | None = None,
    xdg_config_home: Path | None = None,
) -> dict[str, str]:
    """Build env dict for test isolation.

    Always sets XDG_CONFIG_HOME to prevent leaking the host value
    (which would let real user config pollute test results).
    """
    env: dict[str, str] = {
        "TEROK_CONFIG_DIR": str(config_root),
        "TEROK_STATE_DIR": str(state_root),
        "TEROK_CONFIG_FILE": "",
        "XDG_CONFIG_HOME": str(xdg_config_home or config_root.parent / "xdg"),
    }
    if global_config:
        env["TEROK_CONFIG_FILE"] = str(global_config)
    return env


@dataclass(frozen=True)
class AgentConfigLayout:
    """Isolated filesystem layout for agent-config resolution tests."""

    base: Path
    config_root: Path
    state_root: Path
    xdg_config_home: Path


def make_layout(base: Path) -> AgentConfigLayout:
    """Build the standard isolated config/state/XDG layout for tests."""
    return AgentConfigLayout(
        base=base,
        config_root=base / "config",
        state_root=base / "s",
        xdg_config_home=base / "xdg",
    )


def write_test_project(
    layout: AgentConfigLayout, project_name: str, body: str | None = None
) -> None:
    """Write a test project config, defaulting to a minimal project definition."""
    write_project(
        layout.config_root / "projects",
        project_name,
        body or f"project:\n  id: {project_name}\n",
    )


def _patched_env(
    layout: AgentConfigLayout,
    *,
    global_config: Path | None = None,
    xdg_config_home: Path | None = None,
) -> AbstractContextManager[dict[str, str]]:
    """Patch TEROK_* and XDG env vars for the given layout."""
    return unittest.mock.patch.dict(
        os.environ,
        _env(
            layout.config_root,
            layout.state_root,
            global_config,
            xdg_config_home or layout.xdg_config_home,
        ),
    )


def resolve_test_agent_config(
    layout: AgentConfigLayout,
    project_name: str,
    *,
    global_config: Path | None = None,
    cli_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    """Resolve agent config inside the isolated test environment."""
    with _patched_env(layout, global_config=global_config):
        with mock_git_config():
            project = load_project(project_name)
            return resolve_agent_config(
                project_name,
                agent_config=project.agent_config,
                project_root=project.root,
                cli_overrides=cli_overrides,
            )


class TestResolveAgentConfig:
    """Tests for resolve_agent_config()."""

    def test_empty_config_all_levels(self) -> None:
        """Returns {} when no agent config at any level."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "empty")

            result = resolve_test_agent_config(layout, "empty")
            assert result == {}

    def test_project_only(self) -> None:
        """Project-level agent config is returned when no other levels."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(
                layout,
                "proj",
                "project:\n  id: proj\nagent:\n  model: sonnet\n  instructions:\n"
                "    - Follow house style\n",
            )

            result = resolve_test_agent_config(layout, "proj")
            assert result["model"] == "sonnet"
            assert result["instructions"] == ["Follow house style"]

    def test_global_provides_defaults(self) -> None:
        """Global agent config provides defaults when project has none."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(layout, "proj")

            global_cfg = layout.base / "global.yml"
            global_cfg.write_text("agent:\n  model: haiku\n  temperature: 0.5\n", encoding="utf-8")

            result = resolve_test_agent_config(layout, "proj", global_config=global_cfg)
            assert result["model"] == "haiku"
            assert result["temperature"] == 0.5

    def test_project_overrides_global(self) -> None:
        """Project-level config overrides global defaults; other global keys persist."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(
                layout,
                "proj",
                "project:\n  id: proj\nagent:\n  model: opus\n",
            )

            global_cfg = layout.base / "global.yml"
            global_cfg.write_text("agent:\n  model: haiku\n  temperature: 0.5\n", encoding="utf-8")

            result = resolve_test_agent_config(layout, "proj", global_config=global_cfg)
            assert result["model"] == "opus"
            assert result["temperature"] == 0.5

    def test_cli_overrides_all(self) -> None:
        """CLI overrides take highest priority."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(
                layout,
                "proj",
                "project:\n  id: proj\nagent:\n  model: sonnet\n",
            )

            result = resolve_test_agent_config(
                layout, "proj", cli_overrides={"model": "opus", "temperature": 0.1}
            )
            assert result["model"] == "opus"
            assert result["temperature"] == 0.1

    def test_inherit_extends_list(self) -> None:
        """A higher-priority scope with _inherit extends a project list value."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(
                layout,
                "proj",
                "project:\n  id: proj\nagent:\n  instructions:\n    - base rule\n",
            )

            result = resolve_test_agent_config(
                layout,
                "proj",
                cli_overrides={"instructions": ["_inherit", "extra rule"]},
            )
            assert result["instructions"] == ["base rule", "extra rule"]

    def test_project_config_multiple_keys(self) -> None:
        """Project agent config resolves multiple keys correctly."""
        with tempfile.TemporaryDirectory() as td:
            layout = make_layout(Path(td))
            write_test_project(
                layout,
                "proj2",
                "project:\n  id: proj2\nagent:\n  model: sonnet\n  temperature: 0.3\n",
            )

            result = resolve_test_agent_config(layout, "proj2")
            assert result["model"] == "sonnet"
            assert result["temperature"] == 0.3


class TestValidateProjectId:
    """Tests for validate_project_name error messages."""

    def test_error_message_mentions_first_char(self) -> None:
        """Error message describes the first-character requirement."""
        from terok.lib.core.project_model import validate_project_name

        with pytest.raises(SystemExit) as ctx:
            validate_project_name("-bad")
        msg = str(ctx.value)
        assert "must start with a lowercase letter or digit" in msg

    def test_uppercase_rejected(self) -> None:
        """Uppercase letters in project name are rejected."""
        from terok.lib.core.project_model import validate_project_name

        with pytest.raises(SystemExit) as ctx:
            validate_project_name("MyProject")
        assert "Invalid project name" in str(ctx.value)
