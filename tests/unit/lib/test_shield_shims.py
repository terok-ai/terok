# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the post-W5.B shield shim layer.

Both ``terok.lib.api.shield`` and ``terok.lib.integrations.sandbox``
expose thin function shims (``make_shield``, ``shield_up``,
``shield_down``, ``shield_state``, ``shield_status``,
``shield_run_setup``) that wrap the new ``ShieldManager`` /
``ShieldHooks`` class API.  These tests pin the call shape so a
future API change in terok-sandbox doesn't silently drift the
terok-facing surface.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from terok.lib.api import shield as api_shield
from terok.lib.integrations import sandbox as integ_sandbox

# ── api/shield.py shims ──────────────────────────────────────────


def test_api_make_shield_delegates_to_manager() -> None:
    """``make_shield`` constructs a ``ShieldManager`` and returns its cached Shield."""
    task_dir = Path("/tmp/task")
    with mock.patch("terok.lib.api.shield.ShieldManager") as cls:
        result = api_shield.make_shield(task_dir)
    cls.assert_called_once_with(task_dir, None)
    assert result is cls.return_value.shield


def test_api_shield_up_routes_to_manager() -> None:
    """``shield_up`` constructs ShieldManager and calls ``.up``."""
    with mock.patch("terok.lib.api.shield.ShieldManager") as cls:
        api_shield.shield_up("ctr", Path("/tmp/t"))
    cls.assert_called_once_with(Path("/tmp/t"), None)
    cls.return_value.up.assert_called_once_with("ctr")


def test_api_shield_down_propagates_allow_all() -> None:
    """``shield_down(allow_all=True)`` threads the flag to ``ShieldManager.down``."""
    with mock.patch("terok.lib.api.shield.ShieldManager") as cls:
        api_shield.shield_down("ctr", Path("/tmp/t"), allow_all=True)
    cls.return_value.down.assert_called_once_with("ctr", allow_all=True)


def test_api_shield_state_returns_manager_state() -> None:
    """``shield_state`` returns whatever ``ShieldManager.state`` reports."""
    with mock.patch("terok.lib.api.shield.ShieldManager") as cls:
        cls.return_value.state.return_value = "up"
        result = api_shield.shield_state("ctr", Path("/tmp/t"))
    assert result == "up"


def test_api_shield_status_uses_throwaway_task_dir() -> None:
    """``shield_status`` constructs a manager in a tmp dir and returns its status dict."""
    expected = {"mode": "hook", "profiles": []}
    with mock.patch("terok.lib.api.shield.ShieldManager") as cls:
        cls.return_value.status.return_value = expected
        result = api_shield.shield_status()
    assert result == expected
    cls.return_value.status.assert_called_once()


def test_api_shield_run_setup_routes_to_shield_hooks() -> None:
    """``shield_run_setup`` is a one-liner over ``ShieldHooks.install``."""
    with mock.patch("terok.lib.api.shield.ShieldHooks") as hooks:
        api_shield.shield_run_setup(root=True, user=False)
    hooks.install.assert_called_once_with(root=True, user=False)


# ── integrations/sandbox.py shims ───────────────────────────────


def test_integ_make_shield_delegates_to_manager() -> None:
    """``integrations.sandbox.make_shield`` mirrors the api/* shim."""
    with mock.patch("terok.lib.integrations.sandbox.ShieldManager") as cls:
        result = integ_sandbox.make_shield(Path("/tmp/t"))
    cls.assert_called_once_with(Path("/tmp/t"), None)
    assert result is cls.return_value.shield


def test_integ_up_routes_to_manager() -> None:
    """``integrations.sandbox.up`` calls ``ShieldManager.up``."""
    with mock.patch("terok.lib.integrations.sandbox.ShieldManager") as cls:
        integ_sandbox.up("ctr", Path("/tmp/t"))
    cls.return_value.up.assert_called_once_with("ctr")


def test_integ_down_propagates_allow_all() -> None:
    """``integrations.sandbox.down`` threads ``allow_all`` to the manager."""
    with mock.patch("terok.lib.integrations.sandbox.ShieldManager") as cls:
        integ_sandbox.down("ctr", Path("/tmp/t"), allow_all=True)
    cls.return_value.down.assert_called_once_with("ctr", allow_all=True)


def test_integ_quarantine_routes_to_manager() -> None:
    """``integrations.sandbox.quarantine`` calls ``ShieldManager.quarantine``."""
    with mock.patch("terok.lib.integrations.sandbox.ShieldManager") as cls:
        integ_sandbox.quarantine("ctr", Path("/tmp/t"))
    cls.return_value.quarantine.assert_called_once_with("ctr")


def test_integ_state_returns_manager_state() -> None:
    """``integrations.sandbox.state`` returns the manager's state."""
    with mock.patch("terok.lib.integrations.sandbox.ShieldManager") as cls:
        cls.return_value.state.return_value = "down"
        assert integ_sandbox.state("ctr", Path("/tmp/t")) == "down"


def test_integ_status_uses_throwaway_task_dir() -> None:
    """``integrations.sandbox.status`` builds a manager in tmp and returns status."""
    with mock.patch("terok.lib.integrations.sandbox.ShieldManager") as cls:
        cls.return_value.status.return_value = {"mode": "hook"}
        assert integ_sandbox.status() == {"mode": "hook"}


def test_integ_run_setup_routes_to_shield_hooks() -> None:
    """``run_setup`` shim hits ``ShieldHooks.install`` with both flags forwarded."""
    with mock.patch("terok.lib.integrations.sandbox.ShieldHooks") as hooks:
        integ_sandbox.run_setup(root=True, user=True)
    hooks.install.assert_called_once_with(root=True, user=True)


def test_integ_setup_hooks_direct_picks_complementary_scope() -> None:
    """``setup_hooks_direct(root=False)`` translates to ``ShieldHooks.install(user=True)``."""
    with mock.patch("terok.lib.integrations.sandbox.ShieldHooks") as hooks:
        integ_sandbox.setup_hooks_direct(root=False)
    hooks.install.assert_called_once_with(root=False, user=True)


def test_integ_setup_hooks_direct_root_scope() -> None:
    """``setup_hooks_direct(root=True)`` translates to ``ShieldHooks.install(root=True)``."""
    with mock.patch("terok.lib.integrations.sandbox.ShieldHooks") as hooks:
        integ_sandbox.setup_hooks_direct(root=True)
    hooks.install.assert_called_once_with(root=True, user=False)


# ── api/setup.py shim ───────────────────────────────────────────


def test_api_setup_hooks_direct_picks_complementary_scope() -> None:
    """``api.setup.setup_hooks_direct`` mirrors the integrations-layer shim."""
    from terok.lib.api import setup as api_setup

    with mock.patch("terok.lib.api.setup.ShieldHooks") as hooks:
        api_setup.setup_hooks_direct(root=False)
    hooks.install.assert_called_once_with(root=False, user=True)


def test_api_setup_hooks_direct_root_scope() -> None:
    """``api.setup.setup_hooks_direct(root=True)`` flips the scope flag."""
    from terok.lib.api import setup as api_setup

    with mock.patch("terok.lib.api.setup.ShieldHooks") as hooks:
        api_setup.setup_hooks_direct(root=True)
    hooks.install.assert_called_once_with(root=True, user=False)
