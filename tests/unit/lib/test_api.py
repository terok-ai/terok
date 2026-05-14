# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the public [`terok.lib.api`][terok.lib.api] front door.

Covers the two pieces of behaviour the module owns directly:

- [`get_config`][terok.lib.api.get_config] snapshots every path, flag,
  and presentation hint into a frozen [`Config`][terok.lib.api.Config]
  by reading the underlying ``core.config`` / ``core.paths`` helpers
  exactly once each.
- [`get_container_state`][terok.lib.api.get_container_state] delegates
  one-shot state lookups to the runtime driver without dragging
  consumers into ``core.runtime``.

Re-exported facade symbols are not retested here — they are validated by
the tests against their canonical modules.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from terok.lib.api import Config, get_config, get_container_state


class TestGetConfig:
    """``get_config()`` should snapshot each underlying getter exactly once."""

    def test_returns_frozen_config_with_all_fields(self) -> None:
        """Snapshot populates every field by calling each getter exactly once."""
        with (
            patch(
                "terok.lib.api._paths.config_root", return_value=Path("/cfg")
            ) as config_root_mock,
            patch(
                "terok.lib.api._paths.core_state_dir", return_value=Path("/state")
            ) as core_state_dir_mock,
            patch(
                "terok.lib.api._paths.runtime_dir", return_value=Path("/run")
            ) as runtime_dir_mock,
            patch(
                "terok.lib.api._config.archive_dir", return_value=Path("/arch")
            ) as archive_dir_mock,
            patch("terok.lib.api._config.vault_dir", return_value=Path("/vault")) as vault_dir_mock,
            patch(
                "terok.lib.api._config.user_projects_dir", return_value=Path("/proj")
            ) as user_projects_dir_mock,
            patch(
                "terok.lib.api._config.global_config_path", return_value=Path("/cfg/g.yml")
            ) as global_config_path_mock,
            patch(
                "terok.lib.api._config.get_public_host", return_value="1.2.3.4"
            ) as get_public_host_mock,
            patch(
                "terok.lib.api._config.get_shield_bypass_firewall_no_protection",
                return_value=True,
            ) as get_shield_bypass_firewall_no_protection_mock,
            patch(
                "terok.lib.api._config.get_tui_default_tmux", return_value=True
            ) as get_tui_default_tmux_mock,
            patch(
                "terok.lib.api._config.get_tui_external_editor", return_value=False
            ) as get_tui_external_editor_mock,
            patch("terok.lib.api._config.SHIELD_SECURITY_HINT", "HINT"),
        ):
            cfg = get_config()

        assert isinstance(cfg, Config)
        assert cfg.config_root == Path("/cfg")
        assert cfg.core_state_dir == Path("/state")
        assert cfg.runtime_dir == Path("/run")
        assert cfg.archive_dir == Path("/arch")
        assert cfg.vault_dir == Path("/vault")
        assert cfg.user_projects_dir == Path("/proj")
        assert cfg.global_config_path == Path("/cfg/g.yml")
        assert cfg.public_host == "1.2.3.4"
        assert cfg.shield_bypass_firewall_no_protection is True
        assert cfg.tui_default_tmux is True
        assert cfg.tui_external_editor is False
        assert cfg.shield_security_hint == "HINT"

        # ``SHIELD_SECURITY_HINT`` is a constant, not a callable — exclude it.
        for getter_mock in (
            config_root_mock,
            core_state_dir_mock,
            runtime_dir_mock,
            archive_dir_mock,
            vault_dir_mock,
            user_projects_dir_mock,
            global_config_path_mock,
            get_public_host_mock,
            get_shield_bypass_firewall_no_protection_mock,
            get_tui_default_tmux_mock,
            get_tui_external_editor_mock,
        ):
            getter_mock.assert_called_once()

    def test_config_is_immutable(self) -> None:
        """``Config`` is frozen — callers can't mutate the snapshot."""
        import dataclasses

        cfg = get_config()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.public_host = "evil"  # type: ignore[misc]


class TestGetContainerState:
    """One-shot container state lookup must go through the runtime driver."""

    def test_returns_state_from_runtime(self) -> None:
        """Returns whatever ``runtime.container(name).state`` reports."""
        fake_container = MagicMock(state="running")
        fake_runtime = MagicMock()
        fake_runtime.container.return_value = fake_container
        with patch(
            "terok.lib.api._runtime.get_runtime", return_value=fake_runtime
        ) as mock_get_runtime:
            assert get_container_state("proj-cli-42") == "running"
        mock_get_runtime.assert_called_once()
        fake_runtime.container.assert_called_once_with("proj-cli-42")

    def test_returns_none_when_container_absent(self) -> None:
        """Driver reports ``state=None`` for unknown containers — pass it through."""
        fake_container = MagicMock(state=None)
        fake_runtime = MagicMock()
        fake_runtime.container.return_value = fake_container
        with patch(
            "terok.lib.api._runtime.get_runtime", return_value=fake_runtime
        ) as mock_get_runtime:
            assert get_container_state("ghost") is None
        mock_get_runtime.assert_called_once()
        fake_runtime.container.assert_called_once_with("ghost")
