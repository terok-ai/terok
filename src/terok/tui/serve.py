# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Serve the Terok TUI as a web application via textual-serve."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import os
import secrets
import stat
import sys
from base64 import b64decode, urlsafe_b64decode, urlsafe_b64encode
from pathlib import Path
from typing import TYPE_CHECKING

from ..lib.core.paths import config_root

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiohttp import web

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 8566
_AUTH_USER = "terok"
"""Basic-auth username.  Constant so users only memorise the password."""
_AUTH_REALM = "terok-tui"

# scrypt KDF parameters.  N must be a power of 2; N=2**14 · r=8 · p=1 keeps
# verify time around 30 ms on modern hardware — slow enough to frustrate
# brute force yet fast enough for Basic auth re-prompts to stay snappy.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_PREFIX = "scrypt"


def _valid_port(value: str) -> int:
    """Validate that *value* is a valid TCP port number (1–65535)."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid port value: {value!r} (must be an integer)")
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError(
            f"invalid port value: {value!r} (must be between 1 and 65535)"
        )
    return port


def _password_path() -> Path:
    """Return the persistent path that holds the scrypt-hashed serve password."""
    root = config_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root / "serve.password"


def _hash_password(password: str) -> str:
    """Return a serialised scrypt record for *password*.

    Format: ``scrypt$N$r$p$salt_b64$hash_b64`` — a single ASCII line,
    self-describing so parameter changes can be detected on verify.
    """
    salt = secrets.token_bytes(16)
    hashed = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "$".join(
        (
            _SCRYPT_PREFIX,
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            urlsafe_b64encode(salt).decode().rstrip("="),
            urlsafe_b64encode(hashed).decode().rstrip("="),
        )
    )


def _verify_password(candidate: str, stored: str) -> bool:
    """Return True when *candidate* re-hashes to the stored scrypt record."""
    try:
        prefix, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$")
    except ValueError:
        return False
    if prefix != _SCRYPT_PREFIX:
        return False
    try:
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = urlsafe_b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
        expected = urlsafe_b64decode(hash_b64 + "=" * (-len(hash_b64) % 4))
        got = hashlib.scrypt(candidate.encode(), salt=salt, n=n, r=r, p=p, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


def _write_password_hash(path: Path, password: str) -> None:
    """Write the scrypt record for *password* to *path* (0600, no symlink follow)."""
    record = _hash_password(password) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid():
            raise SystemExit(f"Refusing to write {path}: not owned by current uid.")
        os.write(fd, record.encode())
    finally:
        os.close(fd)


def _read_password_hash(path: Path) -> str | None:
    """Return the stored scrypt record, or ``None`` if the file does not exist."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid():
            raise SystemExit(
                f"Refusing to read {path}: owned by uid {st.st_uid}, not {os.getuid()}."
            )
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise SystemExit(
                f"Refusing to read {path}: mode {oct(stat.S_IMODE(st.st_mode))}, expected 0600."
            )
        return os.read(fd, 4096).decode().strip() or None
    finally:
        os.close(fd)


def _prompt_password(confirm: bool = True) -> str:
    """Prompt for a password from stdin (hidden when TTY), confirming if asked."""
    if sys.stdin.isatty():
        pw = getpass.getpass("New terok-web password: ")
        if not pw:
            raise SystemExit("Password must not be empty.")
        if confirm and getpass.getpass("Confirm: ") != pw:
            raise SystemExit("Passwords did not match.")
        return pw
    pw = sys.stdin.readline().rstrip("\n")
    if not pw:
        raise SystemExit("Password must not be empty.")
    return pw


