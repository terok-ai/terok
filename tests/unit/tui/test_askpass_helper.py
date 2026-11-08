# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Desktop authentication remains usable when optional tracking fails."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest

from terok.tui import askpass as helper, askpass_protocol as proto

type _HelperMocks = tuple[str, MagicMock, MagicMock, MagicMock]


@pytest.fixture
def helper_mocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_HelperMocks]:
    """Replace socket and GUI execution without touching the operator's filesystem."""
    gui_helper = str(tmp_path / "desktop-askpass")
    monkeypatch.setenv(proto.GUI_HELPER_ENV, gui_helper)
    monkeypatch.setenv(proto.ACTION_ID_ENV, "7")
    monkeypatch.setenv("TEROK_ASKPASS_SOCKET", str(tmp_path / "askpass.sock"))
    with (
        patch.object(helper.socket, "socket") as factory,
        patch.object(helper.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run,
    ):
        yield gui_helper, factory, factory.return_value.__enter__.return_value, run


@pytest.mark.parametrize("failure", ["missing", "create", "connect", "send"])
@pytest.mark.parametrize("exit_code", [0, 23])
def test_gui_runs_once_when_tracking_unavailable(
    helper_mocks: _HelperMocks,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    exit_code: int,
) -> None:
    """Tracking failures cannot suppress or retry the configured desktop helper."""
    gui_helper, factory, sock, run = helper_mocks
    run.return_value.returncode = exit_code
    if failure == "missing":
        monkeypatch.delenv("TEROK_ASKPASS_SOCKET")
    else:
        operation = {"create": factory, "connect": sock.connect, "send": sock.sendall}[failure]
        operation.side_effect = OSError("Tracking unavailable")

    assert helper.main(["terok-askpass", "Unlock key", "ignored argument"]) == exit_code
    run.assert_called_once_with([gui_helper, "Unlock key"], env=ANY, check=False)
    env = run.call_args.kwargs["env"]
    assert env["SSH_ASKPASS"] == gui_helper
    assert proto.GUI_HELPER_ENV not in env
    if failure == "missing":
        factory.assert_not_called()


@pytest.mark.parametrize("external", [False, True], ids=["native", "gui"])
@pytest.mark.parametrize(
    "action_id",
    ["", "not-an-id", "0", "-1", "²", pytest.param("9" * 5000, id="oversized")],
)
def test_invalid_action_identity_is_unattributed(
    helper_mocks: _HelperMocks,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    action_id: str,
    external: bool,
) -> None:
    """Both authentication paths survive malformed or non-positive metadata."""
    _, _, sock, run = helper_mocks
    monkeypatch.setenv(proto.ACTION_ID_ENV, action_id)
    if not external:
        monkeypatch.delenv(proto.GUI_HELPER_ENV)

    def reply(_size: int) -> bytes:
        """Reply to the native request without exposing its answer to any log."""
        request = proto.parse_request(proto.decode(sock.sendall.call_args.args[0]))
        return proto.encode(proto.make_answer(request.request_id, "secret"))

    sock.recv.side_effect = reply
    assert helper.main(["terok-askpass", "Unlock key"]) == 0
    request = proto.parse_request(proto.decode(sock.sendall.call_args.args[0]))
    assert request.action_id is None
    assert request.external is external
    assert run.call_count == int(external)
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out == ("" if external else "secret\n")


@pytest.mark.parametrize("tracking_fails", [False, True])
def test_gui_launch_error_is_not_retried(
    helper_mocks: _HelperMocks,
    capsys: pytest.CaptureFixture[str],
    tracking_fails: bool,
) -> None:
    """A GUI executable failure gets its own diagnostic, not another launch attempt."""
    _, _, sock, run = helper_mocks
    if tracking_fails:
        sock.connect.side_effect = OSError("Tracking unavailable")
    run.side_effect = FileNotFoundError("GUI helper unavailable")
    assert helper.main(["terok-askpass", "Unlock key"]) == 1
    run.assert_called_once()
    error = capsys.readouterr().err
    assert "cannot run" in error
    assert "socket error" not in error


def test_native_prompt_still_requires_socket(
    helper_mocks: _HelperMocks,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without a desktop helper there is no answer source after TUI disconnect."""
    _, factory, _, run = helper_mocks
    monkeypatch.delenv(proto.GUI_HELPER_ENV)
    monkeypatch.delenv("TEROK_ASKPASS_SOCKET")
    assert helper.main(["terok-askpass", "Unlock key"]) == 1
    assert "TEROK_ASKPASS_SOCKET not set" in capsys.readouterr().err
    factory.assert_not_called()
    run.assert_not_called()


def test_gui_receives_default_prompt_when_no_argument_is_given(helper_mocks: _HelperMocks) -> None:
    """The wrapper always passes one prompt argument, including the native fallback."""
    gui_helper, _, _, run = helper_mocks
    assert helper.main(["terok-askpass"]) == 0
    run.assert_called_once_with([gui_helper, helper._FALLBACK_PROMPT], env=ANY, check=False)


@pytest.mark.parametrize("external", [False, True], ids=["native", "gui"])
def test_unencodable_prompt_does_not_bypass_authentication_fallback(
    helper_mocks: _HelperMocks,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    external: bool,
) -> None:
    """Surrogate-escaped Unix argv reaches the GUI; native transport fails cleanly."""
    gui_helper, _, sock, run = helper_mocks
    prompt = "Unlock key 'bad\udcff'"
    if not external:
        monkeypatch.delenv(proto.GUI_HELPER_ENV)
    assert helper.main(["terok-askpass", prompt]) == (0 if external else 1)
    sock.sendall.assert_not_called()
    if external:
        run.assert_called_once_with([gui_helper, prompt], env=ANY, check=False)
        assert capsys.readouterr().err == ""
    else:
        run.assert_not_called()
        assert "socket error" in capsys.readouterr().err
