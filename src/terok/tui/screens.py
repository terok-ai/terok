#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Full-page and modal Textual screens for the terok TUI."""

from typing import TYPE_CHECKING, Any

from textual import events, screen
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

# Optional textual widget imports: TUI tests build a stub textual module
# without all widgets, so each import is wrapped in try/except. The real
# (non-stub) types are exposed under TYPE_CHECKING so static analysis sees
# concrete classes instead of `<class> | None`.
if TYPE_CHECKING:
    from textual.binding import Binding
    from textual.timer import Timer
    from textual.widgets import Input, OptionList, TextArea
    from textual.widgets.option_list import Option

    from .console_log import ConsoleLogEntry
else:
    try:  # pragma: no cover - optional import for test stubs
        from textual.widgets import OptionList
    except Exception:  # pragma: no cover - textual may be a stub module
        OptionList = None

    try:  # pragma: no cover - optional import for test stubs
        from textual.widgets.option_list import Option
    except Exception:  # pragma: no cover - textual may be a stub module
        Option = None

    try:  # pragma: no cover - optional import for test stubs
        from textual.binding import Binding
    except Exception:  # pragma: no cover - textual may be a stub module
        Binding = None

    try:  # pragma: no cover - optional import for test stubs
        from textual.widgets import TextArea
    except Exception:  # pragma: no cover - textual may be a stub module
        TextArea = None

    try:  # pragma: no cover - optional import for test stubs
        from textual.widgets import Input
    except Exception:  # pragma: no cover - textual may be a stub module
        Input = None

if TYPE_CHECKING:
    from textual.widgets import Select
else:
    try:  # pragma: no cover - optional import for test stubs
        from textual.widgets import Select
    except Exception:  # pragma: no cover - textual may be a stub module
        Select = None

from rich.style import Style
from rich.text import Text

from terok.lib.api.gate import GateStalenessInfo
from terok.lib.api.setup import EnvironmentCheck
from terok.lib.api.vault import VaultStatusSnapshot

from ..lib.api import ProjectConfig, sanitize_task_name, validate_task_name
from .widgets import TaskMeta, render_project_details, render_project_loading, render_task_details


def _modal_binding(key: str, action: str, description: str) -> Any:
    """Create a Binding (or plain tuple fallback) for modal screen key shortcuts."""
    if Binding is None:
        return (key, action, description)
    return Binding(key, action, description, show=False)


# ---------------------------------------------------------------------------
# Shared CSS for full-page detail screens
# ---------------------------------------------------------------------------

_DETAIL_SCREEN_CSS = """
    #detail-content {
        height: auto;
        max-height: 50%;
        border: round $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
        margin: 1;
        overflow-y: auto;
    }

    #actions-list {
        height: 1fr;
        margin: 0 1;
    }
"""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _visible_agents(installed: frozenset[str] | None) -> list[str]:
    """Provider names visible to the user given an *installed* filter.

    Empty/``None`` *installed* (legacy or unlabeled image) → no filtering;
    every known provider is shown.  Otherwise the registry is intersected
    with the install set, preserving registry order.
    """
    from terok.lib.api.agents import AGENTS

    if not installed:
        return list(AGENTS)
    return [name for name in AGENTS if name in installed]


# A border subtitle is assembled from independent hint segments; the
# focus- and readiness-dependent ones evaluate to ``None`` and drop out, so the
# rendered hint never promises a key that wouldn't do what it says right now.
_HINT_SEP = " · "


def _join_hints(*segments: str | None) -> str:
    """Join the present hint segments with the standard separator, dropping blanks."""
    return _HINT_SEP.join(segment for segment in segments if segment)


# ---------------------------------------------------------------------------
# Project Details Screen
# ---------------------------------------------------------------------------


class ProjectDetailsScreen(screen.Screen[str | None]):
    """Full-page detail screen for a project with categorized actions."""

    BINDINGS = [
        _modal_binding("escape", "dismiss", "Back"),
        _modal_binding("q", "dismiss", "Back"),
        _modal_binding("i", "project_init", "Full Setup"),
        _modal_binding("g", "sync_gate", "Sync git gate"),
        _modal_binding("d", "generate", "Generate dockerfiles"),
        _modal_binding("b", "build", "Build project image"),
        _modal_binding("r", "build_agents", "Rebuild L1 with fresh agents"),
        _modal_binding("f", "build_full", "Full rebuild from L0 (no cache)"),
        _modal_binding("s", "init_ssh", "Init SSH"),
        _modal_binding("a", "auth", "Authenticate"),
        _modal_binding("A", "set_agents", "Set agents"),
        _modal_binding("I", "edit_instructions", "Edit instructions"),
        _modal_binding("t", "toggle_inherit", "Toggle inherit"),
        _modal_binding("v", "show_resolved", "Show resolved instructions"),
        _modal_binding("D", "delete_project", "Delete project"),
    ]

    CSS = (
        """
    ProjectDetailsScreen {
        layout: vertical;
        background: $background;
    }
    """
        + _DETAIL_SCREEN_CSS
    )

    def __init__(
        self,
        project: ProjectConfig,
        state: dict | None,
        task_count: int | None,
        staleness: GateStalenessInfo | None = None,
    ) -> None:
        """Store the project data to render when the screen is mounted."""
        super().__init__()
        self._project = project
        self._state = state
        self._task_count = task_count
        self._staleness = staleness

    def compose(self) -> ComposeResult:
        """Build the detail pane and categorized action list for a project."""
        detail_pane = Static(id="detail-content")
        detail_pane.border_title = f"Project: {self._project.name}"
        detail_pane.border_subtitle = "Esc to close"
        yield detail_pane

        yield OptionList(
            Option(
                "Full Setup - project-\\[i]nit  (ssh + generate + build + gate-sync)",
                id="project_init",
            ),
            Option("sync \\[g]it gate", id="sync_gate"),
            None,
            Option("generate \\[d]ockerfiles", id="generate"),
            Option("\\[b]uild project image", id="build"),
            Option("\\[r]ebuild L1 with fresh agents", id="build_agents"),
            Option("\\[f]ull rebuild from L0 (no cache)", id="build_full"),
            Option("initialize \\[s]sh", id="init_ssh"),
            None,
            Option("\\[a]uthenticate...", id="auth"),
            Option("set \\[A]gents (image.agents in project.yml)...", id="set_agents"),
            None,
            Option("edit \\[I]nstructions", id="edit_instructions"),
            Option("\\[t]oggle instructions inherit", id="toggle_inherit"),
            Option("\\[v]iew resolved instructions", id="show_resolved"),
            None,
            Option("\\[D]elete project", id="delete_project"),
            id="actions-list",
        )

    def on_mount(self) -> None:
        """Render project details and focus the action list."""
        detail_widget = self.query_one("#detail-content", Static)
        if self._state is not None:
            rendered = render_project_details(
                self._project, self._state, self._task_count, self._staleness
            )
        else:
            rendered = render_project_loading(self._project, self._task_count)
        detail_widget.update(rendered)
        actions = self.query_one("#actions-list", OptionList)
        actions.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Dismiss with the chosen action ID, or open a sub-modal for nested flows."""
        option_id = event.option_id
        if option_id == "auth":
            self._open_auth_modal()
        elif option_id == "set_agents":
            self._open_agents_modal()
        elif option_id:
            self.dismiss(option_id)

    def _open_auth_modal(self) -> None:
        """Push the authentication provider selection modal."""
        self.app.push_screen(AuthActionsScreen(), self._on_auth_result)

    def _on_auth_result(self, result: str | None) -> None:
        """Forward the selected auth action from the sub-modal as this screen's result."""
        if result:
            self.dismiss(result)

    def _open_agents_modal(self) -> None:
        """Push the shared agents picker seeded with this project's current value."""
        from terok.tui.agents_screen import AgentsSelectScreen

        self.app.push_screen(
            AgentsSelectScreen(
                initial=self._project.agents,
                title=f"Agents for {self._project.name}",
            ),
            self._on_agents_modal_result,
        )

    def _on_agents_modal_result(self, selection: str | None) -> None:
        """Persist the new selection to ``project.yml``; ``None`` = no change."""
        if selection is None:
            return
        from terok.lib.api import set_project_image_agents

        path = set_project_image_agents(self._project.name, selection)
        # Keep the cached config in sync so a re-open of the modal in
        # this same screen instance seeds from the freshly-saved value.
        # ``ProjectConfig`` is frozen, hence the model_copy.
        self._project = self._project.model_copy(update={"agents": selection})
        self.notify(
            f"Wrote image.agents = {selection!r} to {path}",
            severity="information",
        )

    # Action methods invoked by BINDINGS
    async def action_dismiss(self, result: str | None = None) -> None:
        """Close the screen without selecting an action."""
        self.dismiss(result)

    def action_project_init(self) -> None:
        """Trigger the full project initialization pipeline."""
        self.dismiss("project_init")

    def action_sync_gate(self) -> None:
        """Trigger git gate synchronization."""
        self.dismiss("sync_gate")

    def action_generate(self) -> None:
        """Trigger Dockerfile generation."""
        self.dismiss("generate")

    def action_build(self) -> None:
        """Trigger project image build."""
        self.dismiss("build")

    def action_build_agents(self) -> None:
        """Trigger agent image rebuild."""
        self.dismiss("build_agents")

    def action_build_full(self) -> None:
        """Trigger a full no-cache rebuild."""
        self.dismiss("build_full")

    def action_init_ssh(self) -> None:
        """Trigger SSH directory initialization."""
        self.dismiss("init_ssh")

    def action_auth(self) -> None:
        """Open the authenticate agents and tools modal."""
        self._open_auth_modal()

    def action_set_agents(self) -> None:
        """Open the per-project agent multi-select modal."""
        self._open_agents_modal()

    def action_edit_instructions(self) -> None:
        """Open instructions for editing."""
        self.dismiss("edit_instructions")

    def action_toggle_inherit(self) -> None:
        """Toggle instructions inheritance mode."""
        self.dismiss("toggle_inherit")

    def action_show_resolved(self) -> None:
        """Show fully resolved instructions."""
        self.dismiss("show_resolved")

    def action_delete_project(self) -> None:
        """Trigger project deletion."""
        self.dismiss("delete_project")


