# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Screens for managing vault SSH keys and their project links.

Two views over the same many-to-many relationship between deploy keys
and projects:

* [`KeyRoutingScreen`][terok.tui.key_routing_screen.KeyRoutingScreen] —
  the routing matrix (keys × projects), where the operator wires and
  unwires links directly.  A list-mode fallback (a key list beside a
  project checklist) covers terminals too narrow for the grid.
* [`KeyInventoryScreen`][terok.tui.key_routing_screen.KeyInventoryScreen]
  — the key catalog: mint a new key for a project, inspect a key's
  fingerprint and the projects it serves, or delete it everywhere.

Both drive the verbs in
[`terok.lib.api.ssh_routing`][terok.lib.api.ssh_routing] and share a
common base for the load → mutate → reload cycle.  They are reusable outside
``terok-tui``: [`KeyRoutingApp`][terok.tui.key_routing_screen.KeyRoutingApp]
runs the matrix as a standalone Textual app, mirroring ``terok clearance``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import screen
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Input, ListItem, ListView, OptionList, SelectionList, Static
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from terok.lib.api.ssh_routing import (
    KeyRouting,
    delete_key,
    is_last_link,
    link_key,
    load_key_routing,
    mint_key,
    rename_key,
    unlink_key,
)

from .screens import ConfirmDestructiveScreen, _modal_binding
from .widgets.routing_matrix import MatrixKey, RoutingMatrix

if TYPE_CHECKING:
    from collections.abc import Callable

    from terok.lib.integrations.sandbox import SSHKeyRow


class _BaseRoutingScreen(screen.Screen[None]):
    """The load → mutate → reload cycle shared by both routing views.

    Each mutating verb (mint, unlink, delete, rename) re-fetches and
    repaints, so the view always reflects the vault rather than an
    optimistic guess.  Unlinking a key's last link, or deleting a key,
    destroys the keypair — the vault keeps no unassigned keys — so both
    confirm first.  Subclasses supply ``_sync_widgets`` to paint the
    loaded [`KeyRouting`][terok.lib.api.ssh_routing.KeyRouting].
    """

    def __init__(self) -> None:
        """Begin with no routing loaded."""
        super().__init__()
        self._routing: KeyRouting | None = None

    def reload(self) -> None:
        """Re-fetch the routing and repaint, surfacing a locked vault as a toast."""
        try:
            self._routing = load_key_routing()
        except Exception as exc:  # noqa: BLE001 — any DB-open failure is operator-facing
            self._routing = None
            self.app.notify(f"Vault unavailable: {exc}", severity="error")
            return
        self._sync_widgets(self._routing)

    def action_reload(self) -> None:
        """Refresh on demand."""
        self.reload()

    def _sync_widgets(self, routing: KeyRouting) -> None:
        """Render *routing* into the subclass's widgets."""
        raise NotImplementedError

    def _mint(self, scope: str) -> None:
        """Mint a key for *scope*."""
        self._apply(lambda: mint_key(scope), "Mint failed")

    def _link(self, scope: str, key_id: int) -> None:
        """Grant project *scope* access to *key_id*."""
        self._apply(lambda: link_key(scope, key_id), "Link failed")

    def _unlink(self, scope: str, key_id: int) -> None:
        """Unlink a cell, confirming first when it is the key's last link."""
        if self._routing is not None and is_last_link(self._routing, scope, key_id):
            self.app.push_screen(
                ConfirmDestructiveScreen(
                    f"{scope} is the only project linked to this key.\n"
                    "Unlinking it deletes the keypair from the vault.",
                    title="Delete key?",
                    confirm_label="Delete",
                ),
                lambda ok: self._apply_unlink(scope, key_id) if ok else self.reload(),
            )
        else:
            self._apply_unlink(scope, key_id)

    def _apply_unlink(self, scope: str, key_id: int) -> None:
        """Persist an unlink and repaint."""
        self._apply(lambda: unlink_key(scope, key_id), "Unlink failed")

    def _delete(self, key_id: int) -> None:
        """Delete a key everywhere, after confirmation."""
        key = self._key_by_id(key_id)
        label = _key_label(key) if key is not None else f"key #{key_id}"
        self.app.push_screen(
            ConfirmDestructiveScreen(
                f"Delete key '{label}'?\nIt will be unlinked from every project.",
                title="Delete key?",
                confirm_label="Delete",
            ),
            lambda ok: self._apply_delete(key_id) if ok else None,
        )

    def _apply_delete(self, key_id: int) -> None:
        """Persist a key deletion and repaint."""
        self._apply(lambda: delete_key(key_id), "Delete failed")

    def _rename(self, key_id: int) -> None:
        """Prompt for a new comment for *key_id* and apply it."""
        key = self._key_by_id(key_id)
        if key is None:
            return
        self.app.push_screen(
            _RenameScreen(key.comment, _key_label(key)),
            lambda comment: self._apply_rename(key.fingerprint, comment),
        )

    def _apply_rename(self, fingerprint: str, comment: str | None) -> None:
        """Persist a comment edit, unless the prompt was cancelled."""
        if comment is not None:
            self._apply(lambda: rename_key(fingerprint, comment), "Rename failed")

    def _apply(self, mutate: Callable[[], object], failure: str) -> None:
        """Run a vault mutation, repainting on success and toasting on failure.

        Every routing mutation funnels through here so a backend error
        (a locked vault, a rejected comment) surfaces as a notification
        instead of escaping the event handler.
        """
        try:
            mutate()
        except Exception as exc:  # noqa: BLE001 — any vault failure is operator-facing
            self.app.notify(f"{failure}: {exc}", severity="error")
            return
        self.reload()

    def _key_by_id(self, key_id: int) -> SSHKeyRow | None:
        """Look up the loaded key row for *key_id*, or ``None``."""
        if self._routing is None:
            return None
        return next((k for k in self._routing.keys if k.id == key_id), None)


