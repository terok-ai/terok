# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`terok.tui.tmux_session`][] — the terok-managed tmux helpers.

All tmux invocations are faked at the ``subprocess.run`` seam: the tests
pin the exact command lines issued and the parsing of their output, never
touching a real tmux server (the autouse conftest fixture also strips
``TMUX``/``TEROK_TMUX`` from the environment).
"""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from terok.tui import tmux_session


class FakeTmux:
    """Record tmux command lines; reply with canned stdout keyed by argv substring.

    ``outputs`` maps a distinctive fragment (a subcommand like
    ``list-windows`` or a format string like ``#{pane_pid}``) to the
    stdout to return; the first key found anywhere in the argv wins.
    """

    def __init__(self, outputs: dict[str, str] | None = None, fail: bool = False) -> None:
        self.outputs = outputs or {}
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_kwargs: object) -> SimpleNamespace:
        self.calls.append(list(argv))
        if self.fail:
            return SimpleNamespace(returncode=1, stdout="")
        for key, out in self.outputs.items():
            if any(key in arg for arg in argv[1:]):
                return SimpleNamespace(returncode=0, stdout=out)
        return SimpleNamespace(returncode=0, stdout="")

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeTmux:
        """Patch ``subprocess.run`` inside tmux_session with this fake."""
        monkeypatch.setattr(tmux_session.subprocess, "run", self)
        return self


@pytest.fixture
def inside_terok_tmux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate running inside a terok-managed tmux pane."""
    monkeypatch.setenv("TMUX", "/tmp/terok-testing/tmux-1000/default,42,0")
    monkeypatch.setenv(tmux_session.TEROK_TMUX_ENV, "1")
    monkeypatch.setenv("TMUX_PANE", "%7")


def _root_command_outputs(windows: str) -> dict[str, str]:
    """Canned probes for "this process is the pane's root command"."""
    return {"#{pane_pid}": f"{os.getpid()}\n", "#{session_windows}": windows}


class TestIsTerokTmux:
    """The marker only fires inside tmux *and* with the terok session env var."""

    def test_true_inside_marked_session(self, inside_terok_tmux: None) -> None:
        """Both TMUX and TEROK_TMUX set ⇒ terok-managed."""
        assert tmux_session.is_terok_tmux() is True

    def test_false_in_users_own_tmux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A custom user tmux (no marker) never gets terok behaviour."""
        monkeypatch.setenv("TMUX", "/tmp/terok-testing/tmux-1000/default,42,0")
        assert tmux_session.is_terok_tmux() is False

    def test_false_outside_tmux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stale TEROK_TMUX without TMUX (e.g. inherited env) is not enough."""
        monkeypatch.setenv(tmux_session.TEROK_TMUX_ENV, "1")
        assert tmux_session.is_terok_tmux() is False


class TestSessionQueries:
    """``session_exists`` / ``find_main_window`` target the session by exact name."""

    def test_session_exists_uses_exact_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``=terok`` prevents prefix-matching a user session like ``terok2``."""
        fake = FakeTmux().install(monkeypatch)
        assert tmux_session.session_exists() is True
        assert fake.calls == [["tmux", "has-session", "-t", "=terok"]]

    def test_session_exists_false_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failing has-session (no server, no session) reads as absent."""
        FakeTmux(fail=True).install(monkeypatch)
        assert tmux_session.session_exists() is False

    def test_find_main_window_returns_stamped_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The window whose @terok-main expands to 1 wins; unstamped lines don't."""
        FakeTmux(outputs={"list-windows": "@1 \n@3 1\n@4 \n"}).install(monkeypatch)
        assert tmux_session.find_main_window() == "@3"

    def test_find_main_window_none_when_unstamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No stamped window ⇒ None (the TUI window was closed)."""
        FakeTmux(outputs={"list-windows": "@1 \n@2 \n"}).install(monkeypatch)
        assert tmux_session.find_main_window() is None


class TestStampMainWindow:
    """The TUI stamps its own window and clears stale stamps elsewhere."""

    def test_noop_outside_terok_tmux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No tmux calls at all outside a terok-managed session."""
        fake = FakeTmux().install(monkeypatch)
        tmux_session.stamp_main_window()
        assert fake.calls == []

    def test_stamps_own_window_and_clears_stale(
        self, inside_terok_tmux: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Own window gets the stamp; a stale stamp on another window is unset."""
        fake = FakeTmux(outputs={"list-windows": "@1 1\n@2 \n", "#{window_id}": "@2\n"}).install(
            monkeypatch
        )
        tmux_session.stamp_main_window()
        assert ["tmux", "set-option", "-w", "-t", "@1", "-u", "@terok-main"] in fake.calls
        assert fake.calls[-1] == ["tmux", "set-option", "-w", "-t", "@2", "@terok-main", "1"]


class TestSessionMarkerArgs:
    """The ``-e`` marker is only offered on a tmux new enough to accept it."""

    @pytest.mark.parametrize(
        ("version_line", "expected"),
        [
            ("tmux 3.6b\n", ("-e", "TEROK_TMUX=1")),
            ("tmux 3.2a\n", ("-e", "TEROK_TMUX=1")),
            ("tmux next-3.4\n", ("-e", "TEROK_TMUX=1")),
            ("tmux 3.1c\n", ()),
            ("tmux 2.9\n", ()),
        ],
        ids=["modern", "exactly-3.2", "next-prefix", "too-old-minor", "too-old-major"],
    )
    def test_marker_follows_tmux_version(
        self, monkeypatch: pytest.MonkeyPatch, version_line: str, expected: tuple[str, ...]
    ) -> None:
        """tmux >= 3.2 gets the marker; anything older (which rejects -e) gets none."""
        FakeTmux(outputs={"-V": version_line}).install(monkeypatch)
        assert tmux_session.session_marker_args() == expected

    def test_no_marker_when_probe_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unprobeable tmux is treated as too old — degrade, don't break."""
        FakeTmux(fail=True).install(monkeypatch)
        assert tmux_session.session_marker_args() == ()


class TestReviveWindowArgs:
    """A revived TUI window is inserted first on tmux >= 3.2, appended on older."""

    @pytest.mark.parametrize(
        ("version_line", "expected"),
        [
            ("tmux 3.7b\n", ["-b", "-t", "=terok:^"]),
            ("tmux 3.2a\n", ["-b", "-t", "=terok:^"]),
            ("tmux 3.1c\n", ["-t", "=terok:"]),
        ],
        ids=["modern", "exactly-3.2", "too-old"],
    )
    def test_placement_follows_tmux_version(
        self, monkeypatch: pytest.MonkeyPatch, version_line: str, expected: list[str]
    ) -> None:
        """tmux >= 3.2 inserts before the first window; older appends at the next free index."""
        FakeTmux(outputs={"-V": version_line}).install(monkeypatch)
        assert tmux_session.revive_window_args() == expected

    def test_appends_when_probe_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unprobeable tmux is treated as too old and gets the append form."""
        FakeTmux(fail=True).install(monkeypatch)
        assert tmux_session.revive_window_args() == ["-t", "=terok:"]


class TestLoginWindows:
    """Login windows are stamped per container and found for reuse."""

    def test_find_login_window_matches_container(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The window stamped with the container name wins; others don't."""
        fake = FakeTmux(outputs={"list-windows": "@1 \n@3 terok-p1-t1\n@4 terok-p1-t2\n"}).install(
            monkeypatch
        )
        assert tmux_session.find_login_window("terok-p1-t2") == "@4"
        assert fake.calls == [
            ["tmux", "list-windows", "-F", "#{window_id} #{@terok-login}"],
        ]

    def test_find_login_window_none_when_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No window logged into the container ⇒ None (open a fresh one)."""
        FakeTmux(outputs={"list-windows": "@1 \n@3 terok-p1-t1\n"}).install(monkeypatch)
        assert tmux_session.find_login_window("terok-p2-t9") is None

    def test_stamp_login_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The stamp is a window option carrying the container name."""
        fake = FakeTmux().install(monkeypatch)
        tmux_session.stamp_login_window("@7", "terok-p1-t1")
        assert fake.calls == [
            ["tmux", "set-option", "-w", "-t", "@7", "@terok-login", "terok-p1-t1"],
        ]

    @pytest.mark.parametrize(
        ("fail", "expected"), [(False, True), (True, False)], ids=["accepted", "window-gone"]
    )
    def test_select_window(
        self, monkeypatch: pytest.MonkeyPatch, fail: bool, expected: bool
    ) -> None:
        """Selecting reports whether tmux accepted the window id."""
        fake = FakeTmux(fail=fail).install(monkeypatch)
        assert tmux_session.select_window("@7") is expected
        assert fake.calls == [["tmux", "select-window", "-t", "@7"]]


class TestQuitLandsInOtherWindow:
    """Quit guidance fires only for the root command with sibling windows."""

    def test_counts_sibling_windows_for_root_command(
        self, inside_terok_tmux: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Root command (pane_pid == our pid) with 3 windows ⇒ 2 siblings."""
        FakeTmux(outputs=_root_command_outputs("3\n")).install(monkeypatch)
        assert tmux_session.quit_lands_in_other_window() == 2

    def test_zero_when_not_root_command(
        self, inside_terok_tmux: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Launched from a shell inside the pane ⇒ the shell survives, no guidance."""
        FakeTmux(outputs={"#{pane_pid}": "12345\n", "#{session_windows}": "3\n"}).install(
            monkeypatch
        )
        assert tmux_session.quit_lands_in_other_window() == 0

    def test_zero_for_last_window(
        self, inside_terok_tmux: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only window left ⇒ quitting ends the session, back to the terminal."""
        FakeTmux(outputs=_root_command_outputs("1\n")).install(monkeypatch)
        assert tmux_session.quit_lands_in_other_window() == 0

    def test_zero_outside_terok_tmux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Outside a terok tmux nothing is probed and the answer is 0."""
        fake = FakeTmux().install(monkeypatch)
        assert tmux_session.quit_lands_in_other_window() == 0
        assert fake.calls == []


class TestFlashExitHint:
    """The status-line breadcrumb fires only when the user lands in another window."""

    def test_fires_display_message_with_delay(
        self, inside_terok_tmux: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When quitting drops into a sibling window, the hint is flashed."""
        fake = FakeTmux(outputs=_root_command_outputs("2\n")).install(monkeypatch)
        tmux_session.flash_exit_hint()
        hint = fake.calls[-1]
        assert hint[:3] == ["tmux", "display-message", "-d"]
        assert "Ctrl-b d" in hint[-1]

    def test_noop_when_quit_is_unsurprising(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No hint outside a terok tmux."""
        fake = FakeTmux().install(monkeypatch)
        tmux_session.flash_exit_hint()
        assert fake.calls == []


class TestTmuxRunner:
    """The quiet runner degrades every failure mode to None."""

    def test_missing_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FileNotFoundError (no tmux installed) ⇒ None."""

        def run(*_args: object, **_kwargs: object) -> SimpleNamespace:
            raise FileNotFoundError("tmux")

        monkeypatch.setattr(tmux_session.subprocess, "run", run)
        assert tmux_session._tmux("has-session") is None

    def test_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A hung server ⇒ None instead of a TUI stall."""

        def run(*_args: object, **_kwargs: object) -> SimpleNamespace:
            raise subprocess.TimeoutExpired(cmd="tmux", timeout=5)

        monkeypatch.setattr(tmux_session.subprocess, "run", run)
        assert tmux_session._tmux("has-session") is None
