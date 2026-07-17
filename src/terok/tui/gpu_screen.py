# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Picks a project's ``run.gpus`` selector via a TUI modal.

Dismisses with the selector string (``""`` = passthrough off, ``"all"``,
or comma-joined tokens like ``"amd:1,intel"``) or ``None`` on cancel.

The modal is usable immediately: the master "All detected GPUs" checkbox
carries the happy path while a background worker runs the host probe —
detected devices appear as individual checkboxes when it lands, and a
failed or empty detection just leaves the master-only view (the selector
still resolves at launch time, where the authoritative errors live).
"""

from __future__ import annotations

import asyncio

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Rule

from ..lib.api import GpuDeviceChoice, detect_gpu_choices, validate_gpus

_GPU_ALL = "all"  # nosec: B105 — selector token, not a secret
_MASTER_ID = "gpu-select-all"
_ERROR_ID = "gpu-select-error"
_DEVICES_ID = "gpu-select-devices"
_SCROLL_ID = "gpu-select-scroll"
_CUSTOM_ID = "gpu-select-custom"
_PROBE_NOTE_ID = "gpu-select-probe-note"


def _item_id(index: int) -> str:
    return f"gpu-select-item-{index}"


class GpuSelectScreen(ModalScreen[str | None]):
    """Modal picker for the ``run.gpus`` selector.

    *initial* is the prior selector (``""`` = off); ``"all"`` or empty
    preselects the master checkbox.  A token list preselects matching
    device checkboxes once detection lands; tokens the probe didn't
    surface land in the custom field so nothing the user wrote is ever
    dropped on a round-trip.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    GpuSelectScreen {
        align: center middle;
    }

    #gpu-select-dialog {
        width: 70;
        max-width: 100%;
        height: auto;
        max-height: 100%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #gpu-select-scroll {
        /* Explicit height, updated from content when detection lands —
           an auto-height VerticalScroll greedily takes its max, which
           in turn inflates the auto-sized dialog to the full screen. */
        height: 3;
        max-height: 8;
    }

    .gpu-select-list {
        border: round $primary-darken-2;
        padding: 0 1;
        height: auto;
    }

    /* Compact one-row toggles: the default bordered Checkbox is three
       rows tall, which overflows small terminals and the scroll cap. */
    GpuSelectScreen Checkbox {
        border: none;
        padding: 0 1;
        height: auto;
    }

    GpuSelectScreen Checkbox:focus {
        text-style: bold;
        background: $boost;
    }

    .gpu-select-master {
        color: $accent;
    }

    .gpu-select-help {
        color: $text-muted;
        height: auto;
    }

    .gpu-select-error {
        color: $error;
        height: auto;
    }

    #gpu-select-buttons {
        height: 3;
        align-horizontal: right;
        margin-top: 1;
    }

    #gpu-select-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, *, initial: str = "") -> None:
        """Build the modal; device detection runs after mount, off-loop."""
        super().__init__()
        self._initial = initial.strip()
        self._choices: tuple[GpuDeviceChoice, ...] = ()
        self._pending_tokens: set[str] = set()

    def compose(self) -> ComposeResult:
        """Render the master checkbox, device slot, custom field, buttons."""
        initial_tokens = {t.strip() for t in self._initial.split(",") if t.strip()}
        is_all = not initial_tokens or _GPU_ALL in initial_tokens
        self._pending_tokens = initial_tokens - {_GPU_ALL}

        dialog = Vertical(id="gpu-select-dialog")
        dialog.border_title = "GPU passthrough"
        with dialog:
            yield Label(
                "Pass through all detected GPUs, or pick devices.",
                classes="gpu-select-help",
            )
            with VerticalScroll(id=_SCROLL_ID):
                with Vertical(classes="gpu-select-list"):
                    yield Checkbox(
                        "All detected GPUs",
                        value=is_all,
                        id=_MASTER_ID,
                        classes="gpu-select-master",
                        name=_GPU_ALL,
                    )
                    yield Rule(line_style="dashed")
                    with Vertical(id=_DEVICES_ID):
                        yield Label(
                            "Detecting devices…", id=_PROBE_NOTE_ID, classes="gpu-select-help"
                        )
            yield Label("Custom selector (wins over checkboxes):", classes="gpu-select-help")
            yield Input(
                value="",
                placeholder='e.g. "nvidia:0,amd" — vendors or vendor:N devices',
                id=_CUSTOM_ID,
            )
            yield Label("", classes="gpu-select-error", id=_ERROR_ID)
            with Horizontal(id="gpu-select-buttons"):
                yield Button("Off", id="gpu-select-off", variant="default")
                yield Button("Cancel", id="gpu-select-cancel", variant="default")
                yield Button("Apply", id="gpu-select-apply", variant="primary")

    async def on_mount(self) -> None:
        """Kick the host probe off-loop; the modal is usable meanwhile."""
        self.run_worker(self._load_devices(), exclusive=True)

    async def _load_devices(self) -> None:
        """Populate device checkboxes from the (thread-run) host probe."""
        choices = await asyncio.to_thread(detect_gpu_choices)
        self._choices = choices
        note = self.query_one(f"#{_PROBE_NOTE_ID}", Label)
        if not choices:
            note.update("No devices detected — 'all' still resolves at launch.")
            self._spill_pending_to_custom()
            return
        await note.remove()
        devices = self.query_one(f"#{_DEVICES_ID}", Vertical)
        master_on = self._master().value
        for i, choice in enumerate(choices):
            await devices.mount(
                Checkbox(
                    choice.label,
                    value=master_on or choice.token in self._pending_tokens,
                    id=_item_id(i),
                    name=choice.token,
                )
            )
        # Master + rule + one row per device (compact checkboxes), capped
        # by the stylesheet's max-height; scrolls only past the cap.
        self.query_one(f"#{_SCROLL_ID}").styles.height = 2 + len(choices)
        known = {c.token for c in choices}
        self._pending_tokens -= known
        self._spill_pending_to_custom()

    def _spill_pending_to_custom(self) -> None:
        """Round-trip tokens the probe didn't surface via the custom field."""
        if not self._pending_tokens:
            return
        custom = self.query_one(f"#{_CUSTOM_ID}", Input)
        if not custom.value:
            custom.value = ",".join(sorted(self._pending_tokens))
        self._pending_tokens = set()

    def _master(self) -> Checkbox:
        return self.query_one(f"#{_MASTER_ID}", Checkbox)

    @on(Checkbox.Changed)
    def _on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        cb_id = event.checkbox.id or ""
        if cb_id == _MASTER_ID:
            for cb in self.query(Checkbox):
                if cb.id != _MASTER_ID:
                    with cb.prevent(Checkbox.Changed):
                        cb.value = event.checkbox.value
            return
        if cb_id.startswith("gpu-select-item-") and not event.checkbox.value:
            master = self._master()
            if master.value:
                with master.prevent(Checkbox.Changed):
                    master.value = False

    def action_cancel(self) -> None:
        """Dismiss with ``None`` — caller treats as no change."""
        self.dismiss(None)

    @on(Button.Pressed, "#gpu-select-cancel")
    def _on_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#gpu-select-off")
    def _on_off(self) -> None:
        """Explicitly disable GPU passthrough for the project."""
        self.dismiss("")

    @on(Button.Pressed, "#gpu-select-apply")
    def _on_apply(self) -> None:
        """Build the selector; validate through sandbox's grammar."""
        selector = self._read_selector()
        error = validate_gpus(selector)
        if error is not None:
            self.query_one(f"#{_ERROR_ID}", Label).update(error)
            return
        self.dismiss(selector)

    def _read_selector(self) -> str:
        custom = self.query_one(f"#{_CUSTOM_ID}", Input).value.strip()
        if custom:
            return custom
        if self._master().value:
            return _GPU_ALL
        checked = [
            cb.name or ""
            for cb in self.query(Checkbox)
            if cb.id != _MASTER_ID and cb.value and cb.name
        ]
        # Initial tokens the probe hasn't materialized as checkboxes yet
        # (Apply racing _load_devices) must survive, not silently drop.
        pending = sorted(self._pending_tokens - set(checked))
        return ",".join([*checked, *pending])


__all__ = ["GpuSelectScreen"]