class KeyRoutingScreen(_BaseRoutingScreen):
    """The routing matrix plus a master-detail list fallback."""

    BINDINGS = [
        _modal_binding("escape", "dismiss_screen", "Back"),
        _modal_binding("q", "dismiss_screen", "Back"),
        _modal_binding("m", "toggle_mode", "Matrix / list"),
        _modal_binding("c", "rename_cursor_key", "Rename key"),
        _modal_binding("d", "delete_cursor_key", "Delete key"),
        _modal_binding("i", "show_inventory", "Inventory"),
        _modal_binding("r", "reload", "Refresh"),
        _modal_binding("h", "focus_keys", "Keys pane"),
        _modal_binding("l", "focus_projects", "Projects pane"),
    ]

    CSS = """
    KeyRoutingScreen { layout: vertical; background: $background; }
    #kr-header { height: 1; background: $primary; color: $text; padding: 0 1; }
    #kr-hint { height: 1; color: $text-muted; padding: 0 1; }
    #kr-list { height: 1fr; }
    #kr-keys { width: 40%; border: round $primary; }
    #kr-projects { width: 1fr; border: round $primary; }
    """

    def __init__(self) -> None:
        """Start in matrix mode."""
        super().__init__()
        self._list_mode = False

    def compose(self) -> ComposeResult:
        """Header, the matrix, and the (initially hidden) list-mode panes."""
        yield Static(" SSH Key Routing", id="kr-header")
        yield Static(_hint(list_mode=False), id="kr-hint")
        with VerticalScroll(id="kr-matrix"):
            yield RoutingMatrix()
        with Horizontal(id="kr-list"):
            keys = ListView(id="kr-keys")
            keys.border_title = "Keys"
            yield keys
            projects: SelectionList[str] = SelectionList(id="kr-projects")
            projects.border_title = "Linked projects"
            yield projects

    def on_mount(self) -> None:
        """Load routing, show the matrix, hide the list panes."""
        self.query_one("#kr-list").display = False
        self.reload()
        self.query_one(RoutingMatrix).focus()

    def _sync_widgets(self, routing: KeyRouting) -> None:
        """Feed routing to the matrix, and to the list panes when active."""
        self.query_one(RoutingMatrix).set_routing(
            [MatrixKey(k.id, _key_label(k)) for k in routing.keys],
            list(routing.projects),
            set(routing.links),
        )
        if self._list_mode:
            self._sync_list_mode(routing)

    # ── Matrix intents ─────────────────────────────────────────────────

    def on_routing_matrix_cell_toggled(self, event: RoutingMatrix.CellToggled) -> None:
        """Wire a link, or unwire one (confirming a last link)."""
        if event.linked:
            self._unlink(event.scope, event.key_id)
        else:
            self._link(event.scope, event.key_id)

    def on_routing_matrix_mint_requested(self, event: RoutingMatrix.MintRequested) -> None:
        """Mint a fresh key for the cursor column."""
        self._mint(event.scope)

    # ── List-mode intents ──────────────────────────────────────────────

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Repopulate the project checklist for the highlighted key."""
        if self._routing is not None and event.item is not None:
            self._fill_checklist(self._routing, _item_key_id(event.item))

    def on_selection_list_selection_toggled(
        self, event: SelectionList.SelectionToggled[str]
    ) -> None:
        """Translate a checklist tick into a link, a clear into an unlink."""
        key_id = self._highlighted_key_id()
        if key_id is None:
            return
        scope = event.selection.value
        if scope in event.selection_list.selected:
            self._link(scope, key_id)
        else:
            self._unlink(scope, key_id)

    # ── Actions ────────────────────────────────────────────────────────

    def action_toggle_mode(self) -> None:
        """Swap between the matrix grid and the list panes."""
        self._list_mode = not self._list_mode
        self.query_one("#kr-matrix").display = not self._list_mode
        self.query_one("#kr-list").display = self._list_mode
        self.query_one("#kr-hint", Static).update(_hint(list_mode=self._list_mode))
        if self._routing is not None:
            self._sync_widgets(self._routing)
        self.query_one("#kr-keys" if self._list_mode else RoutingMatrix).focus()

    def action_focus_keys(self) -> None:
        """Move to the key list (list mode)."""
        if self._list_mode:
            self.query_one("#kr-keys").focus()

    def action_focus_projects(self) -> None:
        """Move to the project checklist (list mode)."""
        if self._list_mode:
            self.query_one("#kr-projects").focus()

    def action_rename_cursor_key(self) -> None:
        """Rename the key the cursor points at (matrix or list)."""
        if (key_id := self._current_key_id()) is not None:
            self._rename(key_id)

    def action_delete_cursor_key(self) -> None:
        """Delete the key the cursor points at (matrix or list)."""
        if (key_id := self._current_key_id()) is not None:
            self._delete(key_id)

    def _current_key_id(self) -> int | None:
        """The key the operator is pointing at — list highlight or matrix cursor."""
        if self._list_mode:
            return self._highlighted_key_id()
        key = self.query_one(RoutingMatrix).cursor_key
        return key.key_id if key is not None else None

    def action_show_inventory(self) -> None:
        """Open the key inventory; reload on return."""
        self.app.push_screen(KeyInventoryScreen(), lambda _: self.reload())

    def action_dismiss_screen(self) -> None:
        """Close the routing screen."""
        self.dismiss(None)

    # ── List-mode rendering ────────────────────────────────────────────

    def _sync_list_mode(self, routing: KeyRouting) -> None:
        """Rebuild the key list (keeping the highlight) and the checklist."""
        key_list = self.query_one("#kr-keys", ListView)
        previous = self._highlighted_key_id()
        key_list.clear()
        for key in routing.keys:
            key_list.append(_KeyItem(key.id, _key_label(key)))
        if routing.keys:
            index = next((i for i, k in enumerate(routing.keys) if k.id == previous), 0)
            key_list.index = index
            self._fill_checklist(routing, routing.keys[index].id)

    def _fill_checklist(self, routing: KeyRouting, key_id: int) -> None:
        """Show every project as a checkbox, ticked where the key is linked."""
        checklist = self.query_one("#kr-projects", SelectionList)
        checklist.clear_options()
        checklist.add_options(
            Selection(scope, scope, (scope, key_id) in routing.links) for scope in routing.projects
        )

    def _highlighted_key_id(self) -> int | None:
        """The key highlighted in the list-mode key pane, if any."""
        item = self.query_one("#kr-keys", ListView).highlighted_child
        return _item_key_id(item) if item is not None else None


class KeyInventoryScreen(_BaseRoutingScreen):
    """A catalog of every vault key — mint, inspect, and delete."""

    BINDINGS = [
        _modal_binding("escape", "dismiss_screen", "Back"),
        _modal_binding("q", "dismiss_screen", "Back"),
        _modal_binding("n", "mint", "Mint key"),
        _modal_binding("c", "rename", "Rename key"),
        _modal_binding("d", "delete", "Delete key"),
        _modal_binding("r", "reload", "Refresh"),
    ]

    CSS = """
    KeyInventoryScreen { layout: vertical; background: $background; }
    #ki-header { height: 1; background: $primary; color: $text; padding: 0 1; }
    #ki-list { height: 1fr; border: round $primary; }
    """

    def compose(self) -> ComposeResult:
        """Header plus the scrollable key list."""
        yield Static(" SSH Key Inventory   n mint · c rename · d delete · Esc back", id="ki-header")
        listing = ListView(id="ki-list")
        listing.border_title = "Keys"
        yield listing

    def on_mount(self) -> None:
        """Load the catalog and focus the list."""
        self.reload()
        self.query_one("#ki-list", ListView).focus()

    def _sync_widgets(self, routing: KeyRouting) -> None:
        """Render one row per key: metadata and the projects it serves."""
        listing = self.query_one("#ki-list", ListView)
        previous = listing.index or 0
        listing.clear()
        for key in routing.keys:
            scopes = sorted(s for s, k in routing.links if k == key.id)
            listing.append(_KeyItem(key.id, _inventory_label(key, scopes)))
        if routing.keys:
            listing.index = min(previous, len(routing.keys) - 1)

    def action_mint(self) -> None:
        """Pick a project, then mint a key for it."""
        if self._routing is not None:
            self.app.push_screen(
                _ProjectPickerScreen(self._routing.projects), self._mint_for_picked
            )

    def _mint_for_picked(self, scope: str | None) -> None:
        """Mint for the picked project, or do nothing on cancel."""
        if scope:
            self._mint(scope)

    def action_rename(self) -> None:
        """Rename the highlighted key's comment."""
        item = self.query_one("#ki-list", ListView).highlighted_child
        if item is not None:
            self._rename(_item_key_id(item))

    def action_delete(self) -> None:
        """Delete the highlighted key everywhere."""
        item = self.query_one("#ki-list", ListView).highlighted_child
        if item is not None:
            self._delete(_item_key_id(item))

    def action_dismiss_screen(self) -> None:
        """Close the inventory."""
        self.dismiss(None)


