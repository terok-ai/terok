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
    _bootstrap_password_hash,
    _hash_password,
    _read_password_hash,
    _valid_port,
    _verify_password,
    _write_password_hash,
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
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Server is instantiated with default host and port when no args given."""
        mock_server_instance = mock.MagicMock()
        mock_server_cls = mock.MagicMock(return_value=mock_server_instance)

        server_mod = mock.MagicMock()
        server_mod.Server = mock_server_cls

        monkeypatch.setitem(sys.modules, "textual_serve", mock.MagicMock())
        monkeypatch.setitem(sys.modules, "textual_serve.server", server_mod)
        monkeypatch.setattr("sys.argv", ["terok-web"])
        monkeypatch.setenv("TEROK_CONFIG_DIR", str(tmp_path))

        main()

        mock_server_cls.assert_called_once_with(
            "terok-tui", host="localhost", port=8566, public_url=None
        )
        mock_server_instance.serve.assert_called_once()

    def test_server_created_with_custom_args(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Server respects --host and --port arguments."""
        mock_server_instance = mock.MagicMock()
        mock_server_cls = mock.MagicMock(return_value=mock_server_instance)

        server_mod = mock.MagicMock()
        server_mod.Server = mock_server_cls

        monkeypatch.setitem(sys.modules, "textual_serve", mock.MagicMock())
        monkeypatch.setitem(sys.modules, "textual_serve.server", server_mod)
        monkeypatch.setattr("sys.argv", ["terok-web", "--host", "0.0.0.0", "--port", "9000"])
        monkeypatch.setenv("TEROK_CONFIG_DIR", str(tmp_path))

        main()

        mock_server_cls.assert_called_once_with(
            "terok-tui", host="0.0.0.0", port=9000, public_url=None
        )
        mock_server_instance.serve.assert_called_once()


class TestPasswordHashing:
    """Tests for the scrypt password storage and verify pipeline."""

    def test_hash_roundtrip(self) -> None:
        """A password verifies against its own hash and fails on a mismatch."""
        record = _hash_password("hunter2")
        assert record.startswith("scrypt$")
        assert _verify_password("hunter2", record)
        assert not _verify_password("hunter3", record)

    def test_hash_is_salted(self) -> None:
        """Hashing the same password twice produces different records."""
        assert _hash_password("same") != _hash_password("same")

    def test_verify_rejects_garbage(self) -> None:
        """Malformed records never verify."""
        assert not _verify_password("x", "not-a-record")
        assert not _verify_password("x", "scrypt$bogus")
        assert not _verify_password("x", "scrypt$1$1$1$!!!$!!!")

    def test_file_roundtrip(self, tmp_path: Path) -> None:
        """Written files come back as the original record and are mode 0600."""
        path = tmp_path / "pw"
        _write_password_hash(path, "s3cret")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        record = _read_password_hash(path)
        assert record is not None and _verify_password("s3cret", record)

    def test_read_missing_returns_none(self, tmp_path: Path) -> None:
        """Reading a non-existent path returns ``None`` (not an error)."""
        assert _read_password_hash(tmp_path / "nope") is None

    def test_read_rejects_loose_perms(self, tmp_path: Path) -> None:
        """A 0644 password file is refused on load."""
        path = tmp_path / "pw"
        _write_password_hash(path, "s3cret")
        os.chmod(path, 0o644)
        with pytest.raises(SystemExit, match="mode "):
            _read_password_hash(path)


class TestBootstrap:
    """Tests for first-launch password minting and reuse."""

    def test_first_launch_mints_and_prints(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """On first launch a random password is generated, printed, and stored."""
        path = tmp_path / "serve.password"
        stored = _bootstrap_password_hash(path)
        assert stored.startswith("scrypt$")
        err = capsys.readouterr().err
        assert "password = " in err
        printed = next(line for line in err.splitlines() if "password = " in line)
        password = printed.split("password = ", 1)[1]
        assert _verify_password(password, stored)

    def test_second_launch_reuses_hash(self, tmp_path: Path) -> None:
        """If a hash already exists it is returned verbatim (no reprint)."""
        path = tmp_path / "serve.password"
        first = _bootstrap_password_hash(path)
        second = _bootstrap_password_hash(path)
        assert first == second


class TestBasicAuthMiddleware:
    """Tests for the basic-auth aiohttp middleware."""

    async def test_rejects_without_credentials(self) -> None:
        """A request without Authorization gets a 401 + Basic challenge."""
        from aiohttp.test_utils import make_mocked_request

        from terok.tui.serve import _basic_auth_middleware, _hash_password

        mw = _basic_auth_middleware(_hash_password("secret"))
        req = make_mocked_request("GET", "/")
        resp = await mw(req, lambda _: None)
        assert resp.status == 401
        assert "Basic" in resp.headers["WWW-Authenticate"]

    async def test_accepts_correct_credentials(self) -> None:
        """Valid Basic auth passes through to the inner handler."""
        from base64 import b64encode

        from aiohttp import web
        from aiohttp.test_utils import make_mocked_request

        from terok.tui.serve import _basic_auth_middleware, _hash_password

        mw = _basic_auth_middleware(_hash_password("secret"))
        token = b64encode(b"terok:secret").decode()
        req = make_mocked_request("GET", "/", headers={"Authorization": f"Basic {token}"})

        async def inner(_request: web.Request) -> web.Response:
            return web.Response(status=204)

        resp = await mw(req, inner)
        assert resp.status == 204

    async def test_rejects_wrong_password(self) -> None:
        """Wrong password still yields a 401 rather than passing through."""
        from base64 import b64encode

        from aiohttp.test_utils import make_mocked_request

        from terok.tui.serve import _basic_auth_middleware, _hash_password

        mw = _basic_auth_middleware(_hash_password("secret"))
        token = b64encode(b"terok:wrong").decode()
        req = make_mocked_request("GET", "/", headers={"Authorization": f"Basic {token}"})
        resp = await mw(req, lambda _: None)
        assert resp.status == 401
