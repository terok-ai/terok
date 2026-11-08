# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`terok.tui.askpass_service`][terok.tui.askpass_service].

Split into three groups:

- `TestBuildEnv` — pure-function checks on the env builder
  (respect pre-existing GUI askpass, inject ours otherwise, always set
  ``SSH_ASKPASS_REQUIRE=force``).
- `TestServiceLifecycle` — [`AskpassService`][terok.tui.app.AskpassService] start / stop
  / idempotency and socket permissions.
- `TestServiceRoundTrip` — end-to-end helper → service → modal
  round trip driven through ``App.run_test()``.
"""

from __future__ import annotations

import asyncio
import os
import stat
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from textual.app import App
from textual.pilot import Pilot
from textual.screen import ModalScreen

from terok.tui import askpass_protocol as proto
from terok.tui.askpass_service import (
    AskpassModal,
    AskpassService,
    build_askpass_env,
    default_socket_path,
    gui_askpass_usable,
)
from terok.tui.console_log import ConsoleLogEntry, ConsoleLogRegistry, LogStatus

# ── build_askpass_env ─────────────────────────────────────────────────


class TestBuildEnv:
    """Env-injection policy must respect existing GUI askpass when usable."""

    def test_injects_helper_when_no_existing_askpass(self) -> None:
        """With no ``SSH_ASKPASS``, our helper + socket are set."""
        env = build_askpass_env(
            {"HOME": "/home/u"},
            socket_path="/run/x.sock",
            helper_bin="/usr/bin/terok-askpass",
        )
        assert env["SSH_ASKPASS"] == "/usr/bin/terok-askpass"
        assert env["TEROK_ASKPASS_SOCKET"] == "/run/x.sock"
        assert env["SSH_ASKPASS_REQUIRE"] == "force"

    def test_respects_existing_askpass_when_display_is_set(self) -> None:
        """User's seahorse / gnome-keyring wins when a GUI is reachable."""
        env = build_askpass_env(
            {"SSH_ASKPASS": "/usr/libexec/seahorse-askpass", "DISPLAY": ":0"},
            socket_path="/run/x.sock",
            helper_bin="/usr/bin/terok-askpass",
        )
        assert env["SSH_ASKPASS"] == "/usr/bin/terok-askpass"
        assert env["TEROK_ASKPASS_GUI_HELPER"] == "/usr/libexec/seahorse-askpass"
        assert env["TEROK_ASKPASS_SOCKET"] == "/run/x.sock"
        # REQUIRE=force still applies so the GUI dialog is used even with a tty.
        assert env["SSH_ASKPASS_REQUIRE"] == "force"

    def test_respects_existing_askpass_under_wayland(self) -> None:
        """``WAYLAND_DISPLAY`` counts as a GUI the same way ``DISPLAY`` does."""
        env = build_askpass_env(
            {"SSH_ASKPASS": "/usr/bin/custom-askpass", "WAYLAND_DISPLAY": "wayland-0"},
            socket_path="/run/x.sock",
            helper_bin="/usr/bin/terok-askpass",
        )
        assert env["SSH_ASKPASS"] == "/usr/bin/terok-askpass"
        assert env["TEROK_ASKPASS_GUI_HELPER"] == "/usr/bin/custom-askpass"
        assert env["TEROK_ASKPASS_SOCKET"] == "/run/x.sock"

    def test_takes_over_when_existing_askpass_has_no_gui(self) -> None:
        """``SSH_ASKPASS`` set but no ``DISPLAY``/``WAYLAND_DISPLAY`` — ours wins.

        This is the headless-ssh-session / container scenario.  A GUI
        askpass inherited from the login env can't actually render, so
        we step in with the Textual modal instead of silently failing.
        """
        env = build_askpass_env(
            {"SSH_ASKPASS": "/usr/libexec/seahorse-askpass"},
            socket_path="/run/x.sock",
            helper_bin="/usr/bin/terok-askpass",
        )
        assert env["SSH_ASKPASS"] == "/usr/bin/terok-askpass"
        assert env["TEROK_ASKPASS_SOCKET"] == "/run/x.sock"

    def test_base_env_is_not_mutated(self) -> None:
        """The function returns a copy — callers' dicts stay pristine."""
        base = {"HOME": "/home/u"}
        build_askpass_env(base, socket_path="/s", helper_bin="/h")
        assert base == {"HOME": "/home/u"}

    def test_require_force_always_set(self) -> None:
        """Even when respecting an existing askpass, REQUIRE=force applies.

        Without this, OpenSSH prefers ``/dev/tty`` over any askpass when
        a controlling tty is present — undoing the TUI-isolation work
        from PR #821.
        """
        assert (
            build_askpass_env({}, socket_path="/s", helper_bin="/h")["SSH_ASKPASS_REQUIRE"]
            == "force"
        )


