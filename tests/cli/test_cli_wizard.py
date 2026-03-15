# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the CLI project-wizard alias."""

from __future__ import annotations

import sys
from unittest.mock import patch


def test_project_wizard_dispatch(monkeypatch) -> None:
    """``terok project-wizard`` dispatches to ``run_wizard`` with ``cmd_project_init``."""
    from terok.cli.commands.setup import cmd_project_init
    from terok.cli.main import main

    monkeypatch.setattr(sys, "argv", ["terok", "project-wizard"])
    with patch("terok.cli.commands.project.run_wizard") as mock_wizard:
        main()

    mock_wizard.assert_called_once_with(init_fn=cmd_project_init)
