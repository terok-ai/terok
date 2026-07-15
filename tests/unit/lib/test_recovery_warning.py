# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the post-task-launch recovery-key warning footer.

[`_maybe_warn_recovery_unconfirmed`][terok.lib.orchestration.task_runners.container._maybe_warn_recovery_unconfirmed]
prints one line after the SSH login hint when no recovery-ack marker
is on disk.  Coverage here protects against silent regressions on
the three states (acked / unacked-durable / unacked-session-only)
plus probe failure.
"""

from __future__ import annotations

import pytest

from terok.lib.orchestration.task_runners.container import (
    _maybe_warn_recovery_unconfirmed,
)


def _status(*, acknowledged: bool, source: str | None):
    """Build a fake ``RecoveryStatus`` for monkeypatching the probe.

    Uses the real ``RecoveryStatus`` dataclass so the ``urgent``
    property derives correctly from ``acknowledged`` + ``source``.
    ``source`` is coerced to the real [`PassphraseTier`][terok_sandbox.PassphraseTier]
    member — ``session_only`` compares by identity, so a bare string
    would silently defeat the escalation branch.
    """
    from terok.lib.integrations.sandbox import PassphraseTier, RecoveryStatus

    tier = PassphraseTier(source) if source is not None else None
    return RecoveryStatus(acknowledged=acknowledged, source=tier)


class TestMaybeWarnRecoveryUnconfirmed:
    """Branches: acked / unacked-durable / unacked-session / probe-raises."""

    def test_acknowledged_prints_nothing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Marker present → silent.  No noise on the happy path."""
        from terok.lib.integrations.sandbox import RecoveryStatus

        monkeypatch.setattr(
            RecoveryStatus,
            "load",
            classmethod(lambda cls, cfg=None: _status(acknowledged=True, source="systemd-creds")),
        )
        _maybe_warn_recovery_unconfirmed(color=False)
        assert capsys.readouterr().out == ""

    def test_unacknowledged_durable_tier_prints_warn(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Marker missing + non-session source → yellow ``warn`` footer."""
        from terok.lib.integrations.sandbox import RecoveryStatus

        monkeypatch.setattr(
            RecoveryStatus,
            "load",
            classmethod(lambda cls, cfg=None: _status(acknowledged=False, source="keyring")),
        )
        _maybe_warn_recovery_unconfirmed(color=False)
        out = capsys.readouterr().out
        assert "recovery key unconfirmed" in out.lower()
        assert "terok vault passphrase reveal" in out
        # The escalated wording must NOT appear on the durable branch.
        assert "UNRECOVERABLE" not in out

    def test_unacknowledged_session_only_escalates(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Marker missing + session-file source → loud ``error`` footer.

        The session tier is wiped on the next reboot, so this state is
        a genuinely different severity from the durable-tier warning.
        The text must call out "session-unlock", "reboot", and
        "UNRECOVERABLE" so the operator understands the asymmetry.
        """
        from terok.lib.integrations.sandbox import RecoveryStatus

        monkeypatch.setattr(
            RecoveryStatus,
            "load",
            classmethod(lambda cls, cfg=None: _status(acknowledged=False, source="session-file")),
        )
        _maybe_warn_recovery_unconfirmed(color=False)
        out = capsys.readouterr().out
        assert "UNCONFIRMED" in out
        assert "session-unlock" in out
        assert "reboot" in out.lower()
        assert "UNRECOVERABLE" in out
        assert "terok vault passphrase reveal" in out

    def test_probe_raises_swallowed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A best-effort probe must never block the operator's login hint."""
        from terok.lib.integrations.sandbox import RecoveryStatus

        def _boom(cls, cfg=None):
            raise RuntimeError("vault chain broke")

        monkeypatch.setattr(RecoveryStatus, "load", classmethod(_boom))
        _maybe_warn_recovery_unconfirmed(color=False)
        assert capsys.readouterr().out == ""