# ── default_socket_path ───────────────────────────────────────────────


class TestSocketPath:
    """Per-process socket path under terok's namespace runtime dir.

    We delegate to [`terok_sandbox.paths.namespace_runtime_dir`][terok_sandbox.paths.namespace_runtime_dir]
    for the XDG resolution order — patching the [`Config`][terok.lib.api.Config]
    snapshot lets us pin the path regardless of the host's env and
    ensures no ``/tmp`` fallback leaks back in.
    """

    @staticmethod
    def _config_with_runtime_dir(tmp_path: Path) -> object:
        """Build a minimal [`Config`][terok.lib.api.Config] with ``runtime_dir`` pinned."""
        from terok.lib.api import Config

        return Config(
            config_root=tmp_path,
            core_state_dir=tmp_path,
            runtime_dir=tmp_path,
            archive_dir=tmp_path,
            vault_dir=tmp_path,
            user_projects_dir=tmp_path,
            global_config_path=tmp_path / "config.yml",
            public_host="127.0.0.1",
            shield_disable_firewall_no_protection=False,
            tui_default_tmux=False,
            tui_external_editor=True,
            shield_security_hint="",
        )

    def test_uses_namespace_runtime_dir(self, tmp_path: Path) -> None:
        """Socket sits inside whatever ``namespace_runtime_dir`` returns — always.

        This is the anti-regression for bandit B108: the resolver is
        the single source of truth for where ephemeral terok state
        lives, and it has no ``/tmp`` leaf in its fallback chain
        (``$XDG_RUNTIME_DIR/terok`` → ``$XDG_STATE_HOME/terok`` →
        ``~/.local/state/terok``).  Delegating here means any future
        change to that chain lands transparently.
        """
        cfg = self._config_with_runtime_dir(tmp_path)
        with patch("terok.tui.askpass_service.get_config", return_value=cfg):
            path = default_socket_path(pid=12345)
        assert path == tmp_path / "askpass-12345.sock"

    def test_does_not_read_xdg_runtime_dir_directly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``default_socket_path`` goes through the resolver, not the raw env var.

        Proves we're not reimplementing the XDG chain locally.
        """
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp/unused-should-be-ignored")
        cfg = self._config_with_runtime_dir(tmp_path)
        with patch("terok.tui.askpass_service.get_config", return_value=cfg):
            assert default_socket_path(pid=1).parent == tmp_path


# ── gui_askpass_usable ────────────────────────────────────────────────


class TestGuiAskpassUsable:
    """Predicate that decides whether to bother spinning up our socket."""

    def test_needs_both_askpass_and_gui(self) -> None:
        """A GUI helper without a display can't actually render — skip it."""
        assert not gui_askpass_usable({"SSH_ASKPASS": "/usr/libexec/seahorse-askpass"})
        assert not gui_askpass_usable({"DISPLAY": ":0"})
        assert not gui_askpass_usable({})

    def test_x11_session_is_usable(self) -> None:
        """``DISPLAY`` + ``SSH_ASKPASS`` is the classic desktop path."""
        assert gui_askpass_usable({"SSH_ASKPASS": "/usr/libexec/seahorse-askpass", "DISPLAY": ":0"})

    def test_wayland_session_is_usable(self) -> None:
        """Wayland's ``WAYLAND_DISPLAY`` is accepted alongside X's ``DISPLAY``."""
        assert gui_askpass_usable(
            {"SSH_ASKPASS": "/usr/libexec/seahorse-askpass", "WAYLAND_DISPLAY": "wayland-0"}
        )


# ── AskpassService lifecycle ──────────────────────────────────────────


class _BareApp(App):
    """Minimal ``App`` that doesn't push any screen — good enough to host a service."""


