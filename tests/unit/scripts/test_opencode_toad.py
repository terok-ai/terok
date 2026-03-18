# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the unified opencode-toad wrapper script."""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def opencode_toad_script_path() -> Path:
    """Return the path to the opencode-toad wrapper script."""
    return REPO_ROOT / "src" / "terok" / "resources" / "scripts" / "opencode-toad"


def load_opencode_toad_module() -> ModuleType:
    """Load the wrapper script as a Python module."""
    script_path = opencode_toad_script_path()
    loader = importlib.machinery.SourceFileLoader("opencode_toad", str(script_path))
    spec = importlib.util.spec_from_file_location("opencode_toad", script_path, loader=loader)
    if spec is None:
        raise ImportError(f"Could not load spec from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["opencode_toad"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def toad_module() -> Iterator[ModuleType]:
    """Load the opencode-toad script as an isolated module for one test."""
    sys.modules.pop("opencode_toad", None)
    mod = load_opencode_toad_module()
    yield mod
    sys.modules.pop("opencode_toad", None)


# -- Script validity ----------------------------------------------------------


class TestScript:
    """Tests for the opencode-toad wrapper script itself."""

    def test_script_is_valid_python(self) -> None:
        """Verify that the script is syntactically valid Python."""
        ast.parse(opencode_toad_script_path().read_text(encoding="utf-8"))

    def test_script_has_shebang(self) -> None:
        """Verify the script has a proper Python shebang."""
        content = opencode_toad_script_path().read_text(encoding="utf-8")
        assert content.startswith("#!/usr/bin/env python3")


# -- Theme management --------------------------------------------------------


class TestSetToadTheme:
    """Tests for _set_toad_theme."""

    def test_creates_config_from_scratch(self, toad_module) -> None:
        """Creates toad config when none exists."""
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "toad" / "toad.json"
            with patch.object(toad_module, "TOAD_CONFIG", config_path):
                toad_module._set_toad_theme("dracula")
            result = json.loads(config_path.read_text())
            assert result["ui"]["theme"] == "dracula"

    def test_updates_existing_config(self, toad_module) -> None:
        """Updates theme in existing config, preserving other keys."""
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "toad.json"
            config_path.write_text(json.dumps({"ui": {"theme": "monokai"}, "shell": {"x": 1}}))
            with patch.object(toad_module, "TOAD_CONFIG", config_path):
                toad_module._set_toad_theme("dracula")
            result = json.loads(config_path.read_text())
            assert result["ui"]["theme"] == "dracula"
            assert result["shell"]["x"] == 1

    def test_recovers_from_malformed_json(self, toad_module) -> None:
        """Recovers gracefully when config contains invalid JSON."""
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "toad.json"
            config_path.write_text("{not valid json!!!")
            with patch.object(toad_module, "TOAD_CONFIG", config_path):
                toad_module._set_toad_theme("dracula")
            result = json.loads(config_path.read_text())
            assert result["ui"]["theme"] == "dracula"

    def test_skips_write_when_already_set(self, toad_module) -> None:
        """Does not rewrite config when theme already matches."""
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "toad.json"
            config_path.write_text(json.dumps({"ui": {"theme": "dracula"}}))
            mtime = config_path.stat().st_mtime
            with patch.object(toad_module, "TOAD_CONFIG", config_path):
                toad_module._set_toad_theme("dracula")
            assert config_path.stat().st_mtime == mtime


# -- Entry point (parametrized over both providers) ---------------------------


class TestMain:
    """Tests for the opencode-toad main entry point."""

    def test_exits_when_toad_missing(self, toad_module) -> None:
        """Exits with error when toad is not on PATH."""
        with (
            patch("shutil.which", return_value=None),
            patch("sys.argv", ["blablatoad"]),
        ):
            with pytest.raises(SystemExit, match="1"):
                toad_module.main()

    @pytest.mark.parametrize(
        ("prog", "expected_acp"),
        [("blablatoad", "blablador-acp"), ("kisskitoad", "kisski-acp")],
    )
    def test_execs_toad_acp(self, toad_module, prog: str, expected_acp: str) -> None:
        """Calls os.execvp with the correct ACP agent for each wrapper name."""
        with (
            patch("shutil.which", return_value="/usr/bin/toad"),
            patch.object(toad_module, "_set_toad_theme"),
            patch("os.execvp") as mock_exec,
            patch("sys.argv", [prog]),
        ):
            toad_module.main()
        mock_exec.assert_called_once_with("/usr/bin/toad", ["/usr/bin/toad", "acp", expected_acp])

    def test_forwards_extra_args(self, toad_module) -> None:
        """Extra CLI args are forwarded to toad."""
        with (
            patch("shutil.which", return_value="/usr/bin/toad"),
            patch.object(toad_module, "_set_toad_theme"),
            patch("os.execvp") as mock_exec,
            patch("sys.argv", ["blablatoad", "--title", "Test"]),
        ):
            toad_module.main()
        mock_exec.assert_called_once_with(
            "/usr/bin/toad", ["/usr/bin/toad", "acp", "blablador-acp", "--title", "Test"]
        )

    def test_unknown_wrapper_exits(self, toad_module) -> None:
        """Unknown wrapper name exits with error."""
        with (
            patch("shutil.which", return_value="/usr/bin/toad"),
            patch("sys.argv", ["unknowntoad"]),
        ):
            with pytest.raises(SystemExit, match="1"):
                toad_module.main()
