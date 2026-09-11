# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Picks a project's curated egress sets (``shield.sets``) via a TUI modal.

Dismisses with a tuple of set names, or ``None`` on cancel.  With the
master "Recommended" checkbox armed the tuple holds just
[`RECOMMENDED_SET`][terok.lib.core.egress_sets.RECOMMENDED_SET] — every
curated set, including sets added in future releases; otherwise it holds
the checked sets, an explicit and frozen selection (empty = no curated
sets).  Mirrors the agents picker's master-checkbox cascade: turning any
item off un-arms master.  Checking every item by hand leaves master off,
because an enumeration is a different commitment than "recommended,
including future sets".
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Label, Rule

_MASTER_ID = "shield-sets-recommended"
_ITEM_PREFIX = "shield-sets-item-"


def _item_id(slug: str) -> str:
    return f"{_ITEM_PREFIX}{slug}"


class ShieldSetsScreen(ModalScreen[tuple[str, ...] | None]):
    """Modal picker for a project's curated egress sets.

    *initial* is the project's current ``shield.sets``: a selection that
    holds ``recommended`` arms master and checks every set; any other
    selection checks exactly its sets; ``None`` or an empty selection
    checks nothing.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    ShieldSetsScreen {
        align: center middle;
    }

    #shield-sets-dialog {
        width: 78;
        max-width: 100%;
        height: 90%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #shield-sets-scroll {
        height: 1fr;
    }

    .shield-sets-list {
        border: round $primary-darken-2;
        padding: 0 1;
        height: auto;
    }

    .shield-sets-master {
        color: $accent;
    }

    .shield-sets-sep {
        margin: 0 1;
    }

    .shield-sets-help {
        color: $text-muted;
        height: auto;
        margin-bottom: 1;
    }

    #shield-sets-buttons {
        height: 3;
        align-horizontal: right;
        margin-top: 1;
    }

    #shield-sets-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, *, initial: tuple[str, ...] | None, title: str = "Egress sets") -> None:
        """Build the modal; the set registry loads in [`compose`][terok.tui.shield_sets_screen.ShieldSetsScreen.compose]."""
        super().__init__()
        self._initial = initial
        self._title = title
        self._choices: tuple[str, ...] = ()
        self._master_cb: Checkbox | None = None
        self._item_cbs: dict[str, Checkbox] = {}

    def compose(self) -> ComposeResult:
        """Render the master + per-set checkboxes and footer buttons."""
        from terok.lib.api import (
            EGRESS_SETS,
            OS_PACKAGES_SUMMARY,
            RECOMMENDED_SET,
            selected_egress_sets,
        )

        self._choices = tuple(EGRESS_SETS)
        is_recommended = RECOMMENDED_SET in (self._initial or ())
        preset = set(selected_egress_sets(self._initial))

        dialog = Vertical(id="shield-sets-dialog")
        dialog.border_title = self._title
        with dialog:
            yield Label(
                "Curated egress allowlists granted to this project's tasks while the "
                "shield is up.  Recommended grants every curated set, including sets "
                "added in future releases; an explicit selection freezes the set; "
                "unchecking everything grants no curated sets.",
                classes="shield-sets-help",
            )
            with VerticalScroll(id="shield-sets-scroll"):
                with Vertical(classes="shield-sets-list"):
                    yield Checkbox(
                        "Recommended (every curated set, including future ones)",
                        value=is_recommended,
                        id=_MASTER_ID,
                        classes="shield-sets-master",
                        name=RECOMMENDED_SET,
                    )
                    yield Rule(line_style="dashed", classes="shield-sets-sep")
                    for slug, hosts in EGRESS_SETS.items():
                        label = slug if hosts else f"{slug} ({OS_PACKAGES_SUMMARY})"
                        yield Checkbox(label, value=slug in preset, id=_item_id(slug), name=slug)
            with Horizontal(id="shield-sets-buttons"):
                yield Button("Cancel", id="shield-sets-cancel", variant="default")
                yield Button("Save", id="shield-sets-save", variant="primary")

    def on_mount(self) -> None:
        """Cache widget refs once so cascade + read don't re-query the DOM per item."""
        self._master_cb = self.query_one(f"#{_MASTER_ID}", Checkbox)
        self._item_cbs = {
            slug: self.query_one(f"#{_item_id(slug)}", Checkbox) for slug in self._choices
        }

    # ``prevent`` short-circuits Checkbox.Changed synchronously so the
    # cascade-from-master writes below don't recurse into this handler.
    @on(Checkbox.Changed)
    def _on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        cb_id = event.checkbox.id or ""
        if cb_id == _MASTER_ID:
            for item in self._item_cbs.values():
                with item.prevent(Checkbox.Changed):
                    item.value = event.checkbox.value
            return
        if cb_id.startswith(_ITEM_PREFIX) and not event.checkbox.value:
            master = self._master_cb
            if master is not None and master.value:
                with master.prevent(Checkbox.Changed):
                    master.value = False

    def action_cancel(self) -> None:
        """Dismiss with ``None`` — caller treats as no change."""
        self.dismiss(None)

    @on(Button.Pressed, "#shield-sets-cancel")
    def _on_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#shield-sets-save")
    def _on_save(self) -> None:
        """Dismiss with the recommended meta-set or the explicit (possibly empty) tuple."""
        from terok.lib.api import RECOMMENDED_SET

        master = self._master_cb
        if master is not None and master.value:
            self.dismiss((RECOMMENDED_SET,))
            return
        self.dismiss(tuple(slug for slug, cb in self._item_cbs.items() if cb.value))


__all__ = ["ShieldSetsScreen"]
