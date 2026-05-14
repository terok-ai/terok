# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for the cross-package story tests.

Each fixture spins up a real component (sqlite DB, aiohttp server,
D-Bus daemon) into the test's tmp_path / private session so the
stories can assert against actual wire behaviour without leaking
state across tests or into the developer's environment.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tests.integration.helpers import TerokIntegrationEnv

# ── Mock upstream API ───────────────────────────────────────


@dataclass
class CapturedRequest:
    """One inbound request seen by ``mock_upstream_api``."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class MockUpstreamState:
    """Live handle to the running mock upstream — URL + captured traffic."""

    url: str
    requests: list[CapturedRequest] = field(default_factory=list)

    @property
    def last(self) -> CapturedRequest:
        """Return the most recent captured request (raises if none)."""
        if not self.requests:
            raise AssertionError("mock_upstream_api received no requests")
        return self.requests[-1]


@pytest.fixture
async def mock_upstream_api() -> AsyncIterator[MockUpstreamState]:
    """Run a real aiohttp server on a random localhost port and record traffic.

    The server returns ``{"received": <auth-headers>}`` for every request
    and appends a ``CapturedRequest`` to ``state.requests`` so the test
    body can assert what the upstream *actually* saw — including the
    auth header the vault broker injected.
    """
    from aiohttp import web

    state = MockUpstreamState(url="")

    async def _echo(request: web.Request) -> web.Response:
        body = await request.read()
        state.requests.append(
            CapturedRequest(
                method=request.method,
                path=request.path,
                headers=dict(request.headers),
                body=body,
            )
        )
        return web.json_response(
            {
                "method": request.method,
                "path": request.path,
                "authorization": request.headers.get("Authorization", ""),
                "x_api_key": request.headers.get("X-Api-Key", ""),
            }
        )

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", _echo)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=0)
    await site.start()
    state.url = f"http://{site._server.sockets[0].getsockname()[0]}:{site._server.sockets[0].getsockname()[1]}"  # type: ignore[attr-defined]

    try:
        yield state
    finally:
        await runner.cleanup()


# ── Vault DB + token broker ─────────────────────────────────


@dataclass
class VaultEnv:
    """Wired-up vault: DB path, phantom token, and the broker's base URL."""

    db_path: Path
    phantom_token: str
    real_credential: dict[str, Any]
    broker_url: str
    routes_path: Path


@pytest.fixture
def vault_routes(tmp_path: Path, mock_upstream_api: MockUpstreamState) -> Path:
    """Write a minimal routes.json pointing the ``claude`` provider at the mock.

    Production builds this from the agent roster; tests don't need the
    rest of the roster's metadata, so we write a route dict directly.
    """
    routes_path = tmp_path / "routes.json"
    routes_path.write_text(
        json.dumps(
            {
                "claude": {
                    "upstream": mock_upstream_api.url,
                    "auth_header": "Authorization",
                    "auth_prefix": "Bearer ",
                }
            }
        )
    )
    return routes_path


@pytest.fixture
def populated_vault_db(
    terok_env: TerokIntegrationEnv, tmp_path: Path
) -> tuple[Path, str, dict[str, Any]]:
    """Create a CredentialDB with one API-key credential + one phantom token.

    Returns ``(db_path, phantom_token, real_credential_dict)`` so tests
    can both assert on the real key (looked-up via the broker) and
    confirm the phantom token they got back is what they sent.

    Depends on ``terok_env`` from the parent conftest so the passphrase
    resolution chain (config.yml at ``$XDG_CONFIG_HOME/terok/``) has the
    same ``integration-test-passphrase`` the DB was sealed with — the
    broker reads through that same chain when it opens the DB.
    """
    from terok_sandbox import CredentialDB

    db_path = tmp_path / "vault" / "credentials.db"
    real_credential = {"type": "api_key", "key": "sk-ant-real-secret-001"}

    db = CredentialDB(db_path, passphrase="integration-test-passphrase")
    db.store_credential("default", "claude", real_credential)
    phantom_token = db.create_token("project-x", "task-42", "default", "claude")
    db.close()

    return db_path, phantom_token, real_credential


