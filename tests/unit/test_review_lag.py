# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for review-lag detection: gate branches ahead of an open MR/PR.

The forge is always mocked (a unit suite must not talk to GitHub/GitLab);
the gate is a stub exposing exactly the two calls the domain logic uses
(``branch_heads`` / ``compare_vs_upstream``) — the git semantics behind
them are terok-sandbox's tested territory.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from terok.lib.domain import review_lag
from terok.lib.domain.review_lag import (
    OpenReview,
    ReviewLagEntry,
    _forge_command,
    _split_forge_url,
    fetch_open_reviews,
    format_review_status,
    review_lag_entries,
    write_review_status_files,
)
from tests.test_utils import make_staleness_info

# ---------------------------------------------------------------------------
# Forge URL handling
# ---------------------------------------------------------------------------


class TestForgeUrls:
    """HTTPS and scp-like remotes resolve to the right CLI; junk to None."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://github.com/o/r.git", ("github.com", "o/r")),
            ("https://gitlab.com/group/sub/r", ("gitlab.com", "group/sub/r")),
            ("git@github.com:o/r.git", ("github.com", "o/r")),
            ("git@gitlab.hzdr.de:group/r.git", ("gitlab.hzdr.de", "group/r")),
            ("ssh://git@gitlab.com/o/r.git", ("gitlab.com", "o/r")),
            ("/local/path/repo.git", ("", "")),
        ],
    )
    def test_split_forge_url(self, url: str, expected: tuple[str, str]) -> None:
        assert _split_forge_url(url) == expected

    def test_github_uses_gh(self) -> None:
        command = _forge_command("https://github.com/o/r.git")
        assert command is not None
        assert command[0] == "gh"
        assert "repos/o/r/pulls?state=open" in command[-1]

    def test_gitlab_uses_glab_with_encoded_path(self) -> None:
        command = _forge_command("git@gitlab.hzdr.de:group/sub/r.git")
        assert command is not None
        assert command[0] == "glab"
        assert command[command.index("--hostname") + 1] == "gitlab.hzdr.de"
        assert "projects/group%2Fsub%2Fr/merge_requests?state=opened" in command[-1]

    def test_non_forge_url_yields_no_command(self) -> None:
        assert _forge_command("/srv/git/local.git") is None


# ---------------------------------------------------------------------------
# fetch_open_reviews
# ---------------------------------------------------------------------------

_GITHUB_PAYLOAD = [{"number": 7, "head": {"ref": "feat/x", "sha": "abc123"}}]
_GITLAB_PAYLOAD = [{"iid": 42, "source_branch": "border-wave-1", "sha": "def456"}]


class TestFetchOpenReviews:
    """One API call, honest failure semantics: None means 'could not ask'."""

    @pytest.fixture(autouse=True)
    def _forge_tools(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Resolve fixture tools, not GitHub/GitLab clients installed on the test host."""
        tools = tmp_path / "bin"
        tools.mkdir()
        for name in ("gh", "glab"):
            (tools / name).touch(mode=0o755)
        monkeypatch.setenv("PATH", str(tools))

    def _completed(self, payload: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")

    def test_github_reviews_parse(self) -> None:
        with mock.patch.object(
            review_lag.subprocess, "run", return_value=self._completed(_GITHUB_PAYLOAD)
        ):
            reviews = fetch_open_reviews("https://github.com/o/r.git")
        assert reviews == [OpenReview(number=7, source_branch="feat/x", sha="abc123", prefix="#")]

    def test_gitlab_reviews_parse(self) -> None:
        with mock.patch.object(
            review_lag.subprocess, "run", return_value=self._completed(_GITLAB_PAYLOAD)
        ):
            reviews = fetch_open_reviews("https://gitlab.com/o/r.git")
        assert reviews == [
            OpenReview(number=42, source_branch="border-wave-1", sha="def456", prefix="!")
        ]

    def test_cli_failure_returns_none(self) -> None:
        error = subprocess.CalledProcessError(1, ["gh"], stderr="not logged in")
        with mock.patch.object(review_lag.subprocess, "run", side_effect=error):
            assert fetch_open_reviews("https://github.com/o/r.git") is None

    def test_missing_cli_returns_none(self) -> None:
        with mock.patch.object(review_lag.subprocess, "run", side_effect=FileNotFoundError):
            assert fetch_open_reviews("https://github.com/o/r.git") is None

    def test_garbage_payload_returns_none(self) -> None:
        with mock.patch.object(
            review_lag.subprocess, "run", return_value=self._completed(["nonsense"])
        ):
            assert fetch_open_reviews("https://github.com/o/r.git") is None

    def test_non_forge_url_returns_none(self) -> None:
        assert fetch_open_reviews("/srv/git/local.git") is None


# ---------------------------------------------------------------------------
# review_lag_entries
# ---------------------------------------------------------------------------


def _fake_gate(heads: dict[str, str], ahead: dict[str, int]) -> SimpleNamespace:
    """Stub gate: fixed branch heads, per-branch ahead counts."""

    def compare(branch: str):
        return make_staleness_info(
            branch=branch, is_stale=False, commits_behind=0, commits_ahead=ahead.get(branch, 0)
        )

    return SimpleNamespace(branch_heads=lambda: heads, compare_vs_upstream=compare)


def _review(branch: str, sha: str, number: int = 42) -> OpenReview:
    return OpenReview(number=number, source_branch=branch, sha=sha, prefix="!")


class TestReviewLagEntries:
    """Only gate-ahead branches with an open review warn."""

    def test_gate_ahead_of_review_warns(self) -> None:
        gate = _fake_gate({"feat/x": "new"}, {"feat/x": 3})
        entries = review_lag_entries(gate, [_review("feat/x", "old")])
        assert [str(entry) for entry in entries] == ["!42 feat/x +3"]

    def test_in_sync_branch_is_silent(self) -> None:
        gate = _fake_gate({"feat/x": "same"}, {})
        assert review_lag_entries(gate, [_review("feat/x", "same")]) == []

    def test_review_without_gate_branch_is_silent(self) -> None:
        gate = _fake_gate({"master": "aaa"}, {})
        assert review_lag_entries(gate, [_review("feat/elsewhere", "bbb")]) == []

    def test_gate_merely_behind_forge_is_silent(self) -> None:
        """Operator pushed fixups directly — gate stale, but nothing unpushed."""
        gate = _fake_gate({"feat/x": "old"}, {"feat/x": 0})
        assert review_lag_entries(gate, [_review("feat/x", "newer")]) == []

    def test_comparison_error_is_silent(self) -> None:
        gate = SimpleNamespace(
            branch_heads=lambda: {"feat/x": "new"},
            compare_vs_upstream=lambda branch: make_staleness_info(
                branch=branch, error="gate on fire", commits_ahead=None
            ),
        )
        assert review_lag_entries(gate, [_review("feat/x", "old")]) == []

    def test_entries_sorted_by_branch(self) -> None:
        gate = _fake_gate({"b": "x1", "a": "x2"}, {"a": 1, "b": 2})
        entries = review_lag_entries(
            gate, [_review("b", "y1", number=2), _review("a", "y2", number=1)]
        )
        assert [entry.branch for entry in entries] == ["a", "b"]


# ---------------------------------------------------------------------------
# Surfacing
# ---------------------------------------------------------------------------


class TestSurfacing:
    """The review-status file is written atomically, cleared when empty."""

    def test_format_lines(self) -> None:
        entries = [
            ReviewLagEntry(branch="feat/x", review=_review("feat/x", "s"), commits_ahead=3),
        ]
        assert format_review_status(entries) == "!42 feat/x +3\n"
        assert format_review_status([]) == ""

    def test_write_and_clear(self, tmp_path: Path) -> None:
        dirs = [tmp_path / "t1" / "agent-config", tmp_path / "t2" / "agent-config"]
        write_review_status_files(dirs, "!42 feat/x +3\n")
        for config_dir in dirs:
            assert (
                config_dir.parent / "agent-inputs" / "review-status"
            ).read_text() == "!42 feat/x +3\n"

        write_review_status_files(dirs, "")
        for config_dir in dirs:
            assert (config_dir.parent / "agent-inputs" / "review-status").read_text() == ""

    def test_unwritable_dir_is_skipped(self, tmp_path: Path) -> None:
        good = tmp_path / "good" / "agent-config"
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        blocked.chmod(0o555)
        try:
            write_review_status_files([blocked / "agent-config", good], "warning\n")
        finally:
            blocked.chmod(0o755)
        assert (good.parent / "agent-inputs" / "review-status").read_text() == "warning\n"


# ---------------------------------------------------------------------------
# refresh_review_lag orchestration
# ---------------------------------------------------------------------------


def _project(**overrides: object) -> SimpleNamespace:
    defaults = {
        "name": "proj",
        "security_class": "gatekeeping",
        "upstream_url": "https://gitlab.com/o/r.git",
        "review_lag_enabled": True,
        "review_lag_surface_in_tasks": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestRefreshReviewLag:
    """The single TUI entry point: gated by config, honest about failures."""

    def test_disabled_returns_empty_without_forge_query(self) -> None:
        with mock.patch.object(review_lag, "fetch_open_reviews") as fetch:
            assert review_lag.refresh_review_lag(_project(review_lag_enabled=False)) == []
        fetch.assert_not_called()

    def test_online_project_returns_empty(self) -> None:
        assert review_lag.refresh_review_lag(_project(security_class="online")) == []

    def test_forge_failure_propagates_none(self) -> None:
        with mock.patch.object(review_lag, "fetch_open_reviews", return_value=None):
            assert review_lag.refresh_review_lag(_project()) is None

    def test_entries_surface_into_task_dirs(self, tmp_path: Path) -> None:
        gate = _fake_gate({"feat/x": "new"}, {"feat/x": 2})
        config_dir = tmp_path / "42" / "agent-config"
        with (
            mock.patch.object(
                review_lag, "fetch_open_reviews", return_value=[_review("feat/x", "old")]
            ),
            mock.patch("terok.lib.domain.project.make_git_gate", return_value=gate),
            mock.patch.object(review_lag, "tasks_meta_dir", return_value=tmp_path / "meta"),
            mock.patch.object(review_lag, "iter_task_ids", return_value=iter(["42"])),
            mock.patch.object(review_lag, "agent_config_dir", return_value=config_dir),
        ):
            entries = review_lag.refresh_review_lag(_project())
        assert [str(entry) for entry in entries] == ["!42 feat/x +2"]
        assert (
            config_dir.parent / "agent-inputs" / "review-status"
        ).read_text() == "!42 feat/x +2\n"

    def test_surfacing_disabled_skips_files(self, tmp_path: Path) -> None:
        gate = _fake_gate({"feat/x": "new"}, {"feat/x": 2})
        with (
            mock.patch.object(
                review_lag, "fetch_open_reviews", return_value=[_review("feat/x", "old")]
            ),
            mock.patch("terok.lib.domain.project.make_git_gate", return_value=gate),
            mock.patch.object(review_lag, "tasks_meta_dir") as meta_dir,
        ):
            entries = review_lag.refresh_review_lag(_project(review_lag_surface_in_tasks=False))
        assert len(entries) == 1
        meta_dir.assert_not_called()
