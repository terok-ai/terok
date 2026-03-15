# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for project-state loading with gate staleness information."""

from __future__ import annotations

from unittest import mock

import pytest

from tests.test_utils import make_staleness_info
from tests.testnet import TEST_UPSTREAM_URL
from tests.unit.tui.tui_test_helpers import build_textual_stubs, import_fresh


@pytest.mark.parametrize("security_class", ["online", "gatekeeping"])
def test_staleness_checked_for_online_and_gatekeeping(security_class: str) -> None:
    """Online and gatekeeping projects both query gate staleness during state load."""
    stubs = build_textual_stubs()
    _, _, app = import_fresh(stubs)

    staleness = make_staleness_info(commits_behind=1)
    project = mock.Mock(security_class=security_class, upstream_url=TEST_UPSTREAM_URL)
    state = {"gate": True}
    mock_gate = mock.Mock(
        compare_vs_upstream=mock.Mock(return_value=staleness),
        last_commit=mock.Mock(return_value="abc123"),
    )

    with (
        mock.patch.object(app, "load_project", return_value=project),
        mock.patch.object(app, "get_project_state", return_value=state),
        mock.patch.object(app, "GitGate", return_value=mock_gate),
    ):
        result = app.TerokTUI._load_project_state(mock.Mock(), "proj1")

    mock_gate.compare_vs_upstream.assert_called_once()
    assert result.project_id == "proj1"
    assert result.project == project
    assert result.state == state
    assert result.staleness == staleness
    assert result.error is None
