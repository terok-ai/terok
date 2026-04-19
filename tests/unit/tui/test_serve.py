# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the terok-web serve entry point."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

from terok.tui.serve import (
    _load_or_mint_password,
    _secure_runtime_dir,
    _valid_port,
    main,
)


class TestValidPort:
    """Tests for port validation."""

    @pytest.mark.parametrize("value", ["1", "80", "8566", "65535"])
    def test_accepts_valid_ports(self, value: str) -> None:
        """Valid port numbers are returned as integers."""
        assert _valid_port(value) == int(value)

    @pytest.mark.parametrize("value", ["0", "-1", "65536", "99999"])
    def test_rejects_out_of_range(self, value: str) -> None:
        """Out-of-range port numbers raise ArgumentTypeError with descriptive message."""
        with pytest.raises(argparse.ArgumentTypeError, match="must be between 1 and 65535"):
            _valid_port(value)

    @pytest.mark.parametrize("value", ["abc", "", "12.5"])
    def test_rejects_non_integer(self, value: str) -> None:
        """Non-integer strings raise ArgumentTypeError with descriptive message."""
        with pytest.raises(argparse.ArgumentTypeError, match="must be an integer"):
            _valid_port(value)


class TestMain:
    """Tests for the main entry point."""

    def test_missing_textual_serve_exits(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When textual-serve is not installed, main prints guidance and exits."""
        monkeypatch.setitem(sys.modules, "textual_serve", None)
        monkeypatch.setitem(sys.modules, "textual_serve.server", None)
        with pytest.raises(SystemExit, match="1"):
            main()
        captured = capsys.readouterr()
        assert "textual-serve" in captured.err
        assert "pip install textual-serve" in captured.err

    def test_server_created_with_defaults(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Server is instantiated with default host and port when no args given."""
        mock_server_instance = mock.MagicMock()
        mock_server_cls = mock.MagicMock(return_value=mock_server_instance)

        server_mod = mock.MagicMock()
        server_mod.Server = mock_server_cls

        monkeypatch.setitem(sys.modules, "textual_serve", mock.MagicMock())
        monkeypatch.setitem(sys.modules, "textual_serve.server", server_mod)
        monkeypatch.setattr("sys.argv", ["terok-web"])
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

        main()

        mock_server_cls.assert_called_once_with(
            "terok-tui", host="localhost", port=8566, public_url=None
        )
        mock_server_instance.serve.assert_called_once()

    def test_server_created_with_custom_args(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Server respects --host and --port arguments."""
        mock_server_instance = mock.MagicMock()
        mock_server_cls = mock.MagicMock(return_value=mock_server_instance)

        server_mod = mock.MagicMock()
        server_mod.Server = mock_server_cls

        monkeypatch.setitem(sys.modules, "textual_serve", mock.MagicMock())
        monkeypatch.setitem(sys.modules, "textual_serve.server", server_mod)
        monkeypatch.setattr("sys.argv", ["terok-web", "--host", "0.0.0.0", "--port", "9000"])
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

        main()

        mock_server_cls.assert_called_once_with(
            "terok-tui", host="0.0.0.0", port=9000, public_url=None
        )
        mock_server_instance.serve.assert_called_once()


class TestPasswordHandling:
    """Tests for the ephemeral password file lifecycle."""

    def test_secure_runtime_dir_creates_0700(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """First call creates the runtime dir with mode 0700."""
        runtime = tmp_path / "x"
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        out = _secure_runtime_dir()
        assert out == runtime / "terok"
        assert stat.S_IMODE(out.stat().st_mode) == 0o700

    def test_secure_runtime_dir_rejects_loose_perms(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A pre-existing runtime dir with 0755 is refused."""
        runtime = tmp_path / "x" / "terok"
        runtime.mkdir(parents=True, mode=0o755)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "x"))
        with pytest.raises(SystemExit, match="mode "):
            _secure_runtime_dir()

    def test_secure_runtime_dir_rejects_symlink(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A symlink as the runtime dir is refused (defends against /tmp races)."""
        real = tmp_path / "real"
        real.mkdir(mode=0o700)
        link = tmp_path / "x" / "terok"
        link.parent.mkdir()
        link.symlink_to(real)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "x"))
        with pytest.raises(SystemExit, match="not a plain directory"):
            _secure_runtime_dir()

    def test_mint_and_reuse_password(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Fresh run mints a password; a second call returns the same value."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        first = _load_or_mint_password()
        assert first
        path = tmp_path / "terok" / "serve.password"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        second = _load_or_mint_password()
        assert first == second

    def test_rejects_loose_password_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A password file left 0644 is refused on load."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        _load_or_mint_password()  # seed
        path = tmp_path / "terok" / "serve.password"
        os.chmod(path, 0o644)
        with pytest.raises(SystemExit, match="mode "):
            _load_or_mint_password()


class TestBasicAuthMiddleware:
    """Tests for the basic-auth aiohttp middleware."""

    def _run(self, coro):
        """Run *coro* to completion using a fresh event loop."""
        import asyncio

        return asyncio.new_event_loop().run_until_complete(coro)

    def test_rejects_without_credentials(self) -> None:
        """A request without Authorization gets a 401 + Basic challenge."""
        from aiohttp.test_utils import make_mocked_request

        from terok.tui.serve import _basic_auth_middleware

        mw = _basic_auth_middleware("secret")
        req = make_mocked_request("GET", "/")
        resp = self._run(mw(req, lambda _: None))
        assert resp.status == 401
        assert "Basic" in resp.headers["WWW-Authenticate"]

    def test_accepts_correct_credentials(self) -> None:
        """Valid Basic auth passes through to the inner handler."""
        from base64 import b64encode

        from aiohttp import web
        from aiohttp.test_utils import make_mocked_request

        from terok.tui.serve import _basic_auth_middleware

        mw = _basic_auth_middleware("secret")
        token = b64encode(b"terok:secret").decode()
        req = make_mocked_request("GET", "/", headers={"Authorization": f"Basic {token}"})

        async def inner(_request: web.Request) -> web.Response:
            return web.Response(status=204)

        resp = self._run(mw(req, inner))
        assert resp.status == 204

    def test_rejects_wrong_password(self) -> None:
        """Wrong password still yields a 401 rather than passing through."""
        from base64 import b64encode

        from aiohttp.test_utils import make_mocked_request

        from terok.tui.serve import _basic_auth_middleware

        mw = _basic_auth_middleware("secret")
        token = b64encode(b"terok:wrong").decode()
        req = make_mocked_request("GET", "/", headers={"Authorization": f"Basic {token}"})
        resp = self._run(mw(req, lambda _: None))
        assert resp.status == 401