# ---------------------------------------------------------------------------
# Auth Actions Modal (sub-modal of ProjectDetailsScreen)
# ---------------------------------------------------------------------------


class AuthActionsScreen(screen.ModalScreen[str | None]):
    """Small modal for authenticating agents and tools.

    Options are built dynamically from ``AUTH_PROVIDERS``.
    Number keys (1-9) act as shortcuts for the corresponding list entry.
    """

    BINDINGS = [
        _modal_binding("escape", "dismiss", "Cancel"),
        _modal_binding("q", "dismiss", "Cancel"),
    ]

    CSS = """
    AuthActionsScreen {
        align: center middle;
    }

    #auth-dialog {
        width: 50;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #auth-actions-list {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        """Build the numbered list of authentication providers."""
        from terok.lib.api.agents import AUTH_PROVIDERS

        providers = list(AUTH_PROVIDERS.values())
        options: list[Option | None] = [
            Option(f"\\[{i}] {p.label}", id=f"auth_{p.name}")
            for i, p in enumerate(providers, 1)
            if i <= 9
        ]
        next_num = len(providers) + 1
        options.append(None)
        import_label = (
            f"\\[{next_num}] Import OpenCode config" if next_num <= 9 else "Import OpenCode config"
        )
        options.append(Option(import_label, id="import_opencode_config"))
        with Vertical(id="auth-dialog") as dialog:
            yield OptionList(*options, id="auth-actions-list")
        dialog.border_title = "Authenticate agents and tools"
        dialog.border_subtitle = "Esc to close"

    def on_mount(self) -> None:
        """Focus the auth provider list on mount."""
        actions = self.query_one("#auth-actions-list", OptionList)
        actions.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Dismiss with the selected provider's action ID."""
        if event.option_id:
            self.dismiss(event.option_id)

    def on_key(self, event: events.Key) -> None:
        """Handle number-key shortcuts (1-9) to select a provider or import."""
        from terok.lib.api.agents import AUTH_PROVIDERS

        if event.character and event.character.isdigit():
            idx = int(event.character) - 1
            providers = list(AUTH_PROVIDERS.values())
            if 0 <= idx < min(len(providers), 9):
                self.dismiss(f"auth_{providers[idx].name}")
                event.stop()
            elif idx == len(providers) and idx < 9:
                self.dismiss("import_opencode_config")
                event.stop()

    async def action_dismiss(self, result: str | None = None) -> None:
        """Close the auth modal without selecting a provider."""
        self.dismiss(result)


# ---------------------------------------------------------------------------
# OpenCode Config Import Screen
# ---------------------------------------------------------------------------


class OpenCodeConfigScreen(screen.ModalScreen[str | None]):
    """Modal for entering a file path to import as OpenCode config.

    Validates that the file exists and contains valid JSON, then copies it
    to the shared ``_opencode-config`` mount.  Dismisses with the
    destination path on success, or ``None`` if cancelled.
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    OpenCodeConfigScreen {
        align: center middle;
    }

    #opencode-config-dialog {
        width: 70;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #opencode-config-input {
        margin-bottom: 1;
    }

    #opencode-config-buttons {
        height: auto;
        align-horizontal: right;
    }

    #opencode-config-buttons Button {
        margin-left: 1;
    }
    """

    def compose(self) -> ComposeResult:
        """Build the file path input and OK/Cancel buttons."""
        with Vertical(id="opencode-config-dialog") as dialog:
            yield Input(
                placeholder="/path/to/opencode.json",
                id="opencode-config-input",
            )
            with Horizontal(id="opencode-config-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Import", id="btn-import", variant="primary")
        dialog.border_title = "Import OpenCode Config"
        dialog.border_subtitle = "Esc to cancel"

    def on_mount(self) -> None:
        """Focus the file path input for immediate typing."""
        inp = self.query_one("#opencode-config-input", Input)
        inp.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle Import or Cancel button clicks."""
        if event.button.id == "btn-import":
            self._submit()
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: "Input.Submitted") -> None:
        """Accept on Enter key press."""
        self._submit()

    def _submit(self) -> None:
        """Validate the file path and copy the config to the shared mount."""
        import json
        import shutil
        from pathlib import Path

        from ..lib.api import get_config

        inp = self.query_one("#opencode-config-input", Input)
        raw = inp.value.strip()
        if not raw:
            self.notify("File path cannot be empty.")
            return

        src = Path(raw).expanduser()
        if not src.is_file():
            self.notify(f"File not found: {src}")
            return

        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            self.notify(f"Cannot read config: {e}")
            return
        if not isinstance(data, dict):
            self.notify("Invalid config: expected a JSON object")
            return

        try:
            dest_dir = get_config().vault_dir / "_opencode-config"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / "opencode.json"
            shutil.copy2(str(src), str(dest))
        except OSError as e:
            self.notify(f"Copy failed: {e}")
            return

        self.dismiss(str(dest))

    def action_cancel(self) -> None:
        """Cancel the import and dismiss without a result."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Auth: API key entry + mode choice
# ---------------------------------------------------------------------------


class ApiKeyEntryScreen(screen.ModalScreen[str | None]):
    """Modal for collecting an API key from the operator.

    Replaces the CLI's ``prompt_toolkit`` input — the TUI dispatches
    actions into a ``stdin=DEVNULL`` subprocess, so the executor's
    interactive ``_prompt_api_key`` would always see EOF (issue: API key
    entry broken from TUI).  Dismisses with the stripped key string on
    submit, or ``None`` on cancel.

    The ``api_key_hint`` from the provider's roster entry is shown above
    the input so the operator knows where to obtain the token.
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    ApiKeyEntryScreen {
        align: center middle;
    }

    #api-key-dialog {
        width: 80;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #api-key-hint {
        margin-bottom: 1;
        color: $text-muted;
    }

    #api-key-input {
        margin-bottom: 1;
    }

    #api-key-buttons {
        height: auto;
        align-horizontal: right;
    }

    #api-key-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, provider_name: str) -> None:
        """Build the screen for *provider_name*; provider must be in ``AUTH_PROVIDERS``."""
        super().__init__()
        self._provider_name = provider_name

    def compose(self) -> ComposeResult:
        """Build the hint label, masked input, and Cancel/Save buttons."""
        from terok.lib.api import AUTH_PROVIDERS

        info = AUTH_PROVIDERS.get(self._provider_name)
        label = info.label if info else self._provider_name
        hint = (info.api_key_hint if info else "").strip()

        with Vertical(id="api-key-dialog") as dialog:
            yield Static(hint or "Paste your API key below.", id="api-key-hint")
            yield Input(
                placeholder=f"{label} API key",
                password=True,
                id="api-key-input",
            )
            with Horizontal(id="api-key-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Save", id="btn-save", variant="primary")
        dialog.border_title = f"Authenticate {label}"
        dialog.border_subtitle = "Esc to cancel"

    def on_mount(self) -> None:
        """Focus the input so the operator can type immediately."""
        inp = self.query_one("#api-key-input", Input)
        inp.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route Save / Cancel button presses."""
        if event.button.id == "btn-save":
            self._submit()
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: "Input.Submitted") -> None:
        """Accept Enter as Save."""
        self._submit()

    def _submit(self) -> None:
        """Strip whitespace and dismiss with the key — empty input is a no-op."""
        inp = self.query_one("#api-key-input", Input)
        key = inp.value.strip()
        if not key:
            self.notify("API key cannot be empty.")
            return
        self.dismiss(key)

    def action_cancel(self) -> None:
        """Dismiss without a result."""
        self.dismiss(None)


