# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the post-task-launch recovery-key warning footer.

[`_maybe_warn_recovery_unconfirmed`][terok.lib.orchestration.task_runners.container._maybe_warn_recovery_unconfirmed]
prints one line after the SSH login hint when no recovery-ack marker
is on disk.  Coverage here protects against silent regressions on
both the "warn" and the "stay quiet" branches.
"""

from __future__ import annotations

import pytest

from terok.lib.orchestration.task_runners.container import (
    _maybe_warn_recovery_unconfirmed,
)


class TestMaybeWarnRecoveryUnconfirmed:
    """Branches: acked / unacked / probe-raises / sandbox-too-old."""

    def test_acknowledged_prints_nothing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Marker present → silent.  No noise on the happy path."""
        monkeypatch.setattr(
            "terok.lib.integrations.sandbox.is_recovery_acknowledged",
            lambda: True,
        )
        _maybe_warn_recovery_unconfirmed(color=False)
        assert capsys.readouterr().out == ""

    def test_unacknowledged_prints_warning(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Marker missing → one-line nudge with the reveal command."""
        monkeypatch.setattr(
            "terok.lib.integrations.sandbox.is_recovery_acknowledged",
            lambda: False,
        )
        _maybe_warn_recovery_unconfirmed(color=False)
        out = capsys.readouterr().out
        assert "recovery key unconfirmed" in out.lower()
        assert "terok vault passphrase reveal" in out

    def test_probe_raises_swallowed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A best-effort probe must never block the operator's login hint."""

        def _boom() -> bool:
            raise RuntimeError("vault chain broke")

        monkeypatch.setattr("terok.lib.integrations.sandbox.is_recovery_acknowledged", _boom)
        _maybe_warn_recovery_unconfirmed(color=False)
        assert capsys.readouterr().out == ""

    def test_sandbox_without_symbol_is_silent(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An older sandbox pin without the wrapper degrades to no-op, no crash."""
        # Simulate an older adapter that doesn't re-export the symbol.
        import terok.lib.integrations.sandbox as adapter

        original = getattr(adapter, "is_recovery_acknowledged", None)
        monkeypatch.delattr(
            "terok.lib.integrations.sandbox.is_recovery_acknowledged",
            raising=False,
        )
        try:
            _maybe_warn_recovery_unconfirmed(color=False)
        finally:
            if original is not None:
                adapter.is_recovery_acknowledged = original
        assert capsys.readouterr().out == ""
