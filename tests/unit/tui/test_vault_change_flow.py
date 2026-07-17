# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the TUI's change-passphrase conversation.

``_run_vault_change_flow`` is the TUI rendering of ``vault passphrase
change``: probe → (current-passphrase modal only when locked) → create
modal → rekey blockers cleared (running tasks stopped, orphaned
holders swept) → sandbox ``change_passphrase`` → reveal + re-ack →
stopped tasks restarted.  These tests pin the branching and the
failure notifications, driven unbound-method-on-stub (via the worker
decorator's ``__wrapped__``), same idiom as
``test_vault_provisioning_flow.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from terok.lib.domain.vault_rekey import DbHolder, RunningTask
from terok.lib.integrations.sandbox import VaultState, WrongPassphraseError
from terok.tui.app import TerokTUI

_change_flow = TerokTUI._run_vault_change_flow.__wrapped__


@pytest.fixture(autouse=True)
def _no_rekey_blockers() -> Iterator[None]:
    """Default every test to a quiet fleet — nothing pins the credentials DB.

    Blocker-conversation tests re-patch these inside their own scope.
    """
    with (
        patch("terok.lib.api.vault.find_running_tasks", return_value=()),
        patch("terok.lib.api.vault.wait_for_db_release", return_value=()),
    ):
        yield


@pytest.fixture
def flow_stub() -> SimpleNamespace:
    """Duck for ``_run_vault_change_flow`` — modal + notify + refresh seams.

    The real conversation / notification helpers are bound onto the
    stub so the tests keep exercising the full flow behavior after the
    complexity split, not a mocked-out skeleton.  The restart phase is
    a mock: it dispatches a child process through the console-log
    pipeline, which has its own seam tests below.
    """
    stub = SimpleNamespace(
        push_screen_wait=AsyncMock(),
        notify=MagicMock(),
        _refresh_vault_status=AsyncMock(),
        _reveal_new_passphrase=AsyncMock(),
        _restart_stopped_tasks=AsyncMock(),
        _last_vault_status=None,
    )
    stub._collect_change_inputs = TerokTUI._collect_change_inputs.__get__(stub)
    stub._notify_change_failure = TerokTUI._notify_change_failure.__get__(stub)
    stub._notify_change_outcome = TerokTUI._notify_change_outcome.__get__(stub)
    stub._clear_rekey_blockers = TerokTUI._clear_rekey_blockers.__get__(stub)
    return stub


def _status(state: VaultState) -> SimpleNamespace:
    """A ``VaultStatus`` stand-in (the flow reads only ``state``)."""
    return SimpleNamespace(state=state)


def _change_result(*, generated: bool = False, problems: tuple = ()) -> SimpleNamespace:
    """A ``PassphraseChangeResult`` stand-in."""
    return SimpleNamespace(
        passphrase="the-new-passphrase",
        generated=generated,
        rekeyed=True,
        rewrites=(),
        problems=problems,
    )


class TestRunVaultChangeFlow:
    """Branching of the change conversation."""

    async def test_unprovisioned_refuses_without_modals(self, flow_stub: SimpleNamespace) -> None:
        """Nothing to change on a fresh install — points at setup instead."""
        with patch(
            "terok.lib.api.vault.load_vault_status",
            return_value=_status(VaultState.UNPROVISIONED),
        ):
            await _change_flow(flow_stub)
        flow_stub.push_screen_wait.assert_not_awaited()
        assert flow_stub.notify.call_args.kwargs["severity"] == "warning"

    async def test_unlocked_skips_the_current_passphrase_modal(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """A resolvable tier means retyping the old value is theatre — one modal only."""
        flow_stub.push_screen_wait.return_value = "typed-new"
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch(
                "terok.lib.api.vault.change_passphrase",
                return_value=_change_result(),
            ) as change,
        ):
            await _change_flow(flow_stub)
        assert flow_stub.push_screen_wait.await_count == 1  # create modal only
        assert change.call_args.kwargs == {"old": None, "new": "typed-new"}
        flow_stub._refresh_vault_status.assert_awaited_once()
        flow_stub._reveal_new_passphrase.assert_awaited_once()

    async def test_locked_asks_for_the_current_passphrase_first(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """A locked vault needs the old key — cryptographic necessity, not ceremony."""
        flow_stub.push_screen_wait.side_effect = ["old-pass", "typed-new"]
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.LOCKED),
            ),
            patch(
                "terok.lib.api.vault.change_passphrase",
                return_value=_change_result(),
            ) as change,
        ):
            await _change_flow(flow_stub)
        assert flow_stub.push_screen_wait.await_count == 2
        assert change.call_args.kwargs == {"old": "old-pass", "new": "typed-new"}

    async def test_cancel_on_create_modal_changes_nothing(self, flow_stub: SimpleNamespace) -> None:
        """Backing out of the create modal must leave the vault untouched."""
        flow_stub.push_screen_wait.return_value = None
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch("terok.lib.api.vault.change_passphrase") as change,
        ):
            await _change_flow(flow_stub)
        change.assert_not_called()
        flow_stub._reveal_new_passphrase.assert_not_awaited()

    async def test_empty_entry_mints_and_reveals(self, flow_stub: SimpleNamespace) -> None:
        """The create modal's generate sentinel ('') becomes ``new=None`` → sandbox mints."""
        flow_stub.push_screen_wait.return_value = ""
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch(
                "terok.lib.api.vault.change_passphrase",
                return_value=_change_result(generated=True),
            ) as change,
        ):
            await _change_flow(flow_stub)
        assert change.call_args.kwargs["new"] is None
        reveal_args = flow_stub._reveal_new_passphrase.await_args.args
        assert reveal_args[0] == "the-new-passphrase"

    async def test_wrong_passphrase_notifies_and_stops(self, flow_stub: SimpleNamespace) -> None:
        """A wrong current passphrase surfaces as an error and skips the reveal."""
        flow_stub.push_screen_wait.side_effect = ["bad-old", "typed-new"]
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.LOCKED),
            ),
            patch(
                "terok.lib.api.vault.change_passphrase",
                side_effect=WrongPassphraseError("nope"),
            ),
        ):
            await _change_flow(flow_stub)
        assert flow_stub.notify.call_args.kwargs["severity"] == "error"
        flow_stub._reveal_new_passphrase.assert_not_awaited()

    async def test_locked_db_after_preflight_reads_as_a_race(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """The pre-flight freed the DB, so a residual lock means something re-opened it."""
        flow_stub.push_screen_wait.return_value = "typed-new"
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch(
                "terok.lib.api.vault.change_passphrase",
                side_effect=RuntimeError("database is locked"),
            ),
        ):
            await _change_flow(flow_stub)
        message = flow_stub.notify.call_args.args[0]
        assert "Nothing was changed" in message
        flow_stub._reveal_new_passphrase.assert_not_awaited()

    async def test_tier_problems_surface_loudly_but_still_reveal(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """A failed tier rewrite must not scroll past — and the re-ack still runs."""
        flow_stub.push_screen_wait.return_value = "typed-new"
        problem = SimpleNamespace(tier="keyring", ok=False, detail="keyring write failed")
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch(
                "terok.lib.api.vault.change_passphrase",
                return_value=_change_result(problems=(problem,)),
            ),
        ):
            await _change_flow(flow_stub)
        assert flow_stub.notify.call_args.kwargs["severity"] == "error"
        assert "keyring write failed" in flow_stub.notify.call_args.args[0]
        flow_stub._reveal_new_passphrase.assert_awaited_once()


