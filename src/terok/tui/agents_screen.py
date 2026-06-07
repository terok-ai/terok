# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Picks the agent selection baked into an L1 image via a TUI modal.

Dismisses with the selection string in the executor's grammar
(``"all"`` / comma list / ``"all,-name"``) or ``None`` on cancel.
A master "All" checkbox cascades onto per-agent items; turning any
item off un-arms master so an enumeration never silently collapses
to the ``"all"`` literal (which means "this set plus future
additions" — a different commitment).
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Label, Rule

_MASTER_ALL = "all"  # nosec: B105 — selection token, not a secret
_MASTER_ID = "agents-select-all"
_ERROR_ID = "agents-select-error"


def _item_id(slug: str) -> str:
    return f"agents-select-item-{slug}"


class AgentsSelectScreen(ModalScreen[str | None]):
    """Modal picker for an agent-roster selection.

    *initial* is the prior selection (``"all"``, a comma list, or
    empty); empty preselects the master "All" checkbox so the modal
    opens in the "everything" state.  *title* shows on the dialog
    border so callers can distinguish global vs project-scope edits.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    AgentsSelectScreen {
        align: center middle;
    }

    #agents-select-dialog {
        width: 70;
        max-width: 100%;
        height: 90%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #agents-select-scroll {
        height: 1fr;
    }

    .agents-select-list {
        border: round $primary-darken-2;
        padding: 0 1;
        height: auto;
    }

    .agents-select-master {
        color: $accent;
    }

    .agents-select-sep {
        margin: 0 1;
    }

    .agents-select-help {
        color: $text-muted;
        height: auto;
        margin-bottom: 1;
    }

    .agents-select-error {
        color: $error;
        height: auto;
    }

    #agents-select-buttons {
        height: 3;
        align-horizontal: right;
        margin-top: 1;
    }

    #agents-select-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        *,
        initial: str | None = None,
        title: str = "Select agents",
        help_text: str = (
            "Pick 'All agents' to inherit any added in future releases, "
            "or enumerate specific agents to freeze the set."
        ),
    ) -> None:
        """Build the modal; the roster loads lazily in [`compose`][terok.tui.agents_screen.AgentsSelectScreen.compose]."""
        super().__init__()
        self._initial = (initial or "").strip()
        self._title = title
        self._help = help_text
        self._choices: tuple[tuple[str, str], ...] = ()
        self._master_cb: Checkbox | None = None
        self._item_cbs: dict[str, Checkbox] = {}

    def compose(self) -> ComposeResult:
        """Render the master + per-agent checkboxes and footer buttons."""
        from terok.lib.api.agents import AgentRoster

        roster = AgentRoster.shared()
        agents = roster.agents
        self._choices = tuple(
            (name, agents[name].label if name in agents else name) for name in roster.agent_names
        )

        preset_slugs, is_all = self._resolve_initial(roster)

        dialog = Vertical(id="agents-select-dialog")
        dialog.border_title = self._title
        with dialog:
            yield Label(self._help, classes="agents-select-help")
            with VerticalScroll(id="agents-select-scroll"):
                with Vertical(classes="agents-select-list"):
                    yield Checkbox(
                        "All agents (inherit any added in future releases)",
                        value=is_all,
                        id=_MASTER_ID,
                        classes="agents-select-master",
                        name=_MASTER_ALL,
                    )
                    yield Rule(line_style="dashed", classes="agents-select-sep")
                    for slug, label in self._choices:
                        yield Checkbox(
                            label,
                            value=slug in preset_slugs,
                            id=_item_id(slug),
                            name=slug,
                        )
            yield Label("", classes="agents-select-error", id=_ERROR_ID)
            with Horizontal(id="agents-select-buttons"):
                yield Button("Cancel", id="agents-select-cancel", variant="default")
                yield Button("Save", id="agents-select-save", variant="primary")

    def _resolve_initial(self, roster: object) -> tuple[set[str], bool]:
        """Map the *initial* string to ``(preset_slugs, master_on)``.

        Uses the executor's grammar parser for ``"all"`` / include /
        exclude tokens, then derives the literal set the user named —
        without the transitive ``depends_on`` expansion that
        [`AgentRoster.resolve_selection`][terok_executor.AgentRoster.resolve_selection]
        applies for builds, so a Save round-trip preserves what the user
        wrote (``"claude,vibe"`` round-trips as-is, even if claude
        depends on opencode at build time).
        """
        from terok.lib.api.agents import AgentRoster

        all_slugs: set[str] = set(roster.agent_names)  # type: ignore[attr-defined]
        if not self._initial:
            return all_slugs, True
        selection = AgentRoster.parse_selection(self._initial)
        if selection == _MASTER_ALL:
            return all_slugs, True
        includes = {t for t in selection if not t.startswith("-")}
        excludes = {t[1:] for t in selection if t.startswith("-")}
        seed = all_slugs if (_MASTER_ALL in includes or not includes) else includes
        return (seed - excludes) & all_slugs, False

    def on_mount(self) -> None:
        """Cache widget refs once so cascade + read don't re-query the DOM per item."""
        self._master_cb = self.query_one(f"#{_MASTER_ID}", Checkbox)
        self._item_cbs = {
            slug: self.query_one(f"#{_item_id(slug)}", Checkbox) for slug, _ in self._choices
        }

    # ``prevent`` short-circuits Checkbox.Changed synchronously so the
    # cascade-from-master writes below don't recurse into this handler.
    @on(Checkbox.Changed)
    def _on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        cb_id = event.checkbox.id or ""
        if cb_id == _MASTER_ID:
            self._cascade_from_master(target=event.checkbox.value)
            return
        if cb_id.startswith("agents-select-item-") and not event.checkbox.value:
            master = self._master_cb
            if master is not None and master.value:
                with master.prevent(Checkbox.Changed):
                    master.value = False

    def _cascade_from_master(self, *, target: bool) -> None:
        for item in self._item_cbs.values():
            with item.prevent(Checkbox.Changed):
                item.value = target

    def action_cancel(self) -> None:
        """Dismiss with ``None`` — caller treats as no change."""
        self.dismiss(None)

    @on(Button.Pressed, "#agents-select-cancel")
    def _on_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#agents-select-save")
    def _on_save(self) -> None:
        """Build the selection string; refuse empty with an inline error."""
        selection = self._read_selection()
        if not selection:
            self.query_one(f"#{_ERROR_ID}", Label).update(
                "Pick at least one agent (or 'All agents')."
            )
            return
        self.dismiss(selection)

    def _read_selection(self) -> str:
        master = self._master_cb
        if master is not None and master.value:
            return _MASTER_ALL
        return ",".join(slug for slug, cb in self._item_cbs.items() if cb.value)


__all__ = ["AgentsSelectScreen"]
