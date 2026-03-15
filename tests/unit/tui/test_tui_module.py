# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Smoke tests for the TUI entry module and configuration bridge."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.testmodule_utils import assert_module_callable
from tests.unit.tui.tui_test_helpers import import_app


def test_tui_main_is_callable() -> None:
    """The TUI module exports a callable ``main`` entrypoint."""
    import_app()
    assert_module_callable("terok.tui.app")


@pytest.mark.parametrize(
    ("config_text", "expected"),
    [
        pytest.param(None, False, id="missing-config"),
        pytest.param("tui:\n  default_tmux: true\n", True, id="tmux-enabled"),
    ],
)
def test_tmux_configuration_integration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config_text: str | None,
    expected: bool,
) -> None:
    """The TUI module can read the ``tui.default_tmux`` configuration value."""
    from terok.lib.core.config import get_tui_default_tmux

    monkeypatch.delenv("TEROK_CONFIG_FILE", raising=False)
    if config_text is not None:
        cfg_path = tmp_path / "config.yml"
        cfg_path.write_text(config_text, encoding="utf-8")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(cfg_path))

    assert get_tui_default_tmux() is expected