def _basic_auth_middleware(stored_hash: str) -> Callable[..., Awaitable[web.StreamResponse]]:
    """Build an aiohttp middleware that enforces Basic auth for every request.

    The username is fixed (:data:`_AUTH_USER`); the password is verified
    against *stored_hash* via scrypt.  On missing or wrong creds, a 401
    with ``WWW-Authenticate: Basic`` is returned so browsers prompt once
    per origin and cache the credentials for the tab lifetime.
    """
    from aiohttp import web

    challenge = {"WWW-Authenticate": f'Basic realm="{_AUTH_REALM}"'}
    user_prefix = f"{_AUTH_USER}:".encode()

    @web.middleware
    async def mw(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        """Pass through when creds verify; otherwise respond with a 401 challenge."""
        header = request.headers.get("Authorization", "")
        scheme, _, payload = header.partition(" ")
        if scheme.lower() == "basic":
            try:
                decoded = b64decode(payload.encode(), validate=True)
            except ValueError:
                decoded = b""
            if decoded.startswith(user_prefix):
                candidate = decoded[len(user_prefix) :].decode("utf-8", errors="replace")
                if _verify_password(candidate, stored_hash):
                    return await handler(request)
        return web.Response(status=401, headers=challenge, text="Unauthorized")

    return mw


def _build_server(command: str, host: str, port: int, public_url: str | None, stored_hash: str):
    """Construct a ``textual_serve`` Server with basic-auth middleware injected.

    Wraps ``Server._make_app`` on the instance so it returns the parent
    app with our auth middleware appended.  Using instance-level shadowing
    (instead of subclassing) keeps the indirection to one line and leaves
    the ``Server(...)`` call shape unchanged — it breaks only if textual-
    serve renames ``_make_app`` (asserted at import time).
    """
    from textual_serve.server import Server

    mw = _basic_auth_middleware(stored_hash)
    server = Server(command, host=host, port=port, public_url=public_url)
    original_make_app = server._make_app

    async def _make_app_with_auth():
        """Return the vanilla textual-serve app with our middleware appended."""
        app = await original_make_app()
        app.middlewares.append(mw)
        return app

    server._make_app = _make_app_with_auth
    return server


def _set_password_command(path: Path) -> None:
    """Prompt for a new password, write its hash to *path*, and exit."""
    pw = _prompt_password(confirm=True)
    _write_password_hash(path, pw)
    print(f"terok-web: password updated in {path}", file=sys.stderr)


def _bootstrap_password_hash(path: Path) -> str:
    """Load the stored hash, or mint + print a fresh random password on first run."""
    existing = _read_password_hash(path)
    if existing is not None:
        return existing
    fresh = secrets.token_urlsafe(16)
    _write_password_hash(path, fresh)
    print(
        "terok-web: no password set — generated a random one.\n"
        "           Copy it now; it will not be shown again.\n"
        "           Run 'terok-web --set-password' to set your own.",
        file=sys.stderr,
    )
    print(f"terok-web: password = {fresh}", file=sys.stderr)
    return _read_password_hash(path) or ""


def main() -> None:
    """Launch the Terok TUI as a web application.

    Uses textual-serve to expose the TUI over HTTP/WebSocket so it can be
    accessed from a browser.  A scrypt-hashed password at
    ``~/.config/terok/serve.password`` (mode 0600) gates the listener.
    On first launch a random password is minted and printed once; pass
    ``--set-password`` to pick a memorable one instead.
    """
    try:
        from textual_serve.server import Server
    except ModuleNotFoundError as exc:
        if exc.name in ("textual_serve", "textual_serve.server"):
            print(
                "terok-web requires the 'textual-serve' package.\n"
                "Install it with: pip install textual-serve",
                file=sys.stderr,
            )
            sys.exit(1)
        raise

    if not hasattr(Server, "_make_app"):
        print(
            "Unsupported textual-serve version: Server._make_app is missing.  "
            "terok pins the upstream seam used to inject basic-auth middleware.",
            file=sys.stderr,
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(
        prog="terok-web",
        description="Serve the Terok TUI as a web application",
    )
    parser.add_argument(
        "--host",
        default=_DEFAULT_HOST,
        help=f"Host to bind to (default: {_DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=_valid_port,
        default=_DEFAULT_PORT,
        help=f"Port to listen on (default: {_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--public-url",
        default=None,
        help="Public URL for browser-facing links and WebSocket connections "
        "(e.g. http://myhost:8566). Required when serving to LAN or "
        "behind a reverse proxy. If omitted, derived from --host and --port.",
    )
    parser.add_argument(
        "--set-password",
        action="store_true",
        help="Prompt for a new Basic-auth password, store its scrypt hash, and exit.",
    )
    args = parser.parse_args()

    path = _password_path()

    if args.set_password:
        _set_password_command(path)
        return

    stored_hash = _bootstrap_password_hash(path)
    server = _build_server("terok-tui", args.host, args.port, args.public_url, stored_hash)

    display_url = args.public_url or f"http://{args.host}:{args.port}/"
    print(
        f"terok-web: serving at {display_url} (user '{_AUTH_USER}', hash in {path})",
        file=sys.stderr,
    )
    server.serve()


if __name__ == "__main__":
    main()
