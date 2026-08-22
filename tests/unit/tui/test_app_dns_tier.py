# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""TerokTUI's threaded shield-state loader that feeds the degraded-DNS warning.

The warning itself lives in
[`dns_tier_warning`][terok.lib.core.task_display.dns_tier_warning] and is
covered by its own unit tests; here we exercise the App-side loader that reads
a task's shield state and recorded DNS tier off disk, without booting a Textual
app.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from terok.tui.app import TerokTUI
from tests.testfs import MOCK_BASE


def test_load_shield_state_threads_state_name_and_recorded_tier() -> None:
    """The happy path returns the shield state's ``.name`` and the launch tier."""
    task = SimpleNamespace(mode="cli", task_id="t1")
    project = SimpleNamespace(tasks_root=Path(MOCK_BASE))
    manager = SimpleNamespace(state=lambda _cname: SimpleNamespace(name="UP"), dns_tier="dnsmasq")

    with (
        mock.patch("terok.tui.app.load_project", return_value=project),
        mock.patch("terok.tui.app.container_name", return_value="c-name"),
        mock.patch("terok.tui.app.ShieldManager", return_value=manager),
    ):
        result = TerokTUI._load_shield_state("proj", task)

    assert result == ("proj", "t1", "UP", "dnsmasq")


def test_load_shield_state_swallows_errors_into_a_null_result() -> None:
    """Any failure degrades to a tierless result rather than crashing the worker."""
    task = SimpleNamespace(mode="cli", task_id="t1")

    with mock.patch("terok.tui.app.load_project", side_effect=RuntimeError("boom")):
        result = TerokTUI._load_shield_state("proj", task)

    assert result == ("proj", "t1", None, None)
