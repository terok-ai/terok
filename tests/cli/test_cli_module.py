# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Smoke tests for the CLI entry module."""

from testmodule_utils import assert_module_callable


def test_cli_main_is_callable() -> None:
    """The CLI module exports a callable ``main`` entrypoint."""
    assert_module_callable("terok.cli.main")
