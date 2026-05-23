# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Modal that offers the two SELinux-policy fixes after a setup exit 5.

The sandbox ``setup`` CLI handler exits with code 5 when all install
phases succeed but the host's SELinux policy is missing — see
[terok-ai/terok-sandbox#298](https://github.com/terok-ai/terok-sandbox/pull/298).
The TUI catches that exit code and pushes this screen.

Two paths forward:

- **Install the policy (sudo bash …)** — dispatches the bundled
  installer script as a console action; the user authenticates to
  sudo in the captured-log view.  After a successful install, the
  caller re-runs setup.
- **Switch to TCP mode** — flips ``services.mode`` to ``tcp`` in the
  user-scope ``config.yml``; no SELinux policy is required after the
  flip.  The caller re-runs setup.

A *Skip* button bails out without changing anything (the operator
can re-open the same modal from the command palette by re-running
setup).
"""

from __future__ import annotations

import enum
from collections.abc import Iterator

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class SelinuxFixOutcome(enum.Enum):
    """User's pick from [`SelinuxFixScreen`][terok.tui.selinux_fix_screen.SelinuxFixScreen]."""

    INSTALL_POLICY = "install_policy"
    """Run ``sudo bash <install_script>`` to load ``terok_socket_t``."""

    SWITCH_TO_TCP = "switch_to_tcp"
    """Write ``services.mode: tcp`` to the user config and re-run setup."""

    SKIPPED = "skipped"
    """Dismiss without changing anything."""


class SelinuxFixScreen(ModalScreen[SelinuxFixOutcome]):
    """Modal that surfaces the two remediations for a setup exit-5 finish."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("i", "install_policy", "Install policy"),
        Binding("t", "switch_to_tcp", "Switch to TCP"),
        Binding("s", "skip", "Skip"),
    ]

    CSS = """
    SelinuxFixScreen {
        align: center middle;
    }

    #selinux-fix-dialog {
        width: 80;
        max-width: 100%;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #selinux-fix-headline {
        height: auto;
        margin-bottom: 1;
    }

    #selinux-fix-blurb {
        color: $text-muted;
        height: auto;
        margin-bottom: 1;
    }

    #selinux-fix-buttons {
        height: auto;
        align-horizontal: right;
    }

    #selinux-fix-buttons Button {
        margin-left: 1;
    }
    """

    def compose(self) -> ComposeResult:
        """Build the modal: headline, explanation, three buttons."""
        dialog = Vertical(id="selinux-fix-dialog")
        dialog.border_title = "SELinux policy required"
        with dialog:
            yield Static(
                "Sandbox setup finished, but the terok_socket_t SELinux policy "
                "isn't loaded — containers can't reach the host sockets without it.",
                id="selinux-fix-headline",
            )
            yield Label(
                "Pick one of the two remediations below.  Both run setup again "
                "afterwards so the install can complete cleanly.",
                id="selinux-fix-blurb",
            )
            with Horizontal(id="selinux-fix-buttons"):
                yield from self._buttons()

    @staticmethod
    def _buttons() -> Iterator[Button]:
        """Three buttons: Skip, Switch-to-TCP, Install-policy (primary)."""
        yield Button("[s]kip", id="selinux-fix-skip", variant="default")
        yield Button("Switch to [t]CP mode", id="selinux-fix-tcp", variant="default")
        yield Button("[i]nstall policy (sudo)", id="selinux-fix-install", variant="primary")

    def action_close(self) -> None:
        """Esc dismisses with [`SelinuxFixOutcome.SKIPPED`][terok.tui.selinux_fix_screen.SelinuxFixOutcome.SKIPPED]."""
        self.dismiss(SelinuxFixOutcome.SKIPPED)

    def action_install_policy(self) -> None:
        """Key binding for Install policy."""
        self.dismiss(SelinuxFixOutcome.INSTALL_POLICY)

    def action_switch_to_tcp(self) -> None:
        """Key binding for Switch to TCP."""
        self.dismiss(SelinuxFixOutcome.SWITCH_TO_TCP)

    def action_skip(self) -> None:
        """Key binding for Skip."""
        self.dismiss(SelinuxFixOutcome.SKIPPED)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route the three button IDs to their dismissal outcomes."""
        match event.button.id:
            case "selinux-fix-install":
                self.dismiss(SelinuxFixOutcome.INSTALL_POLICY)
            case "selinux-fix-tcp":
                self.dismiss(SelinuxFixOutcome.SWITCH_TO_TCP)
            case "selinux-fix-skip":
                self.dismiss(SelinuxFixOutcome.SKIPPED)
