# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the CLI's rekey pre-flight overlay on ``vault passphrase change``.

The wrapped handler is exercised directly with the pre-flight seams
patched at ``terok.lib.api.vault``; the tree-level test pins that the
overlay actually lands on the verb (and, via node identity, on the
``terok vault …`` shortcut spelling).
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from terok.cli.commands.vault_change import _with_rekey_preflight, wrap_passphrase_change
from terok.lib.domain.vault_rekey import DbHolder, RunningTask

_TASK = RunningTask("alpha", "1")
_OURS = DbHolder(pid=101, cmdline="python -m terok_sandbox supervise-child vault abc", owned=True)
_THEIRS = DbHolder(pid=202, cmdline="sqlitebrowser credentials.db", owned=False)


@pytest.fixture(autouse=True)
def _no_rekey_blockers() -> Iterator[None]:
    """Default every test to a quiet fleet; blocker tests re-patch inside."""
    with (
        patch("terok.lib.api.vault.find_running_tasks", return_value=()),
        patch("terok.lib.api.vault.wait_for_db_release", return_value=()),
        patch("terok.lib.api.vault.restart_tasks_after_rekey", side_effect=_all_ok),
    ):
        yield


def _all_ok(tasks: list[RunningTask]) -> list[tuple[RunningTask, None]]:
    """Restart stand-in: every task comes back."""
    return [(task, None) for task in tasks]