_TASK = RunningTask("alpha", "1")
_TASK_2 = RunningTask("beta", "7")
_OURS = DbHolder(pid=101, cmdline="python -m terok_sandbox supervise-child vault abc", owned=True)
_THEIRS = DbHolder(pid=202, cmdline="sqlitebrowser credentials.db", owned=False)


class TestRekeyBlockerConversation:
    """The stop → re-encrypt → restart offer and the orphan sweep.

    These are the paths that used to dead-end with ``database is
    locked``; each patches the pre-flight seams over the autouse
    quiet-fleet default.
    """

    async def test_running_tasks_offer_declined_changes_nothing(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """Backing out of the stop offer must leave the fleet and the vault untouched."""
        flow_stub.push_screen_wait.side_effect = ["typed-new", False]
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch("terok.lib.api.vault.find_running_tasks", return_value=(_TASK,)),
            patch("terok.lib.api.vault.stop_tasks_for_rekey") as stop,
            patch("terok.lib.api.vault.change_passphrase") as change,
        ):
            await _change_flow(flow_stub)
        stop.assert_not_called()
        change.assert_not_called()
        assert "no tasks were touched" in flow_stub.notify.call_args.args[0]

    async def test_stop_rekey_restart_round_trip(self, flow_stub: SimpleNamespace) -> None:
        """Accepting the offer stops the fleet, rekeys, and owes the tasks a restart."""
        flow_stub.push_screen_wait.side_effect = ["typed-new", True]
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch("terok.lib.api.vault.find_running_tasks", return_value=(_TASK,)),
            patch("terok.lib.api.vault.stop_tasks_for_rekey", return_value=[(_TASK, None)]) as stop,
            patch(
                "terok.lib.api.vault.change_passphrase",
                return_value=_change_result(),
            ) as change,
        ):
            await _change_flow(flow_stub)
        stop.assert_called_once_with((_TASK,))
        change.assert_called_once()
        flow_stub._restart_stopped_tasks.assert_awaited_once_with([_TASK])
        flow_stub._reveal_new_passphrase.assert_awaited_once()

    async def test_failed_stop_aborts_and_restarts_the_stopped(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """A half-stopped fleet means no rekey — and the stopped half comes back."""
        flow_stub.push_screen_wait.side_effect = ["typed-new", True]
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch("terok.lib.api.vault.find_running_tasks", return_value=(_TASK, _TASK_2)),
            patch(
                "terok.lib.api.vault.stop_tasks_for_rekey",
                return_value=[(_TASK, None), (_TASK_2, "podman refused")],
            ),
            patch("terok.lib.api.vault.change_passphrase") as change,
        ):
            await _change_flow(flow_stub)
        change.assert_not_called()
        assert "podman refused" in flow_stub.notify.call_args.args[0]
        flow_stub._restart_stopped_tasks.assert_awaited_once_with([_TASK])

    async def test_restart_runs_even_when_the_rekey_fails(self, flow_stub: SimpleNamespace) -> None:
        """A failed change must not leave the stopped fleet down."""
        flow_stub.push_screen_wait.side_effect = ["typed-new", True]
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch("terok.lib.api.vault.find_running_tasks", return_value=(_TASK,)),
            patch("terok.lib.api.vault.stop_tasks_for_rekey", return_value=[(_TASK, None)]),
            patch(
                "terok.lib.api.vault.change_passphrase",
                side_effect=RuntimeError("disk I/O error"),
            ),
        ):
            await _change_flow(flow_stub)
        assert flow_stub.notify.call_args.kwargs["severity"] == "error"
        flow_stub._restart_stopped_tasks.assert_awaited_once_with([_TASK])

    async def test_foreign_holder_refuses_without_a_kill_offer(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """What isn't ours is named, never signalled — and the change stops."""
        flow_stub.push_screen_wait.side_effect = ["typed-new"]
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch("terok.lib.api.vault.wait_for_db_release", return_value=(_THEIRS,)),
            patch("terok.lib.api.vault.terminate_stale_holders") as terminate,
            patch("terok.lib.api.vault.change_passphrase") as change,
        ):
            await _change_flow(flow_stub)
        terminate.assert_not_called()
        change.assert_not_called()
        assert flow_stub.push_screen_wait.await_count == 1  # create modal only, no kill modal
        assert "202" in flow_stub.notify.call_args.args[0]

    async def test_stale_holders_swept_after_confirmation(self, flow_stub: SimpleNamespace) -> None:
        """The hal case: zero tasks running, an orphaned supervisor pins the DB."""
        flow_stub.push_screen_wait.side_effect = ["typed-new", True]
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch("terok.lib.api.vault.wait_for_db_release", return_value=(_OURS,)),
            patch("terok.lib.api.vault.terminate_stale_holders", return_value=()) as terminate,
            patch(
                "terok.lib.api.vault.change_passphrase",
                return_value=_change_result(),
            ) as change,
        ):
            await _change_flow(flow_stub)
        terminate.assert_called_once_with((_OURS,))
        change.assert_called_once()
        flow_stub._restart_stopped_tasks.assert_awaited_once_with([])

    async def test_stale_holder_kill_declined_changes_nothing(
        self, flow_stub: SimpleNamespace
    ) -> None:
        """Declining the sweep leaves the orphan — and the vault — alone."""
        flow_stub.push_screen_wait.side_effect = ["typed-new", False]
        with (
            patch(
                "terok.lib.api.vault.load_vault_status",
                return_value=_status(VaultState.UNLOCKED),
            ),
            patch("terok.lib.api.vault.wait_for_db_release", return_value=(_OURS,)),
            patch("terok.lib.api.vault.terminate_stale_holders") as terminate,
            patch("terok.lib.api.vault.change_passphrase") as change,
        ):
            await _change_flow(flow_stub)
        terminate.assert_not_called()
        change.assert_not_called()
        assert flow_stub.notify.call_args.kwargs["severity"] == "warning"