class _ProjectPickerScreen(screen.ModalScreen[str | None]):
    """A modal that returns the project the operator picks (or ``None``)."""

    BINDINGS = [_modal_binding("escape", "cancel", "Cancel")]

    CSS = """
    _ProjectPickerScreen { align: center middle; }
    #picker { width: 50; height: auto; max-height: 80%; border: round $primary; background: $surface; }
    """

    def __init__(self, projects: tuple[str, ...]) -> None:
        """Remember the projects to offer."""
        super().__init__()
        self._projects = projects

    def compose(self) -> ComposeResult:
        """A single option list of project names."""
        picker = OptionList(*(Option(p, id=p) for p in self._projects), id="picker")
        picker.border_title = "Mint key for project"
        yield picker

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Return the chosen project."""
        self.dismiss(event.option_id)

    def action_cancel(self) -> None:
        """Dismiss without picking."""
        self.dismiss(None)


class _RenameScreen(screen.ModalScreen[str | None]):
    """A modal that returns a key's new comment (or ``None`` on cancel)."""

    BINDINGS = [_modal_binding("escape", "cancel", "Cancel")]

    CSS = """
    _RenameScreen { align: center middle; }
    #rename-input { width: 60; background: $surface; }
    """

    def __init__(self, comment: str, label: str) -> None:
        """Prefill the input with the key's current comment."""
        super().__init__()
        self._comment = comment
        self._label = label

    def compose(self) -> ComposeResult:
        """A single-line input prefilled with the current comment."""
        box = Input(value=self._comment, id="rename-input")
        box.border_title = f"Rename {self._label}"
        box.border_subtitle = "Enter to save · Esc to cancel"
        yield box

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Return the typed comment on Enter."""
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        """Dismiss without renaming."""
        self.dismiss(None)


class _KeyItem(ListItem):
    """A list row that carries the key id behind its rendered label."""

    def __init__(self, key_id: int, label: str) -> None:
        """Wrap *label* and stash *key_id* for the action handlers."""
        super().__init__(Static(label, markup=False))
        self.key_id = key_id


class KeyRoutingApp(App[None]):
    """Standalone Textual app that opens only the routing matrix.

    Lets another tool — or ``python -m`` — drive the key↔project matrix
    without the rest of ``terok-tui``, the same way ``terok clearance``
    ships [`ClearanceScreen`][terok.tui.clearance_screen.ClearanceScreen].
    """

    TITLE = "terok ssh-key routing"

    def compose(self) -> ComposeResult:
        """Pair an app-level footer with the pushed routing screen."""
        yield Footer()

    def on_mount(self) -> None:
        """Push the routing screen, exiting when it is dismissed."""
        self.push_screen(KeyRoutingScreen(), callback=lambda _: self.exit())


def main() -> None:
    """Entry point for running the routing matrix standalone."""
    KeyRoutingApp().run()


def _key_label(key: SSHKeyRow) -> str:
    """A key's comment, or its type and fingerprint when it has none."""
    return key.comment or f"{key.key_type} {key.fingerprint[:16]}"


def _inventory_label(key: SSHKeyRow, scopes: list[str]) -> str:
    """A catalog row: label, type, full fingerprint, and the projects served."""
    served = ", ".join(scopes) if scopes else "—"
    return f"{_key_label(key)}\n  {key.key_type}  {key.fingerprint}\n  projects: {served}"


def _item_key_id(item: ListItem) -> int:
    """Read the key id stashed on a list row."""
    return item.key_id  # type: ignore[attr-defined]


def _hint(*, list_mode: bool) -> str:
    """The key hint for the routing screen, shortcuts in the footer's key colour.

    The ``m`` shortcut names the mode it switches *to*, flipping between
    "list mode" and "matrix mode".
    """
    target_mode = "matrix" if list_mode else "list"
    shortcuts = [
        ("space", "link/unlink"),
        ("n", "mint"),
        ("c", "rename"),
        ("d", "delete"),
        ("m", f"{target_mode} mode"),
        ("i", "inventory"),
        ("r", "refresh"),
    ]
    return "  ·  ".join(f"[$footer-key-foreground]{key}[/] {label}" for key, label in shortcuts)
