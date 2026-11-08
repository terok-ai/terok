# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Keep background SSH requests visible without stealing keyboard focus.

[`AskpassService`][terok.tui.askpass_service.AskpassService] associates explicit
helper requests with console actions. Native prompts open only when requested;
advertised desktop helpers keep their native dialog and report its lifetime.
The service never writes passphrases to console logs.

The socket is bound lazily for personal-SSH projects under
[`Config.runtime_dir`][terok.lib.api.Config] with mode ``0600``.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
import os
import socket as _socket
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from terok_util import find_host_tool
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from ..lib.api import get_config
from . import askpass_protocol as proto

if TYPE_CHECKING:
    from textual.app import App

    from .console_log import ConsoleLogEntry

logger = logging.getLogger(__name__)


# ── env builder ───────────────────────────────────────────────────────


def gui_askpass_usable(env: Mapping[str, str]) -> bool:
    """Return ``True`` when *env* advertises a GUI askpass that can actually render.

    A ``SSH_ASKPASS`` without a reachable display (``DISPLAY`` /
    ``WAYLAND_DISPLAY``) is almost certainly a dud inherited from a
    desktop login env — a container or headless SSH session would
    never see the prompt.  In that case we want our Textual modal to
    take over instead.

    This checks advertised environment variables, not display connectivity.
    The wrapper preserves the selected helper while reporting its lifetime.
    """
    return bool(env.get("SSH_ASKPASS") and (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")))


def build_askpass_env(
    base_env: Mapping[str, str],
    *,
    socket_path: str | os.PathLike[str],
    helper_bin: str | os.PathLike[str],
) -> dict[str, str]:
    """Route SSH prompts through the TUI, preserving an advertised GUI helper.

    GUI prompts keep their native dialog. The wrapper reports their lifetime
    through the same socket so background actions cannot wait invisibly.
    ``SSH_ASKPASS_REQUIRE=force`` prevents terminal prompts corrupting the TUI.
    """
    env = dict(base_env)
    if gui_askpass_usable(env) and env["SSH_ASKPASS"] != str(helper_bin):
        env[proto.GUI_HELPER_ENV] = env["SSH_ASKPASS"]
    elif env.get("SSH_ASKPASS") != str(helper_bin):
        env.pop(proto.GUI_HELPER_ENV, None)
    env["SSH_ASKPASS"] = str(helper_bin)
    env["TEROK_ASKPASS_SOCKET"] = str(socket_path)
    env["SSH_ASKPASS_REQUIRE"] = "force"
    return env


# ── socket path discovery ─────────────────────────────────────────────


def default_socket_path(*, pid: int | None = None) -> Path:
    """Return a per-process socket path under terok's runtime namespace.

    Reads ``runtime_dir`` from the [`Config`][terok.lib.api.Config]
    snapshot — the boundary-respecting wrapper around the sandbox's
    namespace resolver (``$XDG_RUNTIME_DIR/terok/`` →
    ``$XDG_STATE_HOME/terok/`` → ``~/.local/state/terok/``).  No
    ``/tmp`` fallback means no predictable-temp-path surface (bandit
    B108).
    """
    return get_config().runtime_dir / f"askpass-{pid or os.getpid()}.sock"


def locate_helper_bin() -> Path:
    """Find ``terok-askpass`` on ``PATH``, raising if it isn't installed."""
    found = find_host_tool("terok-askpass")
    if not found:
        raise RuntimeError(
            "terok-askpass helper not found on PATH — was the package installed "
            "with its [project.scripts] entries?"
        )
    return Path(found)


# ── modal ─────────────────────────────────────────────────────────────


class PromptDisposition(enum.Enum):
    """A deferred prompt remains pending without answering or cancelling SSH."""

    LATER = "later"


class AskpassModal(ModalScreen["str | None | PromptDisposition"]):
    """Collect a passphrase, cancel SSH, or defer an action's pending prompt.

    The prompt text comes straight from OpenSSH (e.g.
    ``"Enter passphrase for key '/home/.../id_ed25519':"``) and is shown
    verbatim so users recognise which key is being unlocked.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
    ]

    CSS = """
    AskpassModal {
        align: center middle;
    }

    #askpass-dialog {
        width: 70;
        max-width: 100%;
        height: auto;
        border: heavy $primary;
        border-title-align: right;
        background: $surface;
        padding: 1 2;
    }

    #askpass-prompt {
        margin-bottom: 1;
        color: $text-muted;
    }

    #askpass-buttons {
        height: 3;
        align-horizontal: right;
        margin-top: 1;
    }

    #askpass-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, prompt: str, *, allow_defer: bool = False) -> None:
        """Build the modal with *prompt* as the shown label."""
        super().__init__()
        self._prompt = prompt
        self._allow_defer = allow_defer
        self._expired = False

    def compose(self) -> ComposeResult:
        """Lay out the prompt, password input, and answer/cancel/defer controls."""
        dialog = Vertical(id="askpass-dialog")
        dialog.border_title = "SSH passphrase"
        with dialog:
            # markup=False — OpenSSH prompts contain paths like
            # "Enter passphrase for '/home/.../id_ed25519':" and Rich would
            # otherwise interpret any "[...]" inside the path as its markup
            # syntax.  We want to show the prompt verbatim.
            yield Static(self._prompt, id="askpass-prompt", markup=False)
            yield Input(password=True, id="askpass-input")
            with Horizontal(id="askpass-buttons"):
                if self._allow_defer:
                    yield Button("Later", id="askpass-later", variant="default")
                yield Button("Cancel", id="askpass-cancel", variant="default")
                yield Button("Unlock", id="askpass-ok", variant="primary")

    def on_mount(self) -> None:
        """Focus the input so the user can type immediately."""
        self.query_one("#askpass-input", Input).focus()

    def action_cancel(self) -> None:
        """Hide a reachable pending prompt; otherwise cancel the SSH request."""
        self.dismiss(PromptDisposition.LATER if self._allow_defer else None)

    def expire(self) -> None:
        """Close a disconnected request without popping an unrelated screen."""
        self._expired = True
        if self.is_mounted and self.app.screen is self:
            self.dismiss(None)

    def on_screen_resume(self) -> None:
        """Discard a disconnected prompt when an overlaid screen closes."""
        if self._expired:
            self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Answer, cancel, or postpone the pending SSH request."""
        if event.button.id == "askpass-cancel":
            self.dismiss(None)
        elif event.button.id == "askpass-later":
            self.dismiss(PromptDisposition.LATER)
        elif event.button.id == "askpass-ok":
            self.dismiss(self.query_one("#askpass-input", Input).value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the input field accepts the passphrase — same as clicking Unlock."""
        self.dismiss(event.value)