class AuthModeScreen(screen.ModalScreen["str | None"]):
    """Modal for picking ``oauth`` vs ``api_key`` when a provider supports both.

    Mirrors the executor CLI's ``Choose [1/2]:`` prompt but as a proper
    Textual choice.  Dismisses with ``"oauth"``, ``"api_key"``, or
    ``None`` on cancel.
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
        _modal_binding("q", "cancel", "Cancel"),
    ]

    CSS = """
    AuthModeScreen {
        align: center middle;
    }

    #auth-mode-dialog {
        width: 60;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #auth-mode-list {
        height: auto;
    }
    """

    def __init__(self, provider_name: str) -> None:
        """Build the screen for *provider_name*; provider must support both modes."""
        super().__init__()
        self._provider_name = provider_name

    def compose(self) -> ComposeResult:
        """Build the two-choice list (OAuth vs API key)."""
        from terok.lib.api import AUTH_PROVIDERS

        info = AUTH_PROVIDERS.get(self._provider_name)
        label = info.label if info else self._provider_name
        options = [
            Option("\\[1] OAuth / interactive login (launches container)", id="oauth"),
            Option("\\[2] API key (paste key, no container needed)", id="api_key"),
        ]
        with Vertical(id="auth-mode-dialog") as dialog:
            yield OptionList(*options, id="auth-mode-list")
        dialog.border_title = f"Authenticate {label}"
        dialog.border_subtitle = "Esc to cancel"

    def on_mount(self) -> None:
        """Focus the choice list."""
        actions = self.query_one("#auth-mode-list", OptionList)
        actions.focus()

    def on_option_list_option_selected(self, event: "OptionList.OptionSelected") -> None:
        """Dismiss with the selected mode id."""
        if event.option_id:
            self.dismiss(event.option_id)

    def on_key(self, event: events.Key) -> None:
        """``1`` → oauth, ``2`` → api_key."""
        if event.character == "1":
            self.dismiss("oauth")
            event.stop()
        elif event.character == "2":
            self.dismiss("api_key")
            event.stop()

    def action_cancel(self) -> None:
        """Dismiss without a result."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Unattended Prompt Screen
# ---------------------------------------------------------------------------


class UnattendedPromptScreen(screen.ModalScreen[str | None]):
    """Modal for entering an unattended prompt.

    A modal dialog that prompts the user to enter a prompt for the unattended
    (headless Claude) mode. The user can enter their prompt in a text area and
    submit it or cancel.

    The screen dismisses with the prompt string if submitted, or ``None``
    if cancelled (e.g. via Escape or the Cancel button).
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    UnattendedPromptScreen {
        align: center middle;
    }

    #unattended-dialog {
        width: 80;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #prompt-area {
        height: 8;
        margin-bottom: 1;
    }

    #prompt-buttons {
        height: auto;
        align-horizontal: right;
    }

    #prompt-buttons Button {
        margin-left: 1;
    }
    """

    def compose(self) -> ComposeResult:
        """Build the prompt text area and submit/cancel buttons."""
        with Vertical(id="unattended-dialog") as dialog:
            yield _SubmittablePromptArea(id="prompt-area")
            with Horizontal(id="prompt-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Run ▶", id="btn-run", variant="primary")
        dialog.border_title = "Unattended Prompt"

    def on_mount(self) -> None:
        """Focus the text area for immediate typing."""
        area = self.query_one("#prompt-area", TextArea)
        area.focus()
        self._refresh_hint()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Refresh the Enter hint whenever focus moves between the dialog's controls."""
        self._refresh_hint()

    def _refresh_hint(self) -> None:
        """Rebuild the border subtitle to match what Enter does for the focused control."""
        prompt_focused = self.focused is not None and self.focused.id == "prompt-area"
        subtitle = _join_hints(
            "Ctrl+J newline" if prompt_focused else None,
            "Esc cancel",
            "Enter to run" if prompt_focused else None,
        )
        self.query_one("#unattended-dialog", Vertical).border_subtitle = subtitle

    def on_key(self, event: events.Key) -> None:
        """Submit on Enter (bubbled from the prompt area); modifiers add newlines.

        Newline insertion (Ctrl+Enter / Shift+Enter / Ctrl+J) is owned by
        `_SubmittablePromptArea` and never reaches here.
        """
        if event.key == "enter" and self.query_one("#prompt-area", TextArea).has_focus:
            self._submit()
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle Run or Cancel button clicks."""
        if event.button.id == "btn-run":
            self._submit()
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

    def _submit(self) -> None:
        """Dismiss with the entered prompt text if non-empty."""
        area = self.query_one("#prompt-area", TextArea)
        text = area.text.strip()
        if text:
            self.dismiss(text)

    def action_cancel(self) -> None:
        """Cancel the prompt and dismiss without a result."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Agent Selection Screen (agent + optional sub-agents)
# ---------------------------------------------------------------------------