class TestRestartStoppedTasks:
    """The restart phase — a dispatched child process, loud on casualties."""

    @staticmethod
    def _stub(*, entry_ok: bool) -> SimpleNamespace:
        entry = SimpleNamespace(wait=AsyncMock(), ok=entry_ok)
        stub = SimpleNamespace(
            dispatch_console_action=MagicMock(return_value=entry),
            push_screen=AsyncMock(),
            notify=MagicMock(),
            run_worker=MagicMock(),
            refresh_tasks=MagicMock(return_value=None),
        )
        stub._restart_stopped_tasks = TerokTUI._restart_stopped_tasks.__get__(stub)
        return stub

    async def test_noop_without_stopped_tasks(self) -> None:
        """Nothing stopped → nothing dispatched, no console screen."""
        stub = self._stub(entry_ok=True)
        await stub._restart_stopped_tasks([])
        stub.dispatch_console_action.assert_not_called()

    async def test_dispatches_the_restart_worker(self) -> None:
        """The stopped tasks travel as JSON-positional ``[project, task]`` rows."""
        stub = self._stub(entry_ok=True)
        await stub._restart_stopped_tasks([_TASK, _TASK_2])
        ref, rows = stub.dispatch_console_action.call_args.args
        assert ref == "terok.tui.worker_actions:vault_rekey_restart_tasks"
        assert rows == [["alpha", "1"], ["beta", "7"]]
        stub.notify.assert_not_called()

    async def test_failed_restart_is_flagged_loudly(self) -> None:
        """A non-zero worker exit must not scroll past silently."""
        stub = self._stub(entry_ok=False)
        await stub._restart_stopped_tasks([_TASK])
        assert stub.notify.call_args.kwargs["severity"] == "error"