class TestServiceLifecycle:
    """Service start/stop, idempotency, and socket permissions."""

    @pytest.mark.asyncio
    async def test_start_binds_socket_and_stop_unlinks_it(self, tmp_path: Path) -> None:
        """After ``start`` the socket file exists; after ``stop`` it's gone."""
        sock = tmp_path / "askpass.sock"
        async with _BareApp().run_test():
            service = AskpassService(_BareApp(), socket_path=sock, helper_bin=tmp_path / "bin")
            await service.start()
            try:
                assert sock.exists()
                assert stat.S_ISSOCK(sock.stat().st_mode)
            finally:
                await service.stop()
            assert not sock.exists()

    @pytest.mark.asyncio
    async def test_socket_mode_is_0600(self, tmp_path: Path) -> None:
        """Socket permissions must not grant access beyond the owner."""
        sock = tmp_path / "askpass.sock"
        async with _BareApp().run_test():
            service = AskpassService(_BareApp(), socket_path=sock, helper_bin=tmp_path / "bin")
            await service.start()
            try:
                mode = sock.stat().st_mode & 0o777
                # Owner has rw; group and other must be empty.
                assert mode & 0o077 == 0
            finally:
                await service.stop()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, tmp_path: Path) -> None:
        """Two calls to ``start`` don't rebind or error."""
        sock = tmp_path / "askpass.sock"
        async with _BareApp().run_test():
            service = AskpassService(_BareApp(), socket_path=sock, helper_bin=tmp_path / "bin")
            await service.start()
            try:
                assert service.is_running
                await service.start()  # second call is a no-op
                assert service.is_running
            finally:
                await service.stop()

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self, tmp_path: Path) -> None:
        """``stop`` on a never-started / already-stopped service is harmless."""
        sock = tmp_path / "askpass.sock"
        service = AskpassService(_BareApp(), socket_path=sock, helper_bin=tmp_path / "bin")
        await service.stop()  # never started — must not raise
        async with _BareApp().run_test():
            await service.start()
            await service.stop()
            await service.stop()  # second stop is a no-op


# ── end-to-end service round-trip ─────────────────────────────────────


class _ModalAnsweringApp(App):
    """App that auto-dismisses any [`AskpassModal`][terok.tui.askpass_service.AskpassModal] that gets pushed.

    ``canned_answer`` is the value it dismisses with: a string means
    "user typed this passphrase"; ``None`` means "user clicked Cancel".
    """

    def __init__(self, canned_answer: str | None) -> None:
        super().__init__()
        self._canned_answer = canned_answer

    async def on_screen_resume(self) -> None:  # pragma: no cover - edge path
        pass

    def on_mount(self) -> None:
        self.install_screen = None  # placeholder — real work in client code

    async def _auto_answer(self) -> None:
        """Wait for an [`AskpassModal`][terok.tui.askpass_service.AskpassModal] and dismiss it after a short pause."""
        # Busy-wait briefly until the modal lands on top of the stack.
        for _ in range(50):
            if isinstance(self.screen, AskpassModal):
                self.screen.dismiss(self._canned_answer)
                return
            await asyncio.sleep(0.02)


class TestServiceRoundTrip:
    """End-to-end: a unix-socket client drives the modal through the service."""

    @pytest.mark.asyncio
    async def test_answer_reaches_client(self, tmp_path: Path) -> None:
        """Client sends a request, modal is auto-answered, reply carries the passphrase."""
        sock = tmp_path / "askpass.sock"
        app = _ModalAnsweringApp(canned_answer="letmein")
        async with app.run_test() as pilot:
            service = AskpassService(app, socket_path=sock, helper_bin=tmp_path / "bin")
            await service.start()
            try:
                auto_task = asyncio.create_task(app._auto_answer())

                reader, writer = await asyncio.open_unix_connection(str(sock))
                req = proto.make_request("Enter passphrase:", request_id="rid-1")
                writer.write(proto.encode(req))
                await writer.drain()
                await pilot.pause()  # give the service + modal a chance to run

                raw = await reader.readline()
                reply = proto.decode(raw)
                assert reply == {"request_id": "rid-1", "answer": "letmein"}

                writer.close()
                await writer.wait_closed()
                await auto_task
            finally:
                await service.stop()

    @pytest.mark.asyncio
    async def test_cancel_is_relayed_to_client(self, tmp_path: Path) -> None:
        """User hits Cancel → reply carries ``cancel: true``."""
        sock = tmp_path / "askpass.sock"
        app = _ModalAnsweringApp(canned_answer=None)
        async with app.run_test() as pilot:
            service = AskpassService(app, socket_path=sock, helper_bin=tmp_path / "bin")
            await service.start()
            try:
                auto_task = asyncio.create_task(app._auto_answer())

                reader, writer = await asyncio.open_unix_connection(str(sock))
                req = proto.make_request("Enter passphrase:", request_id="rid-2")
                writer.write(proto.encode(req))
                await writer.drain()
                await pilot.pause()

                raw = await reader.readline()
                reply = proto.decode(raw)
                assert reply == {"request_id": "rid-2", "cancel": True}

                writer.close()
                await writer.wait_closed()
                await auto_task
            finally:
                await service.stop()