class AgentSelectionScreen(screen.ModalScreen[str | None]):
    """Modal for selecting the unattended agent.

    Lists all registered headless agents (Claude, Codex, etc.) with the project
    default marked ``*``.  Number keys (1-9) act as shortcuts for agent
    selection.

    Dismisses with the chosen ``agent_name`` on OK, or ``None`` if cancelled.
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    AgentSelectionScreen {
        align: center middle;
    }

    #agent-dialog {
        width: 60;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #agent-list {
        height: auto;
        max-height: 10;
        margin-bottom: 1;
    }

    #agent-buttons {
        height: auto;
        align-horizontal: right;
    }

    #agent-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        default_agent: str = "claude",
        installed: frozenset[str] | None = None,
    ) -> None:
        """Create the agent selection screen.

        Args:
            default_agent: Name of the project's default agent (pre-highlighted
                and marked with ``*``).
            installed: Names baked into the project's L1 image (from the
                ``ai.terok.agents`` label).  When provided and non-empty,
                the picker hides agents not in the set.  ``None`` or empty
                means no filtering — every known agent is shown.
        """
        super().__init__()
        self._installed = installed

        from terok.lib.api.agents import AGENTS

        visible = _visible_agents(installed)
        if default_agent in AGENTS and (not installed or default_agent in installed):
            self._default_agent: str | None = default_agent
        elif visible:
            self._default_agent = visible[0]
        else:
            # Misconfigured selection (e.g. only tool-kind entries installed):
            # the modal renders an empty list and OK is rejected — user can only cancel.
            self._default_agent = None
        self._selected_agent: str | None = self._default_agent

    def compose(self) -> ComposeResult:
        """Build the agent list and buttons."""
        from terok.lib.api.agents import AGENTS

        with Vertical(id="agent-dialog") as dialog:
            options = []
            visible = _visible_agents(self._installed)
            for i, name in enumerate(visible, 1):
                agent = AGENTS[name]
                marker = " *" if agent.name == self._default_agent else ""
                options.append(Option(f"\\[{i}] {agent.label}{marker}", id=agent.name))
            yield OptionList(*options, id="agent-list")

            with Horizontal(id="agent-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("OK", id="btn-ok", variant="primary")
        dialog.border_title = "Select Agent"
        dialog.border_subtitle = "Esc to cancel  (* = default)"

    def on_mount(self) -> None:
        """Focus the agent list and highlight the default entry."""
        agent_list = self.query_one("#agent-list", OptionList)

        for idx, name in enumerate(_visible_agents(self._installed)):
            if name == self._default_agent:
                agent_list.highlighted = idx
                break
        agent_list.focus()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Track the currently highlighted agent as the selection."""
        if event.option_id:
            self._selected_agent = event.option_id

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Confirm agent choice on Enter and advance focus to OK."""
        if event.option_id:
            self._selected_agent = event.option_id
        self.query_one("#btn-ok", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle OK or Cancel button clicks."""
        if event.button.id == "btn-ok":
            self._submit()
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

    def _submit(self) -> None:
        """Dismiss with the selected agent.

        Rejects the submission when no agent is selectable (empty visible
        list) so the caller never receives a falsy agent name.
        """
        agent = self._selected_agent
        if not agent:
            self.notify("No agents available — rebuild the image or adjust selection.")
            return
        self.dismiss(agent)

    def on_key(self, event: events.Key) -> None:
        """Handle number-key shortcuts (1-9) to select an agent."""
        if event.character and event.character.isdigit():
            idx = int(event.character) - 1
            visible = _visible_agents(self._installed)
            if 0 <= idx < len(visible):
                self._selected_agent = visible[idx]
                agent_list = self.query_one("#agent-list", OptionList)
                agent_list.highlighted = idx
                event.stop()

    def action_cancel(self) -> None:
        """Cancel agent selection and dismiss without a result."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Task Name Screen (name input for new task or rename)
# ---------------------------------------------------------------------------


class TaskNameScreen(screen.ModalScreen[str | None]):
    """Modal for entering or editing a task name.

    Dismisses with the name string if submitted, or ``None`` if cancelled.
    Pre-fills the input with a default (generated or current) name.
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    TaskNameScreen {
        align: center middle;
    }

    #name-dialog {
        width: 60;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #name-input {
        margin-bottom: 1;
    }

    #name-buttons {
        height: auto;
        align-horizontal: right;
    }

    #name-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, default_name: str = "") -> None:
        """Create the name screen with a pre-filled default name."""
        super().__init__()
        self._default_name = default_name

    def compose(self) -> ComposeResult:
        """Build the name input field and OK/Cancel buttons."""
        with Vertical(id="name-dialog") as dialog:
            yield Input(
                value=self._default_name,
                placeholder="task-name",
                id="name-input",
            )
            with Horizontal(id="name-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("OK", id="btn-ok", variant="primary")
        dialog.border_title = "Task Name"
        dialog.border_subtitle = "Esc to cancel"

    def on_mount(self) -> None:
        """Focus the name input for immediate editing."""
        inp = self.query_one("#name-input", Input)
        inp.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle OK or Cancel button clicks."""
        if event.button.id == "btn-ok":
            self._submit()
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: "Input.Submitted") -> None:
        """Accept the name on Enter key press."""
        self._submit()

    def _submit(self) -> None:
        """Validate and dismiss with the sanitized name, or show an error."""
        inp = self.query_one("#name-input", Input)
        raw = inp.value.strip()
        # Fall back to default if field is blank, then run full validation pipeline
        candidate = raw or self._default_name
        if not candidate:
            self.notify("Name cannot be empty.")
            return
        sanitized = sanitize_task_name(candidate)
        if sanitized is None:
            self.notify("Invalid name: must contain at least one alphanumeric character.")
            return
        err = validate_task_name(sanitized)
        if err:
            self.notify(f"Invalid name: {err}.")
            return
        self.dismiss(sanitized)

    def action_cancel(self) -> None:
        """Cancel the name input and dismiss without a result."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Task Create Screen (name + mode selection)
# ---------------------------------------------------------------------------


class TaskCreateScreen(screen.ModalScreen["tuple[str, str] | None"]):
    """Modal for creating a new task: name input + mode selection.

    Dismisses with ``(sanitized_name, mode)`` or ``None`` if cancelled.
    Mode is one of ``"cli"``, ``"toad"``, ``"unattended"``.
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    TaskCreateScreen {
        align: center middle;
    }

    #create-dialog {
        width: 60;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #create-name-input {
        margin-bottom: 1;
    }

    #create-buttons {
        height: auto;
        align-horizontal: right;
    }

    #create-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, default_name: str = "") -> None:
        """Create the task creation screen with a pre-filled default name."""
        super().__init__()
        self._default_name = default_name

    def compose(self) -> ComposeResult:
        """Build the name input, mode option list, and Cancel button."""
        with Vertical(id="create-dialog") as dialog:
            yield Input(
                value=self._default_name,
                placeholder="task-name",
                id="create-name-input",
            )
            options = [
                Option("CLI", id="cli"),
                Option("Toad (browser TUI)", id="toad"),
                Option("Unattended (headless)", id="unattended"),
            ]
            yield OptionList(*options, id="create-mode-list")
            with Horizontal(id="create-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
        dialog.border_title = "New Task"
        dialog.border_subtitle = "Esc to cancel"

    def on_mount(self) -> None:
        """Focus the name input for immediate editing."""
        inp = self.query_one("#create-name-input", Input)
        inp.focus()

    def on_input_submitted(self, event: "Input.Submitted") -> None:
        """On Enter in the name input, submit with the highlighted mode."""
        self._submit_with_highlighted_mode()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Submit when a mode is selected from the option list."""
        mode = event.option_id
        if mode:
            self._submit(mode)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle Cancel button click."""
        if event.button.id == "btn-cancel":
            self.dismiss(None)

    def _submit_with_highlighted_mode(self) -> None:
        """Submit using the currently highlighted mode option (default: cli)."""
        mode_list = self.query_one("#create-mode-list", OptionList)
        idx = mode_list.highlighted
        if idx is not None and 0 <= idx < mode_list.option_count:
            option = mode_list.get_option_at_index(idx)
            self._submit(option.id or "cli")
        else:
            self._submit("cli")

    def _submit(self, mode: str) -> None:
        """Validate the name and dismiss with ``(name, mode)``."""
        inp = self.query_one("#create-name-input", Input)
        raw = inp.value.strip()
        candidate = raw or self._default_name
        if not candidate:
            self.notify("Name cannot be empty.")
            return
        sanitized = sanitize_task_name(candidate)
        if sanitized is None:
            self.notify("Invalid name: must contain at least one alphanumeric character.")
            return
        err = validate_task_name(sanitized)
        if err:
            self.notify(f"Invalid name: {err}.")
            return
        self.dismiss((sanitized, mode))

    def action_cancel(self) -> None:
        """Cancel and dismiss without a result."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Task Launch Screen (CLI launch modal with agent selector + prompt)
# ---------------------------------------------------------------------------


if TextArea is not None:

    class _SubmittablePromptArea(TextArea):
        """Multiline prompt where Enter confirms and a modifier inserts a newline.

        The stock TextArea treats ``enter`` as "insert a newline", which is the
        wrong reflex for a short prompt sitting in front of a confirm button.
        Here the roles are swapped to match the rest of the app:

        * **Enter** is suppressed locally and left to bubble to the host
          screen's ``on_key``, which submits the dialog (or ignores it when
          there is nothing to confirm).
        * **Ctrl+Enter**, **Shift+Enter**, and **Ctrl+J** insert a literal
          newline and stop there — the screen never sees them.

        Terminal caveat: most terminals send the same byte for Enter and for
        Ctrl/Shift+Enter, so the latter two only arrive as distinct keys under
        the enhanced (Kitty) keyboard protocol. ``ctrl+j`` is the one newline
        key that works on every terminal.

        ``event.prevent_default()`` on Enter is load-bearing: without it
        Textual still runs TextArea's own ``enter`` binding (insert ``\\n``)
        after this handler returns. ``event.stop()`` is deliberately omitted
        for Enter so the host screen's ``on_key`` still receives it.
        """

        _NEWLINE_KEYS = frozenset({"ctrl+enter", "shift+enter", "ctrl+j"})

        async def _on_key(self, event: events.Key) -> None:
            if event.key in self._NEWLINE_KEYS:
                self.insert("\n")
                event.prevent_default()
                event.stop()
                return
            if event.key == "enter":
                event.prevent_default()
                return
            await super()._on_key(event)
else:  # pragma: no cover - stub TextArea in test envs
    _SubmittablePromptArea = TextArea  # type: ignore[assignment,misc]


class TaskLaunchScreen(
    screen.ModalScreen["tuple[str, str, str, str, str | None, str | None] | None"]
):
    """Post-creation modal for CLI tasks: agent selection + optional prompt.

    Dismisses with ``(project_name, task_id, task_name, container_name,
    agent, prompt)``.  ``agent`` is the chosen shell/agent on Login and
    ``None`` on Dismiss — but the *prompt* travels either way, so a prompt
    typed before dismissing is preserved for the next manual ``login``
    rather than lost.  The full launch context is captured at creation time
    so the callback is immune to selection changes.
    """

    BINDINGS = [
        _modal_binding("escape", "dismiss_screen", "Dismiss"),
    ]

    CSS = """
    TaskLaunchScreen {
        align: center middle;
    }

    #launch-dialog {
        width: 70;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #launch-status {
        margin-bottom: 1;
    }

    #login-agent {
        margin-bottom: 1;
    }

    #launch-prompt {
        margin-bottom: 1;
        height: 10;
    }

    #launch-buttons {
        height: auto;
        align-horizontal: right;
    }

    #launch-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        container_name: str,
        project_name: str,
        task_id: str,
        task_name: str | None = "",
        default_shell: str = "bash",
        installed: frozenset[str] | None = None,
        console_entry: "ConsoleLogEntry | None" = None,
    ) -> None:
        """Create the launch screen with container context and default agent.

        *installed* is the set of agents present in the project's L1 image
        (from the ``ai.terok.agents`` label).  ``None`` means the lookup is
        still in flight — the agent dropdown renders disabled with only
        ``bash`` as a placeholder until the caller invokes
        [`set_installed`][terok.tui.screens.TaskLaunchScreen.set_installed]
        with the resolved set.  A non-empty frozenset filters the dropdown
        to those agents; an empty frozenset means no filter (legacy /
        unlabeled images) — every known provider is shown.
        """
        super().__init__()
        self._container_name = container_name
        self._project_name = project_name
        self._task_id = task_id
        self._task_name = task_name or task_id
        self._default_shell = default_shell
        self._installed = installed
        # ConsoleLogEntry for the background container start.  The start
        # stays backgrounded; "Show log" is the only thing that foregrounds
        # its WorkerLogScreen view.
        self._console_entry = console_entry
        self._container_ready = False
        self._poll_timer: Timer | None = None
        self._probe_in_flight = False
        self._start_time = 0.0

    def compose(self) -> ComposeResult:
        """Build prompt input, agent selector, status, and action buttons."""
        with Vertical(id="launch-dialog") as dialog:
            yield Static("Status: Starting container\u2026", id="launch-status")

            # Prompt input first (multiline TextArea with Ctrl+Enter for newline)
            yield _SubmittablePromptArea(
                placeholder="Initial prompt (optional)", id="launch-prompt"
            )

            # bash is always offered (login shell); the rest are filtered by
            # what's installed in the project's L1 image \u2014 populated lazily
            # by ``set_installed`` once the (slow) image inspect returns.
            loading = self._installed is None
            choices = self._build_agent_choices()
            valid_values = {v for _, v in choices}
            login_value = self._default_shell if self._default_shell in valid_values else "bash"
            yield Select(
                choices,
                value=login_value,
                id="login-agent",
                disabled=loading,
                prompt="Loading agents\u2026" if loading else "Select an agent",
            )

            with Horizontal(id="launch-buttons"):
                if self._console_entry is not None:
                    yield Button("Show log", id="btn-show-log", variant="default")
                yield Button("Dismiss", id="btn-dismiss", variant="default")
                yield Button("Login", id="btn-login", variant="primary", disabled=True)
        dialog.border_title = f"CLI Task {self._task_id} ({self._task_name})"

    def _build_agent_choices(self) -> list[tuple[str, str]]:
        """Build the (label, value) list for the agent Select.

        Returns only ``bash`` while the installed-agents lookup is in
        flight (``_installed is None``); once populated, prepends ``bash``
        to the visible providers.
        """
        from terok.lib.api.agents import AGENTS

        choices: list[tuple[str, str]] = [("bash", "bash")]
        if self._installed is None:
            return choices
        for name in _visible_agents(self._installed):
            p = AGENTS[name]
            choices.append((p.label, p.name))
        return choices

    def set_installed(self, installed: frozenset[str]) -> None:
        """Populate the agent dropdown once the background lookup finishes.

        Replaces the placeholder ``bash``-only choice list with the real
        set of installed agents, restores the configured *default_shell*
        if it's now selectable, and enables the dropdown.  Safe to call
        before ``compose`` has run \u2014 in that case it just updates state
        and the dropdown renders enabled on first mount.
        """
        self._installed = installed
        try:
            select = self.query_one("#login-agent", Select)
        except Exception:  # pragma: no cover - compose hasn't run yet
            return
        choices = self._build_agent_choices()
        select.set_options(choices)
        # ``set_options`` resets the value; pick the configured login if it's
        # still available, else fall back to ``bash`` (always in choices) so
        # the dropdown is never left blank.
        valid_values = {v for _, v in choices}
        select.value = self._default_shell if self._default_shell in valid_values else "bash"
        select.prompt = "Select an agent"
        select.disabled = False

    def on_mount(self) -> None:
        """Start polling for container readiness and focus the prompt input."""
        import time

        prompt = self.query_one("#launch-prompt", TextArea)
        prompt.focus()
        self._refresh_hint()
        self._start_time = time.monotonic()
        self._poll_timer = self.set_interval(1.5, self._poll_status)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Refresh the Enter hint whenever focus moves between the dialog's controls."""
        self._refresh_hint()

    def _enter_hint(self) -> str | None:
        """What Enter does for the focused control right now, or ``None`` when nothing.

        Enter reaches login only from the prompt, and only once the container is
        ready — until then it's a dimmed, hourglassed promise that brightens in
        place the moment login goes live. On the agent ``Select`` Enter expands the
        dropdown; on the action buttons it activates the focused one, whose own
        caption already says what that is — so those drop the segment rather than
        echo the label.
        """
        focused = self.focused
        focused_id = focused.id if focused else None
        if focused_id == "launch-prompt":
            return "Enter login" if self._container_ready else "[dim]Enter login ⌛[/dim]"
        if focused_id == "login-agent":
            return "Enter shows agent list"
        return None

    def _refresh_hint(self) -> None:
        """Rebuild the border subtitle for the current focus and readiness state."""
        prompt_focused = self.focused is not None and self.focused.id == "launch-prompt"
        subtitle = _join_hints(
            "Ctrl+J newline" if prompt_focused else None,
            "Esc dismiss",
            self._enter_hint(),
        )
        self.query_one("#launch-dialog", Vertical).border_subtitle = subtitle

    def on_key(self, event: events.Key) -> None:
        """Submit on Enter (bubbled from the prompt) once the container is ready.

        Newline insertion (Ctrl+Enter / Shift+Enter / Ctrl+J) is owned by
        `_SubmittablePromptArea` and never reaches here; Tab still cycles
        focus by default.
        """
        # Only act on Enter while the prompt TextArea holds focus.
        if not self.query_one("#launch-prompt", TextArea).has_focus:
            return
        if event.key == "enter" and self._container_ready:
            self._do_login()
            event.stop()

    # If no container has appeared within this many wall-clock seconds, assume
    # the launch failed and surface a hint.  Wall-clock based (not tick count)
    # so the hint still fires when a probe is wedged on ``podman inspect``.
    _POLL_STALL_TIMEOUT_S = 90.0

    def _probe_readiness(self) -> tuple[str | None, bool]:
        """Synchronous readiness probe — runs in a worker thread.

        ``podman inspect`` can stall for several seconds the first time a
        freshly built image is referenced; doing it here keeps the event
        loop free so the modal stays dismissible.
        """
        from ..lib.api import get_container_state, get_task_meta

        state = get_container_state(self._container_name)
        has_mode = False
        if state == "running":
            try:
                has_mode = get_task_meta(self._project_name, self._task_id).mode is not None
            except (SystemExit, Exception) as exc:
                from ..lib.util.logging_utils import _log_debug

                _log_debug(
                    f"Task meta fetch failed for {self._project_name}/{self._task_id}: {exc}"
                )
                has_mode = False
        return state, has_mode

    async def _poll_status(self) -> None:
        """Check container state and task mode; enable Login only when fully ready.

        A task is fully ready when both conditions are met:
        1. The container is in "running" state (podman says so).
        2. The task metadata has a ``mode`` set (the runner finished init).
        This prevents premature Login attempts before init scripts complete.

        If the container never appears after many polls, updates the status
        to indicate a likely launch failure so the user can dismiss.
        """
        import asyncio
        import time

        elapsed = time.monotonic() - self._start_time
        stalled = elapsed >= self._POLL_STALL_TIMEOUT_S
        status_widget = self.query_one("#launch-status", Static)

        # The probe shells out to podman; skip overlapping ticks so a slow
        # subprocess can't fan out into a backlog of in-flight threads.
        # The stall hint still fires here so a wedged ``podman inspect`` can
        # surface a failure to the user.
        if self._probe_in_flight:
            if stalled:
                status_widget.update("Status: Launch may have failed \u2014 check notifications")
            return

        self._probe_in_flight = True
        try:
            state, has_mode = await asyncio.to_thread(self._probe_readiness)
        finally:
            self._probe_in_flight = False

        if state == "running" and has_mode:
            status_widget.update("Status: Container ready")
            self._container_ready = True
            self.query_one("#btn-login", Button).disabled = False
            self._refresh_hint()
            if self._poll_timer:
                self._poll_timer.stop()
                self._poll_timer = None
        elif state == "running":
            status_widget.update("Status: Initializing\u2026")
        elif state:
            status_widget.update(f"Status: {state}")
        elif stalled:
            status_widget.update("Status: Launch may have failed \u2014 check notifications")

    def on_input_submitted(self, event: "Input.Submitted") -> None:
        """Treat Enter in the prompt input as Login if container is ready."""
        if self._container_ready:
            self._do_login()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle Login, Dismiss, or Show-log button clicks."""
        if event.button.id == "btn-login":
            self._do_login()
        elif event.button.id == "btn-dismiss":
            self._dismiss_keeping_prompt()
        elif event.button.id == "btn-show-log" and self._console_entry is not None:
            from .worker_log_screen import WorkerLogScreen

            self.app.push_screen(WorkerLogScreen(self._console_entry))

    def _build_result(
        self, agent: str | None
    ) -> "tuple[str, str, str, str, str | None, str | None]":
        """Bundle the launch context with *agent* and the prompt typed so far.

        *agent* is the chosen shell/agent on Login, or ``None`` on Dismiss;
        the prompt travels either way (stripped, or ``None`` when blank).
        """
        prompt = self.query_one("#launch-prompt", TextArea).text.strip() or None
        return (
            self._project_name,
            self._task_id,
            self._task_name,
            self._container_name,
            agent,
            prompt,
        )

    def _do_login(self) -> None:
        """Dismiss with launch context + selected agent and optional prompt."""
        from textual.widgets.select import NoSelection

        agent_select = self.query_one("#login-agent", Select)
        agent = agent_select.value
        if isinstance(agent, NoSelection):
            self.app.notify("Pick an agent first.")
            return
        self.dismiss(self._build_result(str(agent)))

    def _dismiss_keeping_prompt(self) -> None:
        """Dismiss without logging in, but hand back the prompt typed so far.

        The container is already starting in the background, so a ``None``
        agent tells the callback to persist the prompt (where the agent
        wrapper and login banner read it) and refresh — rather than launch a
        terminal.  The prompt then greets the user on their next ``login``.
        """
        self.dismiss(self._build_result(None))

    def action_dismiss_screen(self) -> None:
        """Dismiss the launch screen without logging in (prompt is preserved)."""
        self._dismiss_keeping_prompt()

    def on_unmount(self) -> None:
        """Clean up the polling timer."""
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None


# ---------------------------------------------------------------------------
# Task Details Screen
# ---------------------------------------------------------------------------


class ConfirmDestructiveScreen(screen.ModalScreen[bool]):
    """Modal confirmation dialog for destructive operations.

    Dismisses with ``True`` if the user confirms, ``False`` otherwise.
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    ConfirmDestructiveScreen {
        align: center middle;
    }

    #confirm-dialog {
        width: 60;
        height: auto;
        max-height: 80%;
        border: heavy $error;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #confirm-message {
        margin-bottom: 1;
    }

    #confirm-buttons {
        height: auto;
        align-horizontal: right;
    }

    #confirm-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        message: str,
        title: str = "Confirm Delete",
        confirm_label: str = "Delete",
    ) -> None:
        """Create a confirmation dialog with a warning message."""
        super().__init__()
        self._message = message
        self._title = title
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        """Build the confirmation message and Yes/Cancel buttons."""
        with Vertical(id="confirm-dialog") as dialog:
            yield Static(self._message, id="confirm-message", markup=False)
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button(self._confirm_label, id="btn-confirm", variant="error")
        dialog.border_title = self._title
        dialog.border_subtitle = "Esc to cancel"

    def on_mount(self) -> None:
        """Focus the cancel button by default (safe choice)."""
        self.query_one("#btn-cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        if event.button.id == "btn-confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        """Cancel and dismiss without confirming."""
        self.dismiss(False)


class QuitConfirmScreen(screen.ModalScreen[bool]):
    """Second-``q`` guard so a stray keystroke can't kill the whole TUI.

    Dismisses with ``True`` when the operator presses ``q`` again, and
    ``False`` on any other key — closing menus and screens with ``q``
    stays a single press; only quitting the app needs ``qq``.
    """

    CSS = """
    QuitConfirmScreen {
        align: center middle;
    }
    #quit-confirm {
        width: auto;
        height: auto;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        """A single centred prompt; the ``q`` shortcut wears the footer key colour."""
        yield Static(
            "Press \\[[$footer-key-foreground]q[/]] again to quit,\n"
            "any other key to go back to terok.",
            id="quit-confirm",
        )

    def on_key(self, event: events.Key) -> None:
        """Quit on a second ``q``; any other key returns to terok."""
        event.stop()
        self.dismiss(event.key == "q")


class TaskDetailsScreen(screen.Screen[str | None]):
    """Full-page detail screen for a task with categorized actions."""

    # Only escape/q use BINDINGS. Other keys require case-sensitive
    # dispatch (e.g. shift-N vs n) which Textual BINDINGS cannot express,
    # so they are handled in on_key instead.
    BINDINGS = [
        _modal_binding("escape", "dismiss", "Back"),
        _modal_binding("q", "dismiss", "Back"),
    ]

    CSS = (
        """
    TaskDetailsScreen {
        layout: vertical;
        background: $background;
    }
    """
        + _DETAIL_SCREEN_CSS
    )

    def __init__(
        self,
        task: TaskMeta | None,
        has_tasks: bool,
        project_name: str,
        image_old: bool | None = None,
    ) -> None:
        """Store task data and context for rendering when the screen mounts."""
        super().__init__()
        self._task_meta = task
        self._has_tasks = has_tasks
        self._project_name = project_name
        self._image_old = image_old

    def compose(self) -> ComposeResult:
        """Build the detail pane and categorized action list for a task."""
        detail_pane = Static(id="detail-content")
        title = "Task Details"
        if self._task_meta:
            backend = self._task_meta.backend or self._task_meta.mode or "unknown"
            title = f"Task: {self._task_meta.task_id} ({backend})"
        detail_pane.border_title = title
        detail_pane.border_subtitle = "Esc to close"
        yield detail_pane

        options: list[Option | None] = [
            Option("Start \\[c]li task  (new task + run CLI)", id="task_start_cli"),
            Option("Start Toad task  \\[w]  (new task + browser TUI)", id="task_start_toad"),
        ]
        options.append(
            Option(
                "Start \\[U]nattended task  (new task + run headless)", id="task_start_unattended"
            )
        )
        if self._has_tasks:
            options.append(Option("\\[l]ogin to container", id="login"))
            if self._task_meta and self._task_meta.mode:
                options.append(Option("view \\[f]ormatted logs", id="follow_logs"))
            options.append(None)
            options.append(Option("\\[r]estart container", id="restart"))
            options.append(Option("s\\[t]op container", id="stop"))
            if (
                self._task_meta
                and self._task_meta.mode == "run"
                and self._task_meta.exit_code is not None
            ):
                options.append(Option("follow \\[u]p with new prompt", id="followup"))
            options.append(None)
            options.append(Option("Copy diff vs \\[H]EAD", id="diff_head"))
            options.append(Option("Copy diff vs \\[P]REV", id="diff_prev"))
            options.append(None)
            options.append(Option("re\\[n]ame task", id="rename"))
            options.append(Option("delete task  \\[X]", id="delete"))
            options.append(None)
            from ..lib.api import get_config

            options.append(Option("shield \\[d]own (bypass)", id="shield_down"))
            options.append(
                Option("shield \\[D]own --all (+ private ranges)", id="shield_disengaged")
            )
            if not get_config().shield_bypass_firewall_no_protection:
                options.append(Option("\\[s]hield up (deny-all)", id="shield_up"))
            options.append(
                Option("shield \\[i]nteractive (verdict handler)", id="shield_interactive")
            )
            options.append(Option("shield \\[W]atch (event stream)", id="shield_watch"))
            options.append(Option("shield \\[C]learance (live D-Bus)", id="show_clearance"))

        yield OptionList(*options, id="actions-list")

    def on_mount(self) -> None:
        """Render task details and focus the action list."""
        self._last_render_width = -1
        self._render_details()
        actions = self.query_one("#actions-list", OptionList)
        actions.focus()

    def _render_details(self) -> None:
        """Render the cached task into the detail pane at its current width."""
        detail_widget = self.query_one("#detail-content", Static)
        # ``scrollable_content_region`` subtracts the vertical scrollbar's
        # gutter (the pane has ``overflow-y: auto``) so the wrap doesn't
        # overshoot once the content scrolls.
        width = detail_widget.scrollable_content_region.size.width
        if width == self._last_render_width:
            return
        self._last_render_width = width
        rendered = render_task_details(
            self._task_meta,
            project_name=self._project_name,
            image_old=self._image_old,
            empty_message="No task selected.",
            width=width,
        )
        detail_widget.update(rendered)

    def on_resize(self, event: events.Resize) -> None:
        """Re-render so the Name line wraps to the new screen width."""
        self._render_details()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Dismiss with the chosen action ID."""
        option_id = event.option_id
        if option_id:
            self.dismiss(option_id)

    def on_key(self, event: events.Key) -> None:
        """Handle case-sensitive shortcut keys for task actions."""
        key = event.key  # case-sensitive

        if key.lower() in ("escape", "q"):
            self.dismiss(None)
            event.stop()
            return

        # Shift keys (uppercase) — U always available, others require tasks
        shift_map: dict[str, str] = {
            "U": "task_start_unattended",
            "H": "diff_head",
            "P": "diff_prev",
            "X": "delete",
            "D": "shield_disengaged",
            "W": "shield_watch",
            "C": "show_clearance",
        }
        if key in shift_map:
            if key in ("H", "P", "X", "D", "W", "C") and not self._has_tasks:
                return
            self.dismiss(shift_map[key])
            event.stop()
            return

        # c/w — start new tasks (always available, same shortcuts as main screen)
        start_map: dict[str, str] = {
            "c": "task_start_cli",
            "w": "task_start_toad",
        }
        if key in start_map:
            self.dismiss(start_map[key])
            event.stop()
            return

        # Lowercase keys — require tasks to exist
        lower_map: dict[str, str] = {
            "r": "restart",
            "t": "stop",
            "l": "login",
            "u": "followup",
            "n": "rename",
            "i": "shield_interactive",
            "d": "shield_down",
            "s": "shield_up",
        }
        if key in lower_map:
            if not self._has_tasks:
                return
            self.dismiss(lower_map[key])
            event.stop()
            return

        # 'f' (view formatted logs) — available for all modes with containers
        if key == "f":
            if self._has_tasks and self._task_meta and self._task_meta.mode:
                self.dismiss("follow_logs")
                event.stop()

    async def action_dismiss(self, result: str | None = None) -> None:
        """Close the task details screen without selecting an action."""
        self.dismiss(result)


# ---------------------------------------------------------------------------
# Shield helpers
# ---------------------------------------------------------------------------


_SHIELD_HEALTH_STYLES: dict[str, str] = {
    "ok": "green",
    "setup-needed": "red",
    "stale-hooks": "yellow",
    "bypass": "yellow",
}


def render_shield_status(
    env_check: EnvironmentCheck | None, shield_info: dict | None = None
) -> Text:
    """Render shield environment check as a Rich Text object."""
    if env_check is None:
        return Text("Shield environment status unknown.")

    color = _SHIELD_HEALTH_STYLES.get(env_check.health, "red")
    health_s = Text(env_check.health, style=Style(color=color))

    # Shield package version
    try:
        from importlib.metadata import version as _meta_version

        shield_version = _meta_version("terok-shield")
    except Exception as exc:
        from terok.lib.util.logging_utils import _log_debug

        _log_debug(f"importlib.metadata lookup for terok-shield failed: {exc}")
        shield_version = "unknown"

    podman_str = ".".join(str(v) for v in env_check.podman_version)
    lines = [
        Text(f"Version:   {shield_version}"),
        Text(f"Podman:    {podman_str}"),
        Text.assemble("Health:    ", health_s),
        Text(f"Hooks:     {env_check.hooks}"),
    ]

    # Config details from shield_info (mode, audit, profiles)
    if shield_info:
        mode = shield_info.get("mode", "hook")
        audit = "enabled" if shield_info.get("audit_enabled", True) else "disabled"
        profiles = shield_info.get("profiles", [])
        lines.append(Text(f"Mode:      {mode}"))
        lines.append(Text(f"Audit:     {audit}"))
        lines.append(Text(f"Profiles:  {', '.join(profiles) or '(none)'}"))
    if env_check.issues:
        lines.append(Text(""))
        lines.append(Text("Issues:"))
        for issue in env_check.issues:
            style = Style(color="red", bold=True) if "bypass" in issue else Style()
            lines.append(Text(f"  - {issue}", style=style))

    if env_check.setup_hint:
        lines.append(Text(""))
        lines.append(Text(env_check.setup_hint, style=Style(dim=True)))

    return Text("\n").join(lines)


# ---------------------------------------------------------------------------
# Shield Screen
# ---------------------------------------------------------------------------


class ShieldScreen(screen.Screen[str | None]):
    """Full-page screen for viewing shield environment status."""

    BINDINGS = [
        _modal_binding("escape", "dismiss", "Back"),
        _modal_binding("q", "dismiss", "Back"),
        _modal_binding("s", "shield_setup", "Setup global hooks"),
        _modal_binding("r", "shield_refresh", "Refresh status"),
    ]

    CSS = (
        """
    ShieldScreen {
        layout: vertical;
        background: $background;
    }
    """
        + _DETAIL_SCREEN_CSS
    )

    def __init__(self, env_check: EnvironmentCheck | None = None) -> None:
        """Store environment check result for rendering."""
        super().__init__()
        self._env_check = env_check
        self._shield_info: dict | None = None
        self._loading = False

    @property
    def _needs_setup(self) -> bool:
        """Return True if global hook setup is needed (podman < 5.6.0 without hooks)."""
        return self._env_check is not None and self._env_check.needs_setup

    def compose(self) -> ComposeResult:
        """Build the detail pane and action list for shield management."""
        detail_pane = Static(id="detail-content")
        detail_pane.border_title = "Shield Environment"
        detail_pane.border_subtitle = "Esc to close"
        yield detail_pane

        yield OptionList(
            Option("\\[s]etup global hooks", id="shield_setup"),
            None,
            Option("\\[r]efresh status", id="shield_refresh"),
            id="actions-list",
        )

    def on_mount(self) -> None:
        """Start loading shield status and focus the action list."""
        actions = self.query_one("#actions-list", OptionList)
        actions.focus()
        if self._env_check is not None:
            # Already have cached data — render it, then refresh in background
            self._load_shield_info()
            self._render_status()
            self._update_setup_option()
        self._start_refresh()

    def _load_shield_info(self) -> None:
        """Fetch shield config (mode, audit, profiles) for display."""
        import tempfile
        from pathlib import Path

        from terok.lib.api.shield import ShieldManager

        try:
            # ``status`` is config-only; the throwaway task_dir is never written to.
            with tempfile.TemporaryDirectory() as tmp:
                self._shield_info = ShieldManager(Path(tmp)).status()
        except Exception as exc:
            from ..lib.util.logging_utils import _log_debug

            _log_debug(f"Shield status load failed: {exc}")
            self._shield_info = None

    def _render_status(self) -> None:
        """Update the detail pane with current status."""
        detail_widget = self.query_one("#detail-content", Static)
        if self._loading and self._env_check is None:
            detail_widget.update(Text("Loading shield status...", style=Style(dim=True)))
        elif self._loading:
            # Show existing data with a loading hint
            content = render_shield_status(self._env_check, self._shield_info)
            content.append("\n\nRefreshing...")
            detail_widget.update(content)
        else:
            detail_widget.update(render_shield_status(self._env_check, self._shield_info))

    def _update_setup_option(self) -> None:
        """Disable the setup option when hooks are per-container (modern podman)."""
        actions = self.query_one("#actions-list", OptionList)
        for idx in range(actions.option_count):
            opt = actions.get_option_at_index(idx)
            if opt.id == "shield_setup":
                if self._needs_setup:
                    actions.enable_option_at_index(idx)
                else:
                    actions.disable_option_at_index(idx)
                break

    def _start_refresh(self) -> None:
        """Kick off a background refresh of shield status."""
        self._loading = True
        self._render_status()
        self.run_worker(self._fetch_status, thread=True, exit_on_error=False)

    @staticmethod
    def _fetch_status() -> tuple[EnvironmentCheck | None, dict | None]:
        """Load environment check and shield config in a thread."""
        import tempfile
        from pathlib import Path

        from terok.lib.api.setup import check_environment as shield_check_environment
        from terok.lib.api.shield import ShieldManager

        env: EnvironmentCheck | None = None
        info: dict | None = None
        try:
            env = shield_check_environment()
        except Exception as exc:
            from terok.lib.util.logging_utils import _log_debug

            _log_debug(f"shield_check_environment failed in background fetch: {exc}")
        try:
            # ``status`` is config-only; the throwaway task_dir is never written to.
            with tempfile.TemporaryDirectory() as tmp:
                info = ShieldManager(Path(tmp)).status()
        except Exception as exc:
            from terok.lib.util.logging_utils import _log_debug

            _log_debug(f"shield status load failed in background fetch: {exc}")
        return env, info

    def on_worker_state_changed(self, event: Any) -> None:
        """Handle background worker completion."""
        if event.state.name != "SUCCESS":
            self._loading = False
            self._render_status()
            return
        result = event.worker.result
        if result and isinstance(result, tuple) and len(result) == 2:
            self._env_check, self._shield_info = result
            self._loading = False
            self._render_status()
            self._update_setup_option()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle action selection from the option list."""
        option_id = event.option_id
        if option_id == "shield_refresh":
            self._start_refresh()
        elif option_id:
            self.dismiss(option_id)

    async def action_dismiss(self, result: str | None = None) -> None:
        """Close the screen without selecting an action."""
        self.dismiss(result)

    def action_shield_setup(self) -> None:
        """Trigger shield setup flow (only if needed)."""
        if not self._needs_setup:
            return
        self.dismiss("shield_setup")

    def action_shield_refresh(self) -> None:
        """Refresh the status display."""
        self._start_refresh()


# ---------------------------------------------------------------------------
# Vault helpers
# ---------------------------------------------------------------------------


def _format_credentials_typed(status: VaultStatusSnapshot) -> str:
    """Format stored credentials as ``name (type), ...`` for status display.

    The snapshot already collected provider names and types in one DB
    pass across every credential set — the renderer is a pure
    formatter that doesn't touch the DB.
    """
    if not status.credentials_stored:
        return ""
    return ", ".join(
        f"{name} ({status.credential_types.get(name, 'unknown')})"
        for name in status.credentials_stored
    )


def render_vault_status(status: VaultStatusSnapshot | None) -> Text:
    """Render vault status details as a Rich Text object.

    Every container's supervisor embeds its own vault proxy, so the
    host view collapses to the DB-side facts the operator can act on:
    locked / unlocked, passphrase tier, count + type of stored
    credentials, plaintext-on-disk warning.  Per-container proxy
    health surfaces in the task's own doctor row.
    """
    if status is None:
        return Text("Vault status unknown.")

    ok = Style(color="green")
    warn = Style(color="yellow")
    err = Style(color="red")
    dim = Style(dim=True)

    locked_label = (
        Text("yes — no tier resolved", style=err) if status.locked else Text("no", style=ok)
    )

    lines: list[Text] = [
        Text.assemble("Locked:      ", locked_label),
    ]

    if not status.locked and status.passphrase_source is not None:
        lines.append(Text(f"Passphrase:  resolved via {status.passphrase_source}"))

    lines.append(Text(f"DB:          {status.db_path}"))

    if status.db_error is not None:
        lines.append(Text.assemble("DB error:    ", Text(status.db_error, style=err)))

    # ``None`` means the DB couldn't be read; render explicitly so a
    # locked vault holding real data isn't mistaken for a fresh empty
    # install ("SSH keys: 0  /  Credentials: none stored").
    if status.ssh_keys_stored is None:
        lines.append(Text.assemble("SSH keys:    ", Text("(unavailable)", style=dim)))
    else:
        lines.append(Text(f"SSH keys:    {status.ssh_keys_stored}"))

    if status.credentials_stored is None:
        lines.append(Text.assemble("Credentials: ", Text("(unavailable)", style=dim)))
    elif status.credentials_stored:
        lines.append(Text(f"Credentials: {_format_credentials_typed(status)}"))
    else:
        lines.append(Text.assemble("Credentials: ", Text("none stored", style=dim)))

    plaintext_path = status.plaintext_passphrase_path
    if plaintext_path is not None:
        # The TUI is screenshot- and screen-share-friendly; rendering
        # the full filesystem path of the plaintext-passphrase file
        # is more disclosure than the warning requires.  Surface
        # just the basename — enough for the operator to recognise
        # what's going on, not enough to advertise the file's
        # location to a casual observer.  The CLI ``vault status``
        # still prints the full path for grep-friendly scripting.
        from pathlib import Path as _Path

        redacted = _Path(plaintext_path).name
        lines.append(Text(""))
        lines.append(
            Text.assemble(
                Text("WARNING: ", style=err),
                Text(f"vault passphrase stored in plaintext on disk ({redacted})", style=warn),
            )
        )
        lines.append(
            Text(
                "         accept on-disk plaintext as your trust boundary,"
                " or migrate to keyring/systemd-creds.",
                style=warn,
            )
        )

    if status.locked:
        lines.append(Text(""))
        lines.append(
            Text(
                "The vault stores API credentials encrypted at rest.  Unlock\n"
                "it so per-container supervisors can resolve credentials on\n"
                "container start.",
                style=dim,
            )
        )

    return Text("\n").join(lines)


# ---------------------------------------------------------------------------
# Vault Unlock Modal
# ---------------------------------------------------------------------------


class VaultUnlockModal(screen.ModalScreen["str | None"]):
    """Passphrase prompt that writes to the session-unlock tmpfs file.

    Triggered when ``VaultStatusSnapshot.locked`` is True at TUI mount or after
    a manual ``Ctrl+L`` re-probe.  Mirrors the [`AskpassModal`][terok.tui.askpass_service.AskpassModal]
    shape: one masked input, two buttons.  The "Unlock for this
    session" path is the always-safe one — it writes the session-file
    tier, the highest-priority resolver tier, which wins over any
    stale persistent state.

    Persistent-tier writes (keyring / systemd-creds / config.yml) are
    setup-time decisions surfaced via the chooser and ``vault seal``;
    keeping the runtime modal narrow avoids leaking that policy
    surface into the unlock path.
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    VaultUnlockModal {
        align: center middle;
    }

    #vault-unlock-dialog {
        width: 70;
        max-width: 100%;
        height: auto;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #vault-unlock-prompt {
        margin-bottom: 1;
        color: $text-muted;
    }

    #vault-unlock-buttons {
        height: 3;
        align-horizontal: right;
        margin-top: 1;
    }

    #vault-unlock-buttons Button {
        margin-left: 1;
    }
    """

    def compose(self) -> ComposeResult:
        """Lay out prompt, masked input, and Cancel / Unlock buttons."""
        if Input is None:  # pragma: no cover — textual is stubbed in unit tests
            return
        dialog = Vertical(id="vault-unlock-dialog")
        dialog.border_title = "Vault locked"
        with dialog:
            yield Static(
                "Enter the credentials-DB passphrase to unlock the vault for this session.\n"
                "The value is written to the session-unlock tmpfs file (cleared at reboot).",
                id="vault-unlock-prompt",
            )
            yield Input(password=True, id="vault-unlock-input")
            with Horizontal(id="vault-unlock-buttons"):
                yield Button("Cancel", id="vault-unlock-cancel", variant="default")
                yield Button("Unlock for this session", id="vault-unlock-ok", variant="primary")

    def on_mount(self) -> None:
        """Focus the input so the user can type immediately."""
        if Input is None:  # pragma: no cover
            return
        self.query_one("#vault-unlock-input", Input).focus()

    def action_cancel(self) -> None:
        """Dismiss without unlocking — the locked state persists."""
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route the two buttons to ``dismiss(passphrase)`` or ``dismiss(None)``."""
        if event.button.id == "vault-unlock-cancel":
            self.dismiss(None)
        elif event.button.id == "vault-unlock-ok":
            value = self.query_one("#vault-unlock-input", Input).value
            self.dismiss(value or None)

    def on_input_submitted(self, event: "Input.Submitted") -> None:
        """Enter in the input field is equivalent to clicking Unlock."""
        self.dismiss(event.value or None)


