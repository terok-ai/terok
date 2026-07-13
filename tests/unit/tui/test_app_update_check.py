# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the live-upgrade detection and in-place restart flow.

A ``pip``/``pipx`` upgrade while the TUI is open only changes what is on
disk; the running process keeps its imported code.  The TUI probes the
on-disk version periodically, offers a restart when it differs, and
``_run_tui`` re-execs the process in place when the offer is accepted.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from terok.lib.core import version as version_mod
from terok.tui import app as app_mod
from terok.tui.app import _RESTART_EXIT_RESULT, TerokTUI
from terok.tui.screens import UpdateRestartScreen


class TestInstalledDistVersion:
    """The probe asks a fresh interpreter and degrades every failure to None."""

    def _patch_run(self, monkeypatch: pytest.MonkeyPatch, **result_attrs: object) -> None:
        monkeypatch.setattr(
            version_mod.subprocess,
            "run",
            lambda *_a, **_kw: SimpleNamespace(**result_attrs),
        )

    def test_returns_stripped_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A successful probe yields the dist version without the trailing newline."""
        self._patch_run(monkeypatch, returncode=0, stdout="0.9.0\n")
        assert version_mod.installed_dist_version() == "0.9.0"

    def test_none_on_probe_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-zero exit (package missing, interpreter gone mid-upgrade) ⇒ None."""
        self._patch_run(monkeypatch, returncode=1, stdout="")
        assert version_mod.installed_dist_version() is None

    def test_none_on_empty_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Zero exit but empty stdout still reads as unknown."""
        self._patch_run(monkeypatch, returncode=0, stdout="\n")
        assert version_mod.installed_dist_version() is None

    def test_none_on_oserror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing interpreter raises OSError; the probe answers None."""

        def boom(*_a: object, **_kw: object) -> None:
            raise OSError("gone")

        monkeypatch.setattr(version_mod.subprocess, "run", boom)
        assert version_mod.installed_dist_version() is None


class TestProbeInstalledVersion:
    """The worker-thread comparison only escalates a genuinely new on-disk version."""

    def _probe(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        running: str = "0.8.0",
        installed: str | None,
        already_offered: str | None = None,
    ) -> SimpleNamespace:
        monkeypatch.setattr(app_mod, "_installed_dist_version", lambda: installed)
        monkeypatch.setattr(app_mod, "_get_version_info", lambda: (running, None))
        stub = SimpleNamespace(
            _update_offered_version=already_offered,
            call_from_thread=MagicMock(),
            _offer_update_restart=object(),
        )
        TerokTUI._probe_installed_version(stub)
        return stub

    def test_new_version_offers_restart(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A different on-disk version hops to the UI thread with both versions."""
        stub = self._probe(monkeypatch, installed="0.9.0")
        stub.call_from_thread.assert_called_once_with(stub._offer_update_restart, "0.8.0", "0.9.0")

    def test_same_version_is_silent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Disk matches the running version ⇒ nothing to offer."""
        stub = self._probe(monkeypatch, installed="0.8.0")
        stub.call_from_thread.assert_not_called()

    def test_failed_probe_is_silent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unreadable disk version never produces an offer."""
        stub = self._probe(monkeypatch, installed=None)
        stub.call_from_thread.assert_not_called()

    def test_dismissed_version_not_reoffered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A version the operator already dismissed stays dismissed."""
        stub = self._probe(monkeypatch, installed="0.9.0", already_offered="0.9.0")
        stub.call_from_thread.assert_not_called()


class TestOfferUpdateRestart:
    """The offer pushes the modal once per on-disk version."""

    def test_pushes_modal_and_records_version(self) -> None:
        """The modal carries both versions; the offer is recorded for dedup."""
        stub = SimpleNamespace(
            _update_offered_version=None,
            push_screen=MagicMock(),
            _on_update_restart_choice=object(),
        )
        TerokTUI._offer_update_restart(stub, "0.8.0", "0.9.0")
        assert stub._update_offered_version == "0.9.0"
        screen, callback = stub.push_screen.call_args[0]
        assert isinstance(screen, UpdateRestartScreen)
        assert callback is stub._on_update_restart_choice

    @pytest.mark.asyncio
    async def test_accepting_restarts_via_action_quit(self) -> None:
        """``r`` routes into the restart-flavoured quit."""
        stub = SimpleNamespace(action_quit=AsyncMock())
        await TerokTUI._on_update_restart_choice(stub, True)
        stub.action_quit.assert_awaited_once_with(restart=True)

    @pytest.mark.asyncio
    async def test_dismissing_keeps_running(self) -> None:
        """Any other key keeps the current instance alive."""
        stub = SimpleNamespace(action_quit=AsyncMock())
        await TerokTUI._on_update_restart_choice(stub, False)
        await TerokTUI._on_update_restart_choice(stub, None)
        stub.action_quit.assert_not_awaited()


class TestUpdateRestartScreen:
    """The modal restarts only on ``r``."""

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            pytest.param("r", True, id="r-restarts"),
            pytest.param("x", False, id="other-key-dismisses"),
        ],
    )
    def test_key_mapping(self, key: str, expected: bool) -> None:
        """``r`` confirms the restart; anything else dismisses the offer."""
        screen = UpdateRestartScreen.__new__(UpdateRestartScreen)
        screen.dismiss = MagicMock()
        event = SimpleNamespace(key=key, stop=MagicMock())
        UpdateRestartScreen.on_key(screen, event)
        event.stop.assert_called_once()
        screen.dismiss.assert_called_once_with(expected)


