# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`TerokTUI._update_title`][terok.tui.app] header composition.

Pins #683 — the right-aligned ``sub_title`` carries ``user@host`` so a
TUI opened over SSH (or in tmux on another box) can't be confused with
a local one.  The most-reported footgun was running a task on the
wrong host because the header looked identical across machines.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from terok.tui import app as app_mod
from terok.tui.app import TerokTUI


@pytest.fixture
def title_stub(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Tiny duck for ``TerokTUI._update_title`` — just the two settable attrs."""
    monkeypatch.setattr(app_mod, "_get_version_info", lambda: ("1.2.3", "feat/foo"))
    monkeypatch.setattr(app_mod, "_short_version", lambda v: v)
    monkeypatch.setattr(app_mod.getpass, "getuser", lambda: "strazce")
    monkeypatch.setattr(app_mod.socket, "gethostname", lambda: "spark")
    return SimpleNamespace(title="", sub_title="")


class TestUpdateTitle:
    """The title carries version + branch; ``sub_title`` carries ``user@host``."""

    def test_title_includes_version_and_branch(self, title_stub: SimpleNamespace) -> None:
        """Title is unchanged from pre-#683 — version + branch in brackets."""
        TerokTUI._update_title(title_stub)
        assert title_stub.title == "Terok TUI v1.2.3 [feat/foo]"

    def test_sub_title_carries_user_at_host(self, title_stub: SimpleNamespace) -> None:
        """``sub_title`` is ``user@host`` — Textual renders that on the right."""
        TerokTUI._update_title(title_stub)
        assert title_stub.sub_title == "strazce@spark"

    def test_title_omits_branch_when_unknown(
        self, monkeypatch: pytest.MonkeyPatch, title_stub: SimpleNamespace
    ) -> None:
        """No branch → no brackets; sub_title is unaffected."""
        monkeypatch.setattr(app_mod, "_get_version_info", lambda: ("1.2.3", ""))
        TerokTUI._update_title(title_stub)
        assert title_stub.title == "Terok TUI v1.2.3"
        assert title_stub.sub_title == "strazce@spark"
