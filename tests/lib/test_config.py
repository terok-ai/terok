# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for core config helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from terok.lib.core import config as cfg


@pytest.fixture(autouse=True)
def reset_experimental() -> None:
    """Reset the module-global experimental flag around each test."""
    cfg.set_experimental(False)
    yield
    cfg.set_experimental(False)


def write_config(tmp_path: Path, content: str) -> Path:
    """Write a temporary config file and return its path."""
    path = tmp_path / "config.yml"
    path.write_text(content, encoding="utf-8")
    return path


def test_global_config_search_paths_respects_env_override(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(config_path))
    assert cfg.global_config_search_paths() == [config_path.expanduser().resolve()]


def test_global_config_path_prefers_xdg(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "terok" / "config.yml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("ui:\n  base_port: 7000\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cfg.global_config_path() == config_file.resolve()


@pytest.mark.parametrize(
    ("env_var", "config_text", "resolver", "expected_name"),
    [
        ("TEROK_STATE_DIR", None, cfg.state_root, "state"),
        ("TEROK_CONFIG_FILE", "paths:\n  state_root: {path}\n", cfg.state_root, "state"),
        (
            "TEROK_CONFIG_FILE",
            "paths:\n  user_projects_root: {path}\n",
            cfg.user_projects_root,
            "projects",
        ),
        (
            "TEROK_CONFIG_FILE",
            "ui:\n  base_port: 8123\nenvs:\n  base_dir: {path}\n",
            cfg.get_envs_base_dir,
            "envs",
        ),
    ],
    ids=["state-env", "state-config", "projects-config", "envs-config"],
)
def test_path_resolution(
    monkeypatch,
    tmp_path: Path,
    env_var: str,
    config_text: str | None,
    resolver,
    expected_name: str,
) -> None:
    expected_path = tmp_path / expected_name
    if config_text is None:
        monkeypatch.setenv(env_var, str(expected_path))
    else:
        monkeypatch.setenv(
            env_var, str(write_config(tmp_path, config_text.format(path=expected_path)))
        )
    assert resolver() == expected_path.resolve()


def test_ui_base_port_is_read_from_global_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, "ui:\n  base_port: 8123\n")))
    assert cfg.get_ui_base_port() == 8123


@pytest.mark.parametrize(
    ("config_text", "expected"),
    [
        ("tui:\n  default_tmux: true\n", True),
        ("", False),
        ("tui:\n  default_tmux: false\n", False),
    ],
    ids=["true", "default-false", "explicit-false"],
)
def test_tui_default_tmux(monkeypatch, tmp_path: Path, config_text: str, expected: bool) -> None:
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(write_config(tmp_path, config_text)))
    assert cfg.get_tui_default_tmux() is expected


def test_experimental_flag_roundtrip() -> None:
    assert not cfg.is_experimental()
    cfg.set_experimental(True)
    assert cfg.is_experimental()
    cfg.set_experimental(False)
    assert not cfg.is_experimental()


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        ({}, False),
        ({"bypass_firewall_no_protection": True}, True),
        ({"bypass_firewall_no_protection": False}, False),
    ],
    ids=["default-false", "enabled", "explicit-false"],
)
def test_get_shield_bypass_firewall_no_protection(section: dict[str, bool], expected: bool) -> None:
    with patch.object(cfg, "get_global_section", return_value=section) as mock_section:
        assert cfg.get_shield_bypass_firewall_no_protection() is expected
    mock_section.assert_called_once_with("shield")