class _PendingApp(App):
    """Host console actions whose SSH prompts can be opened explicitly."""

    def __init__(self) -> None:
        super().__init__()
        self.console_logs = ConsoleLogRegistry()


type _PendingService = tuple[_PendingApp, Pilot, AskpassService, ConsoleLogEntry]


@pytest.fixture
async def pending_service(tmp_path: Path) -> AsyncIterator[_PendingService]:
    """Run a managed action and its askpass service without any real SSH process."""
    app = _PendingApp()
    entry = app.console_logs.create("Gate sync", [], "sync")
    async with app.run_test() as pilot:
        service = AskpassService(
            app, socket_path=tmp_path / "askpass.sock", helper_bin=tmp_path / "helper"
        )
        await service.start()
        try:
            yield app, pilot, service, entry
        finally:
            await service.stop()


async def _send_prompt(
    service: AskpassService, entry: ConsoleLogEntry, *, external: bool = False
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect a helper and send an attributed prompt, leaving the socket open."""
    reader, writer = await asyncio.open_unix_connection(service.socket_path)
    request = proto.make_request("Unlock key", action_id=entry.id, external=external)
    writer.write(proto.encode(request))
    await writer.drain()
    return reader, writer


async def test_managed_prompt_notifies_without_stealing_focus(
    pending_service: _PendingService,
) -> None:
    """An explicit open is required; answering restores the action's running state."""
    app, pilot, service, entry = pending_service
    original_screen = app.screen
    with patch.object(app, "notify") as notify:
        reader, writer = await _send_prompt(service, entry)
        await pilot.pause()
        assert app.screen is original_screen
        assert entry.status is LogStatus.WAITING
        assert entry.pending_inputs == 1
        notify.assert_called_once()
        assert "needs input" in notify.call_args.args[0]

    entry.request_input()
    await pilot.pause()
    assert isinstance(app.screen, AskpassModal)
    app.screen.dismiss("secret")
    reply = proto.decode(await asyncio.wait_for(reader.readline(), timeout=2))
    assert reply["answer"] == "secret"
    await pilot.pause()
    assert entry.status is LogStatus.RUNNING
    assert entry.pending_inputs == 0
    assert entry.lines == []
    writer.close()
    await writer.wait_closed()


async def test_deferred_prompt_remains_reachable(pending_service: _PendingService) -> None:
    """Escape postpones a managed prompt; explicit Cancel sends the SSH cancellation."""
    app, pilot, service, entry = pending_service
    reader, writer = await _send_prompt(service, entry)
    await pilot.pause()
    entry.request_input()
    await pilot.pause()
    await pilot.press("escape")
    await pilot.pause()
    assert not isinstance(app.screen, AskpassModal)
    assert entry.pending_inputs == 1
    assert entry.status is LogStatus.WAITING
    entry.request_input()
    await pilot.pause()
    await pilot.click("#askpass-cancel")
    reply = proto.decode(await asyncio.wait_for(reader.readline(), timeout=2))
    assert reply["cancel"] is True
    writer.close()
    await writer.wait_closed()


async def test_concurrent_prompts_keep_action_waiting(pending_service: _PendingService) -> None:
    """Resolving one request does not clear a second request for the same action."""
    app, pilot, service, entry = pending_service
    reader1, writer1 = await _send_prompt(service, entry)
    reader2, writer2 = await _send_prompt(service, entry)
    await pilot.pause()
    assert entry.pending_inputs == 2
    entry.request_input()
    await pilot.pause()
    app.screen.dismiss("first")
    assert proto.decode(await asyncio.wait_for(reader1.readline(), timeout=2))["answer"] == "first"
    await pilot.pause()
    assert entry.pending_inputs == 1
    assert entry.status is LogStatus.WAITING
    entry.request_input()
    await pilot.pause()
    app.screen.dismiss("second")
    assert proto.decode(await asyncio.wait_for(reader2.readline(), timeout=2))["answer"] == "second"
    await pilot.pause()
    assert entry.status is LogStatus.RUNNING
    for writer in (writer1, writer2):
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize("open_prompt", [False, True])
async def test_disconnected_native_prompt_cleans_up(
    pending_service: _PendingService, open_prompt: bool
) -> None:
    """A dead helper cannot leave an action waiting or an obsolete password dialog."""
    app, pilot, service, entry = pending_service
    _, writer = await _send_prompt(service, entry)
    await pilot.pause()
    if open_prompt:
        entry.request_input()
        await pilot.pause()
    writer.close()
    await writer.wait_closed()
    await pilot.pause()
    assert entry.pending_inputs == 0
    assert entry.status is LogStatus.RUNNING
    assert not isinstance(app.screen, AskpassModal)


async def test_disconnected_prompt_does_not_pop_another_screen(
    pending_service: _PendingService,
) -> None:
    """An expired prompt closes only after an unrelated overlaid modal is dismissed."""
    app, pilot, service, entry = pending_service
    _, writer = await _send_prompt(service, entry)
    await pilot.pause()
    entry.request_input()
    await pilot.pause()
    overlay = ModalScreen()
    app.push_screen(overlay)
    await pilot.pause()
    writer.close()
    await writer.wait_closed()
    await pilot.pause()
    assert entry.pending_inputs == 0
    assert app.screen is overlay
    overlay.dismiss()
    await pilot.pause()
    assert not isinstance(app.screen, AskpassModal)


async def test_gui_prompt_tracks_connection_without_a_native_dialog(
    pending_service: _PendingService,
) -> None:
    """GUI helper lifetime marks its action waiting without duplicating its prompt."""
    app, pilot, service, entry = pending_service
    original_screen = app.screen
    _, writer = await _send_prompt(service, entry, external=True)
    await pilot.pause()
    assert entry.pending_inputs == 1
    assert entry.status is LogStatus.WAITING
    with patch.object(app, "notify") as notify:
        entry.request_input()
        notify.assert_called_once()
        assert "desktop dialog" in notify.call_args.args[0]
    assert app.screen is original_screen
    writer.close()
    await writer.wait_closed()
    await pilot.pause()
    assert entry.pending_inputs == 0


async def test_stop_clears_pending_helpers(pending_service: _PendingService) -> None:
    """Stopping the service disconnects helpers and removes pending state."""
    _, pilot, service, entry = pending_service
    reader, writer = await _send_prompt(service, entry)
    await pilot.pause()
    await asyncio.wait_for(service.stop(), timeout=2)
    assert await asyncio.wait_for(reader.read(), timeout=2) == b""
    assert entry.pending_inputs == 0
    assert not service._clients
    writer.close()
    await writer.wait_closed()


# ── helper exit-code contract ─────────────────────────────────────────


class TestHelperMain:
    """Cover the [`terok.tui.askpass.main`][terok.tui.askpass.main] code paths via direct calls.

    These use a tiny blocking-socket stand-in for the service so we don't
    have to drag asyncio in — the protocol layer is identical.
    """

    @pytest.mark.asyncio
    async def test_helper_exits_zero_and_prints_answer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Accept path: helper prints the passphrase and exits 0."""
        import socket as _socket

        from terok.tui import askpass as helper

        sock_path = tmp_path / "helper.sock"
        server = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)

        async def _fake_tui() -> None:
            conn, _ = await asyncio.get_running_loop().sock_accept(server)
            loop = asyncio.get_running_loop()
            raw = b""
            while not raw.endswith(b"\n"):
                chunk = await loop.sock_recv(conn, 4096)
                if not chunk:
                    break
                raw += chunk
            request_id = proto.parse_request(proto.decode(raw)).request_id
            await loop.sock_sendall(conn, proto.encode(proto.make_answer(request_id, "secret")))
            conn.close()

        try:
            tui_task = asyncio.create_task(_fake_tui())
            monkeypatch.setenv("TEROK_ASKPASS_SOCKET", str(sock_path))

            # Run the blocking helper off the event loop so the fake TUI can service it.
            rc = await asyncio.to_thread(helper.main, ["terok-askpass", "Enter passphrase:"])
            await tui_task
        finally:
            server.close()

        assert rc == 0
        assert capsys.readouterr().out.strip() == "secret"

    @pytest.mark.asyncio
    async def test_helper_exits_nonzero_on_cancel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancel path: helper exits non-zero, ssh aborts immediately."""
        import socket as _socket

        from terok.tui import askpass as helper

        sock_path = tmp_path / "helper.sock"
        server = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)

        async def _fake_tui() -> None:
            conn, _ = await asyncio.get_running_loop().sock_accept(server)
            loop = asyncio.get_running_loop()
            raw = b""
            while not raw.endswith(b"\n"):
                chunk = await loop.sock_recv(conn, 4096)
                if not chunk:
                    break
                raw += chunk
            request_id = proto.parse_request(proto.decode(raw)).request_id
            await loop.sock_sendall(conn, proto.encode(proto.make_cancel(request_id)))
            conn.close()

        try:
            tui_task = asyncio.create_task(_fake_tui())
            monkeypatch.setenv("TEROK_ASKPASS_SOCKET", str(sock_path))
            rc = await asyncio.to_thread(helper.main, ["terok-askpass", "prompt"])
            await tui_task
        finally:
            server.close()

        assert rc != 0

    def test_helper_exits_nonzero_when_socket_env_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without ``TEROK_ASKPASS_SOCKET`` the helper can't do anything — bail."""
        from terok.tui import askpass as helper

        monkeypatch.delenv("TEROK_ASKPASS_SOCKET", raising=False)
        assert helper.main(["terok-askpass", "prompt"]) != 0

    def test_helper_exits_nonzero_when_socket_unreachable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pointed at a non-existent path, the helper errors cleanly."""
        from terok.tui import askpass as helper

        monkeypatch.setenv("TEROK_ASKPASS_SOCKET", str(tmp_path / "never-bound.sock"))
        assert helper.main(["terok-askpass", "prompt"]) != 0


@pytest.mark.parametrize("exit_code", [0, 1])
async def test_gui_wrapper_tracks_native_helper_lifetime(
    pending_service: _PendingService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
) -> None:
    """Wrapping a GUI reports waiting, preserves its argv/env, and relays its exit code."""
    from terok.tui import askpass as helper

    _, pilot, service, entry = pending_service
    gui_helper = str(tmp_path / "desktop-askpass")
    monkeypatch.setenv("TEROK_ASKPASS_SOCKET", str(service.socket_path))
    monkeypatch.setenv(proto.GUI_HELPER_ENV, gui_helper)
    monkeypatch.setenv(proto.ACTION_ID_ENV, str(entry.id))
    entered, release = threading.Event(), threading.Event()

    def run_gui(argv: list[str], *, env: dict[str, str], check: bool) -> SimpleNamespace:
        assert argv == [gui_helper, "Unlock key"]
        assert env["SSH_ASKPASS"] == gui_helper
        assert proto.GUI_HELPER_ENV not in env
        assert check is False
        entered.set()
        assert release.wait(timeout=5)
        return SimpleNamespace(returncode=exit_code)

    with patch.object(helper.subprocess, "run", side_effect=run_gui):
        task = asyncio.create_task(asyncio.to_thread(helper.main, ["terok-askpass", "Unlock key"]))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await pilot.pause()
            assert entry.pending_inputs == 1
            assert entry.status is LogStatus.WAITING
            assert entry.lines == []
        finally:
            release.set()
        assert await asyncio.wait_for(task, timeout=2) == exit_code
    await pilot.pause()
    assert entry.pending_inputs == 0
    assert entry.status is LogStatus.RUNNING


# silence pytest about the unused fixture scaffold
_ = os


async def test_shutdown_closes_accepted_client_whose_handler_has_not_started(
    tmp_path: Path,
) -> None:
    """A late accepted handler cannot keep Server.wait_closed blocked at TUI exit."""
    stopping: list[asyncio.Task[None]] = []

    class StopOnAccept(AskpassService):
        def _handle_client(self, reader, writer):
            stopping.append(asyncio.create_task(self.stop()))
            return super()._handle_client(reader, writer)

    service = StopOnAccept(App(), socket_path=tmp_path / "late-client.sock")
    await service.start()
    reader, writer = await asyncio.open_unix_connection(str(service.socket_path))
    try:
        assert await asyncio.wait_for(reader.read(), timeout=2) == b""
        await asyncio.wait_for(asyncio.gather(*stopping), timeout=2)
        assert not service._clients
        assert not service.is_running
    finally:
        writer.close()
        await writer.wait_closed()
        await service.stop()