class TestRunTui:
    """``_run_tui`` re-execs in place only on the restart sentinel."""

    def test_restart_sentinel_re_execs_entry_point(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The freshly resolved ``terok-tui`` replaces the process, flags preserved."""
        import shutil

        monkeypatch.setattr(
            app_mod, "TerokTUI", lambda: SimpleNamespace(run=lambda: _RESTART_EXIT_RESULT)
        )
        monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/local/bin/terok-tui")
        execs: list[tuple[str, list[str]]] = []
        monkeypatch.setattr(app_mod.os, "execv", lambda f, argv: execs.append((f, argv)))

        app_mod._run_tui(("--experimental",))

        assert execs == [
            ("/usr/local/bin/terok-tui", ["/usr/local/bin/terok-tui", "--experimental"])
        ]

    def test_normal_exit_does_not_re_exec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A plain quit returns without touching execv."""
        monkeypatch.setattr(app_mod, "TerokTUI", lambda: SimpleNamespace(run=lambda: None))
        execs: list[object] = []
        monkeypatch.setattr(app_mod.os, "execv", lambda f, argv: execs.append((f, argv)))

        app_mod._run_tui()

        assert execs == []

    def test_exec_failure_becomes_a_message_not_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The entry point vanishing between which() and exec (upgrade in flight) exits cleanly."""
        import shutil

        monkeypatch.setattr(
            app_mod, "TerokTUI", lambda: SimpleNamespace(run=lambda: _RESTART_EXIT_RESULT)
        )
        monkeypatch.setattr(shutil, "which", lambda _cmd: "/usr/local/bin/terok-tui")

        def broken_execv(_file: str, _argv: list[str]) -> None:
            raise OSError("gone")

        monkeypatch.setattr(app_mod.os, "execv", broken_execv)

        app_mod._run_tui()

        assert "Could not restart" in capsys.readouterr().out

    def test_declines_restart_when_entry_point_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ``terok-tui`` on PATH ⇒ exit normally instead of exec'ing a guess."""
        import shutil

        monkeypatch.setattr(
            app_mod, "TerokTUI", lambda: SimpleNamespace(run=lambda: _RESTART_EXIT_RESULT)
        )
        monkeypatch.setattr(shutil, "which", lambda _cmd: None)
        execs: list[object] = []
        monkeypatch.setattr(app_mod.os, "execv", lambda f, argv: execs.append((f, argv)))

        app_mod._run_tui()

        assert execs == []


class TestRestartFlags:
    """Restart flags are rebuilt from parsed values, never echoed from argv."""

    @pytest.mark.parametrize(
        ("namespace", "expected"),
        [
            (
                {"tmux": None, "new_session": False, "experimental": False, "no_emoji": False},
                (),
            ),
            (
                {"tmux": True, "new_session": True, "experimental": False, "no_emoji": False},
                ("--tmux", "--new-session"),
            ),
            (
                {"tmux": False, "new_session": False, "experimental": True, "no_emoji": True},
                ("--no-tmux", "--experimental", "--no-emoji"),
            ),
        ],
        ids=["defaults-carry-nothing", "tmux-fork", "no-tmux-extras"],
    )
    def test_flags_mirror_parsed_values(
        self, namespace: dict[str, object], expected: tuple[str, ...]
    ) -> None:
        """Each flag reappears exactly when its parsed value asks for it."""
        assert app_mod._restart_flags(SimpleNamespace(**namespace)) == expected