# ── service ───────────────────────────────────────────────────────────


class AskpassService:
    """Track SSH prompt lifetimes and serialize explicitly opened native dialogs.

    Pending requests remain associated with their actions until answered,
    cancelled, or disconnected. Unattributed prompts open immediately because
    no console action exists to make them reachable later.
    """

    def __init__(
        self,
        app: App,
        *,
        socket_path: Path | None = None,
        helper_bin: Path | None = None,
    ) -> None:
        """Construct the service — does not bind the socket or locate the helper.

        Both *socket_path* and *helper_bin* are resolved lazily at
        [`start`][terok.tui.askpass_service.AskpassService.start] / [`helper_bin`][terok.tui.askpass_service.AskpassService.helper_bin] access — the service is
        created at TUI mount but often never started, so we shouldn't
        fail the whole TUI if the helper happens to be missing.
        """
        self._app = app
        self._socket_path = socket_path or default_socket_path()
        self._helper_bin = helper_bin
        self._server: asyncio.AbstractServer | None = None
        self._modal_lock = asyncio.Lock()
        self._clients: set[asyncio.Task] = set()

    @property
    def socket_path(self) -> Path:
        """Filesystem path of the unix socket (bound iff [`start`][terok.tui.askpass_service.AskpassService.start] has run)."""
        return self._socket_path

    @property
    def helper_bin(self) -> Path:
        """Path to the ``terok-askpass`` binary that subprocesses should invoke.

        Resolved on first access — raises if the binary isn't on PATH,
        which lets the caller surface a user-friendly error at the
        moment askpass is actually needed, not at TUI boot.
        """
        if self._helper_bin is None:
            self._helper_bin = locate_helper_bin()
        return self._helper_bin

    @property
    def is_running(self) -> bool:
        """``True`` iff the asyncio server is bound and serving."""
        return self._server is not None

    async def start(self) -> None:
        """Bind the unix socket with mode ``0600`` and start accepting connections.

        Idempotent — calling ``start`` on an already-running service is
        a no-op.  Any stale socket file at the target path is removed
        first; we assume the old owner is gone because the path embeds
        our pid.

        The bind happens synchronously under a local umask so the
        socket inode is created ``0600`` atomically.  We deliberately
        do *not* wrap the whole ``asyncio.start_unix_server`` call in
        ``os.umask(…); await …; os.umask(old)`` because ``os.umask``
        is process-global — any other coroutine that runs during the
        ``await`` would see our tightened umask and create files with
        unexpectedly restrictive permissions.
        """
        if self._server is not None:
            return
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        old_umask = os.umask(0o077)
        try:
            sock.bind(str(self._socket_path))
        except BaseException:
            sock.close()
            raise
        finally:
            os.umask(old_umask)
        sock.listen(16)
        try:
            self._server = await asyncio.start_unix_server(self._handle_client, sock=sock)
        except BaseException:
            sock.close()
            self._socket_path.unlink(missing_ok=True)
            raise
        logger.debug("askpass service listening on %s", self._socket_path)

    async def stop(self) -> None:
        """Close the server and unlink the socket.  Idempotent."""
        server, self._server = self._server, None
        if server is not None:
            server.close()
        clients = tuple(self._clients)
        for client in clients:
            client.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
        if server is not None:
            await server.wait_closed()
        self._socket_path.unlink(missing_ok=True)
        logger.debug("askpass service stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Serve one prompt; keep its pending state tied to the socket lifetime."""
        client = asyncio.current_task()
        assert client is not None  # nosec B101
        self._clients.add(client)
        try:
            # An accepted connection's handler may start after stop() snapshots clients.
            if self._server is None:
                return
            raw = await reader.readline()
            if not raw:
                return
            try:
                request = proto.parse_request(proto.decode(raw))
            except proto.AskpassProtocolError as exc:
                logger.warning("askpass: bad request: %s", exc)
                return

            await self._serve_request(request, reader, writer)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self._clients.discard(client)
            writer.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await writer.wait_closed()

    async def _serve_request(
        self,
        request: proto.AskpassRequest,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Notify without stealing focus; expose the prompt on its console action."""
        entry = self._request_action(request.action_id)
        answer: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        disconnected = asyncio.create_task(reader.read())
        prompt_task: asyncio.Task | None = None

        def open_prompt() -> None:
            """Open one native dialog or point to the existing desktop prompt."""
            nonlocal prompt_task
            if request.external:
                self._app.notify("Answer the SSH prompt in your desktop dialog.", timeout=10)
            elif not answer.done() and (prompt_task is None or prompt_task.done()):
                prompt_task = asyncio.create_task(
                    self._show_prompt(request.prompt, answer, allow_defer=entry is not None)
                )

        try:
            if entry is not None:
                entry.add_input(request.request_id, open_prompt)
            destination = "desktop dialog" if request.external else "Console output"
            title = entry.title if entry is not None else "SSH"
            self._app.notify(
                f"{title} needs input — open {destination}.", severity="warning", timeout=10
            )
            if request.external:
                await disconnected
            else:
                if entry is None:
                    # No console row exists to reopen an unattributed prompt.
                    open_prompt()
                await self._reply_when_answered(request.request_id, answer, disconnected, writer)
        finally:
            if entry is not None:
                entry.remove_input(request.request_id)
            answer.cancel()
            tasks = [disconnected, *([prompt_task] if prompt_task else [])]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _request_action(self, action_id: int | None) -> ConsoleLogEntry | None:
        """Associate a question only with an action that is still active."""
        registry = getattr(self._app, "console_logs", None)
        if registry is None or action_id is None:
            return None
        entry = registry.get(action_id)
        return entry if entry is not None and entry.running else None

    async def _reply_when_answered(
        self,
        request_id: str,
        answer: asyncio.Future[str | None],
        disconnected: asyncio.Task[bytes],
        writer: asyncio.StreamWriter,
    ) -> None:
        """Return the operator's answer unless the requesting helper has disconnected."""
        await asyncio.wait((answer, disconnected), return_when=asyncio.FIRST_COMPLETED)
        if disconnected.done():
            return
        value = answer.result()
        reply = (
            proto.make_cancel(request_id) if value is None else proto.make_answer(request_id, value)
        )
        writer.write(proto.encode(reply))
        await writer.drain()

    async def _show_prompt(
        self, prompt: str, answer: asyncio.Future[str | None], *, allow_defer: bool
    ) -> None:
        """Serialize requested dialogs; Later leaves the SSH request pending."""
        async with self._modal_lock:
            result: asyncio.Future[str | None | PromptDisposition] = (
                asyncio.get_running_loop().create_future()
            )
            modal = AskpassModal(prompt, allow_defer=allow_defer)

            def on_dismissed(value: str | None | PromptDisposition) -> None:
                """Deliver the dialog outcome unless its request was disconnected."""
                if not result.done():
                    result.set_result(value)

            self._app.push_screen(modal, on_dismissed)
            try:
                value = await result
                if value is not PromptDisposition.LATER and not answer.done():
                    answer.set_result(value)
            finally:
                modal.expire()