@pytest.fixture
async def running_token_broker(
    populated_vault_db: tuple[Path, str, dict[str, Any]],
    vault_routes: Path,
    mock_upstream_api: MockUpstreamState,
) -> AsyncIterator[VaultEnv]:
    """Run a real ``terok_sandbox.vault.daemon.token_broker`` on a random port.

    The broker app is built via the same ``_build_app`` factory that
    production uses — same auth header parsing, same upstream-forwarding
    aiohttp client, same audit hook surface — just without the systemd /
    socket-activation packaging that the daemon uses in real deployment.
    """
    from aiohttp import web

    # _build_app is the broker's internal factory; we lean on it directly
    # because there is no public "construct an in-process broker for
    # testing" entrypoint — adding one is a separate refactor.
    from terok_sandbox.vault.daemon.token_broker import _build_app

    db_path, phantom_token, real_credential = populated_vault_db
    app = _build_app(str(db_path), str(vault_routes))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=0)
    await site.start()
    sock = site._server.sockets[0]  # type: ignore[attr-defined]
    broker_url = f"http://{sock.getsockname()[0]}:{sock.getsockname()[1]}"

    try:
        yield VaultEnv(
            db_path=db_path,
            phantom_token=phantom_token,
            real_credential=real_credential,
            broker_url=broker_url,
            routes_path=vault_routes,
        )
    finally:
        await runner.cleanup()


@dataclass
class VaultSocketEnv:
    """Wired-up vault with a UNIX-socket-listening broker for container tests."""

    db_path: Path
    phantom_token: str
    real_credential: dict[str, Any]
    socket_path: Path
    routes_path: Path


@pytest.fixture
async def running_token_broker_socket(
    populated_vault_db: tuple[Path, str, dict[str, Any]],
    vault_routes: Path,
    mock_upstream_api: MockUpstreamState,
    tmp_path: Path,
) -> AsyncIterator[VaultSocketEnv]:
    """Run the token broker on a UNIX domain socket instead of TCP.

    This is the shape the container-launching story uses: a podman
    container bind-mounts the socket and reaches the broker without
    needing ``host.containers.internal`` resolution, which varies by
    rootless network backend (pasta vs slirp4netns) and podman
    version.  Production's "socket" vault transport is also this
    shape — see ``terok_sandbox.config.get_vault_transport``.

    The socket file is ``chmod 0666`` so a container running as a
    different rootless UID (the in-namespace ``testrunner``) can still
    reach the host-side broker through the bind-mount.
    """
    from aiohttp import web
    from terok_sandbox.vault.daemon.token_broker import _build_app

    db_path, phantom_token, real_credential = populated_vault_db
    app = _build_app(str(db_path), str(vault_routes))

    socket_path = tmp_path / "vault.sock"
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, path=str(socket_path))
    await site.start()
    # Loosen perms so a container peer with a different mapped UID can
    # still connect through the bind-mount — the file is in a per-test
    # tmp dir, so the looser mode never escapes the test sandbox.
    socket_path.chmod(0o666)

    try:
        yield VaultSocketEnv(
            db_path=db_path,
            phantom_token=phantom_token,
            real_credential=real_credential,
            socket_path=socket_path,
            routes_path=vault_routes,
        )
    finally:
        await runner.cleanup()


# ── D-Bus mock notification daemon ──────────────────────────


# python-dbusmock's ``dbusmock.pytest_fixtures`` plugin is activated at
# the rootdir conftest (pytest 8+ rejects ``pytest_plugins`` in
# non-rootdir conftests with a hard collection error).  We re-check
# availability here so we can provide a fallback ``dbusmock_session``
# that skips cleanly when the plugin couldn't load — without it, tests
# requesting the fixture hit "fixture not found" instead of skip.
try:
    import dbusmock as _dbusmock  # noqa: F401

    _DBUS_AVAILABLE = True
except ImportError:
    _DBUS_AVAILABLE = False

    @pytest.fixture(scope="session")
    def dbusmock_session() -> Iterator[Any]:
        """Fallback skip when python-dbusmock isn't installed."""
        pytest.skip("python-dbusmock not installed (or dbus-python failed to build)")
        yield None  # pragma: no cover — pytest.skip raises


@pytest.fixture(scope="session")
def notification_daemon(dbusmock_session: Any) -> Iterator[Any]:
    """Spawn a mock ``org.freedesktop.Notifications`` daemon on the test bus.

    Yields the [`SpawnedMock`][dbusmock.SpawnedMock] instance — gives the
    test direct access to ``.obj`` (a dbus-python proxy already bound to
    the mocked object) for control calls like ``GetMethodCalls`` and
    ``EmitSignal``.  ``dbusmock_session`` (a ``PrivateDBus`` in
    python-dbusmock >= 0.36) doesn't expose a ``get_object`` API of its
    own, so tests should reach the mock through ``.obj`` rather than
    the bus.

    Uses python-dbusmock's built-in ``notification_daemon`` template,
    which implements the full freedesktop Notifications interface and
    records every method invocation so tests can assert what landed
    and emit signals back to the client.
    """
    if not _DBUS_AVAILABLE:  # dbusmock_session already skipped, but be explicit
        pytest.skip("python-dbusmock not installed (or dbus-python failed to build)")
    from dbusmock import SpawnedMock

    mock = SpawnedMock.spawn_with_template("notification_daemon")
    try:
        yield mock
    finally:
        mock.process.terminate()
        mock.process.wait()
