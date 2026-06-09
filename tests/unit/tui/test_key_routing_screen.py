# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the pure label and hint helpers of the key-routing screens."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from terok.tui.key_routing_screen import (
    _BaseRoutingScreen,
    _hint,
    _inventory_label,
    _key_label,
)


def _row(*, comment: str = "", key_type: str = "ed25519", fingerprint: str = "SHA256:abcdef"):
    """A stand-in key row carrying the fields the label helpers read."""
    return SimpleNamespace(comment=comment, key_type=key_type, fingerprint=fingerprint)


class TestKeyLabel:
    """A key's display label prefers its comment, else type + fingerprint."""

    def test_uses_comment_when_present(self):
        """A commented key shows its comment."""
        assert _key_label(_row(comment="tk-main:foo")) == "tk-main:foo"

    def test_falls_back_to_type_and_fingerprint(self):
        """A blank comment yields the key type and a 16-char fingerprint prefix."""
        label = _key_label(_row(comment="", fingerprint="SHA256:0123456789abcdef0"))
        assert label == "ed25519 SHA256:012345678"


class TestInventoryLabel:
    """The catalog row carries metadata and the projects served."""

    def test_lists_served_projects(self):
        """Linked scopes appear; type and fingerprint are spelled out in full."""
        label = _inventory_label(_row(comment="k"), ["bar", "foo"])
        assert "ed25519  SHA256:abcdef" in label
        assert "projects: bar, foo" in label

    def test_unlinked_key_shows_dash(self):
        """A key with no projects renders an em dash."""
        assert "projects: —" in _inventory_label(_row(), [])


class TestApplyGuard:
    """Every vault mutation funnels through _apply, which must catch failures."""

    def test_success_reloads_without_toast(self):
        """A clean mutation repaints and raises no notification."""
        duck = SimpleNamespace(app=mock.Mock(), reload=mock.Mock())
        _BaseRoutingScreen._apply(duck, lambda: None, "Boom")
        duck.reload.assert_called_once()
        duck.app.notify.assert_not_called()

    def test_failure_toasts_and_skips_reload(self):
        """A raising mutation is caught, surfaced as an error, and does not repaint."""
        duck = SimpleNamespace(app=mock.Mock(), reload=mock.Mock())

        def boom():
            raise RuntimeError("vault locked")

        _BaseRoutingScreen._apply(duck, boom, "Unlink failed")
        duck.app.notify.assert_called_once()
        assert "Unlink failed" in duck.app.notify.call_args[0][0]
        assert duck.app.notify.call_args.kwargs["severity"] == "error"
        duck.reload.assert_not_called()


class TestHint:
    """The footer-style hint flips the mode label and colours its keys."""

    def test_m_names_the_target_mode(self):
        """``m`` advertises the mode it switches to, not the current one."""
        assert "m[/] list mode" in _hint(list_mode=False)
        assert "m[/] matrix mode" in _hint(list_mode=True)

    def test_keys_wear_the_footer_colour(self):
        """Every shortcut key is wrapped in the footer key-colour variable."""
        assert "[$footer-key-foreground]space[/]" in _hint(list_mode=False)
        assert "[$footer-key-foreground]r[/]" in _hint(list_mode=False)