@pytest.fixture
def tty(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Pretend stdin is a terminal; return the mock feeding ``input()``."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answer = MagicMock(return_value="y")
    monkeypatch.setattr("builtins.input", answer)
    return answer


class TestPreflightWrapper:
    """Behavior of the wrapped handler across blocker situations."""

    def test_quiet_fleet_delegates_untouched(self) -> None:
        """No blockers → the sandbox handler runs exactly as before."""
        handler = MagicMock()
        _with_rekey_preflight(handler)(cfg="the-cfg")
        handler.assert_called_once_with(cfg="the-cfg")

    def test_running_tasks_accept_stops_rekeys_restarts(self, tty: MagicMock) -> None:
        """The full round trip: stop on 'y', delegate, bring the fleet back."""
        handler = MagicMock()
        with (
            patch("terok.lib.api.vault.find_running_tasks", return_value=(_TASK,)),
            patch("terok.lib.api.vault.stop_tasks_for_rekey", return_value=[(_TASK, None)]) as stop,
            patch("terok.lib.api.vault.restart_tasks_after_rekey", side_effect=_all_ok) as restart,
        ):
            _with_rekey_preflight(handler)()
        stop.assert_called_once_with((_TASK,))
        handler.assert_called_once()
        restart.assert_called_once_with([_TASK])

    def test_running_tasks_decline_exits_before_the_handler(self, tty: MagicMock) -> None:
        """Anything but yes cancels with nothing stopped and nothing changed."""
        tty.return_value = "n"
        handler = MagicMock()
        with (
            patch("terok.lib.api.vault.find_running_tasks", return_value=(_TASK,)),
            patch("terok.lib.api.vault.stop_tasks_for_rekey") as stop,
        ):
            with pytest.raises(SystemExit, match="no tasks were touched"):
                _with_rekey_preflight(handler)()
        stop.assert_not_called()
        handler.assert_not_called()

    def test_no_tty_with_blockers_fails_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Piped stdin belongs to the passphrase protocol — never consume it for y/N."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        handler = MagicMock()
        with patch("terok.lib.api.vault.find_running_tasks", return_value=(_TASK,)):
            with pytest.raises(SystemExit, match="without a TTY"):
                _with_rekey_preflight(handler)()
        handler.assert_not_called()

    def test_failed_stop_restarts_the_stopped_and_exits(self, tty: MagicMock) -> None:
        """A half-stopped fleet aborts the change and brings the stopped half back."""
        other = RunningTask("beta", "7")
        handler = MagicMock()
        with (
            patch("terok.lib.api.vault.find_running_tasks", return_value=(_TASK, other)),
            patch(
                "terok.lib.api.vault.stop_tasks_for_rekey",
                return_value=[(_TASK, None), (other, "podman refused")],
            ),
            patch("terok.lib.api.vault.restart_tasks_after_rekey", side_effect=_all_ok) as restart,
        ):
            with pytest.raises(SystemExit, match="nothing was changed"):
                _with_rekey_preflight(handler)()
        handler.assert_not_called()
        restart.assert_called_once_with([_TASK])

    def test_foreign_holder_refuses_without_prompting(self, tty: MagicMock) -> None:
        """What isn't ours is named, never signalled — no y/N, no handler."""
        handler = MagicMock()
        with (
            patch("terok.lib.api.vault.wait_for_db_release", return_value=(_THEIRS,)),
            patch("terok.lib.api.vault.terminate_stale_holders") as terminate,
        ):
            with pytest.raises(SystemExit, match="outside terok"):
                _with_rekey_preflight(handler)()
        tty.assert_not_called()
        terminate.assert_not_called()
        handler.assert_not_called()

    def test_stale_holders_swept_after_confirmation(self, tty: MagicMock) -> None:
        """The hal case on the CLI: orphaned supervisor → y → sweep → delegate."""
        handler = MagicMock()
        with (
            patch("terok.lib.api.vault.wait_for_db_release", return_value=(_OURS,)),
            patch("terok.lib.api.vault.terminate_stale_holders", return_value=()) as terminate,
        ):
            _with_rekey_preflight(handler)()
        terminate.assert_called_once_with((_OURS,))
        handler.assert_called_once()

    def test_handler_failure_still_restarts_the_fleet(self, tty: MagicMock) -> None:
        """The handler's own exit propagates — after the stopped tasks came back."""
        handler = MagicMock(side_effect=SystemExit("tiers marked ✗ above"))
        with (
            patch("terok.lib.api.vault.find_running_tasks", return_value=(_TASK,)),
            patch("terok.lib.api.vault.stop_tasks_for_rekey", return_value=[(_TASK, None)]),
            patch("terok.lib.api.vault.restart_tasks_after_rekey", side_effect=_all_ok) as restart,
        ):
            with pytest.raises(SystemExit, match="tiers marked"):
                _with_rekey_preflight(handler)()
        restart.assert_called_once_with([_TASK])

    def test_restart_casualties_turn_a_clean_change_loud(self, tty: MagicMock) -> None:
        """A successful rekey whose restarts failed must not exit zero."""
        handler = MagicMock()
        with (
            patch("terok.lib.api.vault.find_running_tasks", return_value=(_TASK,)),
            patch("terok.lib.api.vault.stop_tasks_for_rekey", return_value=[(_TASK, None)]),
            patch(
                "terok.lib.api.vault.restart_tasks_after_rekey",
                return_value=[(_TASK, "image gone")],
            ),
        ):
            with pytest.raises(SystemExit, match="could not be restarted"):
                _with_rekey_preflight(handler)()
        handler.assert_called_once()


class TestTreeOverlay:
    """The overlay lands on the verb inside a composed tree."""

    def test_wraps_the_change_verb(self) -> None:
        """The wrapped handler replaces the original at the deep path."""
        from terok.lib.api import CommandDef, CommandTree

        original = MagicMock()
        tree = CommandTree(
            (
                CommandDef(
                    name="sandbox",
                    help="",
                    children=(
                        CommandDef(
                            name="vault",
                            help="",
                            children=(
                                CommandDef(
                                    name="passphrase",
                                    help="",
                                    children=(
                                        CommandDef(name="change", help="", handler=original),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            )
        )

        wrapped_tree = wrap_passphrase_change(tree)

        handler = wrapped_tree.find_at(("sandbox", "vault", "passphrase", "change")).handler
        assert handler is not original
        handler()  # quiet fleet (autouse) → delegates
        original.assert_called_once()

    def test_missing_verb_passes_through(self) -> None:
        """A tree without the verb (older sandbox) is returned unchanged."""
        from terok.lib.api import CommandDef, CommandTree

        tree = CommandTree((CommandDef(name="sandbox", help="", children=()),))
        assert wrap_passphrase_change(tree) is tree