# ---------------------------------------------------------------------------
# Vault Reveal Modal — show the passphrase + offer recovery ack
# ---------------------------------------------------------------------------


class VaultRevealModal(screen.ModalScreen["bool | None"]):
    """Display the resolved vault passphrase and collect an off-host save ack.

    Dismisses with ``True`` if the operator confirms they've saved the
    value (caller writes the recovery marker), ``False`` if they
    explicitly say not yet, or ``None`` on Esc.  The modal never
    contacts the resolver itself — the caller does the lookup and
    passes the cleartext in, so a future "reveal via Python API" path
    can reuse the same modal without re-walking the chain.
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Close without acknowledging"),
    ]

    CSS = """
    VaultRevealModal {
        align: center middle;
    }

    #vault-reveal-dialog {
        width: 80;
        max-width: 100%;
        height: auto;
        border: heavy $warning;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #vault-reveal-explainer {
        margin-bottom: 1;
        color: $text-muted;
    }

    #vault-reveal-passphrase {
        margin: 1 0;
        padding: 1;
        background: $boost;
        color: $text;
        text-style: bold;
        text-align: center;
    }

    #vault-reveal-warning {
        color: $warning;
        margin-bottom: 1;
    }

    #vault-reveal-buttons {
        height: 3;
        align-horizontal: right;
        margin-top: 1;
    }

    #vault-reveal-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, passphrase: str, source: str, *, already_acked: bool) -> None:
        """Build the modal with the cleartext + source label + ack state."""
        super().__init__()
        self._passphrase = passphrase
        self._source = source
        self._already_acked = already_acked

    def compose(self) -> ComposeResult:
        """Lay out the explainer, the passphrase box, the buttons."""
        dialog = Vertical(id="vault-reveal-dialog")
        dialog.border_title = "Vault recovery key"
        with dialog:
            yield Static(
                "Save this off-host (password manager, paper safe, "
                "sealed envelope). Every storage tier we resolve through "
                "(systemd-creds, keyring, session-file) is bound to this "
                "machine, account, or boot — a hardware failure or TPM "
                "transplant strands the vault without it.",
                id="vault-reveal-explainer",
            )
            yield Static(self._passphrase, id="vault-reveal-passphrase")
            yield Static(f"resolved via: {self._source}", id="vault-reveal-warning")
            with Horizontal(id="vault-reveal-buttons"):
                yield Button("Close", id="vault-reveal-cancel", variant="default")
                if self._already_acked:
                    yield Button("Already marked saved", id="vault-reveal-acked", variant="default")
                else:
                    yield Button("Mark as saved", id="vault-reveal-ack", variant="primary")

    def action_cancel(self) -> None:
        """Esc — dismiss without changing the marker."""
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route the buttons to ``dismiss(True | False | None)``."""
        if event.button.id == "vault-reveal-ack":
            self.dismiss(True)
        elif event.button.id == "vault-reveal-acked":
            self.dismiss(None)
        else:
            self.dismiss(False)


# ---------------------------------------------------------------------------
# Vault Screen
# ---------------------------------------------------------------------------


class VaultScreen(screen.Screen[str | None]):
    """Full-page screen for managing the vault store.

    The per-container supervisor model has no host-side daemon to
    operate, so all the actions here are DB-side: unlock / lock the
    session tier, move the passphrase between tiers, reveal /
    acknowledge the recovery key.
    """

    BINDINGS = [
        _modal_binding("escape", "dismiss", "Back"),
        _modal_binding("q", "dismiss", "Back"),
        _modal_binding("n", "vault_unlock", "Unlock (session-file tier)"),
        _modal_binding("l", "vault_lock", "Lock (clear all tiers)"),
        _modal_binding("e", "vault_seal", "Seal into systemd-creds"),
        _modal_binding("k", "vault_to_keyring", "Move passphrase to keyring"),
        _modal_binding("v", "vault_reveal", "Reveal recovery passphrase"),
        _modal_binding("a", "vault_acknowledge", "Mark recovery key as saved"),
        _modal_binding("r", "vault_refresh", "Refresh status"),
    ]

    CSS = (
        """
    VaultScreen {
        layout: vertical;
        background: $background;
    }
    """
        + _DETAIL_SCREEN_CSS
    )

    def __init__(self, status: VaultStatusSnapshot | None = None) -> None:
        """Store vault status for rendering."""
        super().__init__()
        self._status = status

    def compose(self) -> ComposeResult:
        """Build the detail pane and action list for vault management."""
        detail_pane = Static(id="detail-content")
        detail_pane.border_title = "Vault"
        detail_pane.border_subtitle = "Esc to close"
        yield detail_pane

        yield OptionList(
            Option("u\\[n]lock (write to session-file tier)", id="vault_unlock"),
            Option("\\[l]ock (clear all tiers)", id="vault_lock"),
            Option("s\\[e]al current passphrase into systemd-creds", id="vault_seal"),
            Option("move passphrase to \\[k]eyring", id="vault_to_keyring"),
            None,
            Option("re\\[v]eal recovery passphrase", id="vault_reveal"),
            Option("mark recovery key as s\\[a]ved", id="vault_acknowledge"),
            None,
            Option("\\[r]efresh status", id="vault_refresh"),
            id="actions-list",
        )

    def on_mount(self) -> None:
        """Render vault status and focus the action list."""
        self._render_status()
        actions = self.query_one("#actions-list", OptionList)
        actions.focus()

    def _render_status(self) -> None:
        """Update the detail pane with current status."""
        detail_widget = self.query_one("#detail-content", Static)
        detail_widget.update(render_vault_status(self._status))

    def _refresh_status(self) -> None:
        """Re-fetch status and update the display."""
        try:
            self._status = VaultStatusSnapshot.load()
        except Exception as exc:
            from ..lib.util.logging_utils import log_warning

            log_warning(f"Vault status refresh failed: {exc}")
            self._status = None
        self._render_status()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle action selection from the option list."""
        option_id = event.option_id
        if option_id == "vault_refresh":
            self._refresh_status()
        elif option_id:
            self.dismiss(option_id)

    async def action_dismiss(self, result: str | None = None) -> None:
        """Close the screen without selecting an action."""
        self.dismiss(result)

    def action_vault_unlock(self) -> None:
        """Trigger the session-file unlock flow."""
        self.dismiss("vault_unlock")

    def action_vault_lock(self) -> None:
        """Trigger session-file lock (reversible; persistent tiers untouched)."""
        self.dismiss("vault_lock")

    def action_vault_seal(self) -> None:
        """Seal the currently resolved passphrase into a systemd-creds credential."""
        self.dismiss("vault_seal")

    def action_vault_to_keyring(self) -> None:
        """Trigger the to-keyring relocation flow."""
        self.dismiss("vault_to_keyring")

    def action_vault_reveal(self) -> None:
        """Open the reveal modal — surfaces the passphrase + offers a save-ack."""
        self.dismiss("vault_reveal")

    def action_vault_acknowledge(self) -> None:
        """Mark the current passphrase as saved without re-displaying it."""
        self.dismiss("vault_acknowledge")

    def action_vault_refresh(self) -> None:
        """Refresh the status display."""
        self._refresh_status()
