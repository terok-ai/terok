# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the best-effort logging helpers."""

from __future__ import annotations

import pytest

from terok.lib.util import logging_utils


def test_timed_phase_logs_start_and_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean phase logs a start line and a ``done in N.NNs`` line."""
    lines: list[str] = []
    monkeypatch.setattr(logging_utils, "_log_debug", lines.append)

    with logging_utils.timed_phase("launch[c1]: podman run"):
        pass

    assert lines[0] == "launch[c1]: podman run: start"
    assert lines[1].startswith("launch[c1]: podman run: done in ")
    assert lines[1].endswith("s")


def test_timed_phase_logs_failed_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising body logs ``failed in N.NNs`` and propagates the exception."""
    lines: list[str] = []
    monkeypatch.setattr(logging_utils, "_log_debug", lines.append)

    with pytest.raises(ValueError, match="boom"), logging_utils.timed_phase("shield[c1]: policy"):
        raise ValueError("boom")

    assert lines[0] == "shield[c1]: policy: start"
    assert lines[1].startswith("shield[c1]: policy: failed in ")
