# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Story: the broker resolves a provider's storage redirect and relays its headers.

Some provider endpoints (file downloads, log/artifact fetches) answer an
idempotent GET with a 3xx to a self-authenticating URL on a *different* host.
The container's HTTP client is pinned to the vault broker's UNIX socket, so it
can neither reach that host nor follow the hop itself.  The broker therefore
follows the redirect on the container's behalf — dropping the injected provider
credential on the cross-origin hop — and streams the final response back with
the provider's real headers intact.

This is the runtime contract every ``openai-chat`` agent (Pi in particular)
leans on: route a call through the socket and get the real response, headers and
all, without ever holding the real key.  It exercises the two broker fixes that
ship together this release — redirect following and response-header relay —
through the real transport hop a socket-pinned agent uses.

The container drives it with ``curl --unix-socket`` and *no* ``-L``: the whole
point is that the broker, not curl, follows the redirect, so the assertions are
on what a socket-pinned agent actually observes.  In-process broker fixtures
already cover the swap mechanics (see
``tests/integration/stories/test_phantom_token_roundtrip.py``); this adds the
container + redirect dimension on top.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tests.integration.helpers import (
    PODMAN_CONTAINER_PREFIX,
    PODMAN_TEST_IMAGE,
    podman_subprocess_env,
)
from tests.integration.stories.conftest import CapturedRequest

# This story talks to a real DB + broker.
pytestmark = pytest.mark.needs_vault


# ── Story constants ─────────────────────────────────────────────────────────

# An Anthropic Files-style endpoint that 302s to object storage.
_PROVIDER_PATH = "/v1/files/file-abc123/content"
# Where the provider redirects.  Relative, so it resolves back onto the same
# mock host at a distinct path; the broker drops the credential on the follow
# regardless of host, so a same-host target proves the cross-origin rule fine.
_STORAGE_PATH = "/_storage/blob-abc123"
_FINAL_BLOB = b"terok-relay-payload-7f3a9c"

# Headers the storage hop returns that MUST reach the container verbatim
# (lowercased here so the case-insensitive search in the test is exact).
_RELAYED_ETAG = '"blob-v1"'
_RELAYED_TRACE_HEADER = "x-provider-trace"
_RELAYED_TRACE_VALUE = "trace-xyz-42"
# A ``Location`` on the *final* 200 — a deliberate probe: the broker withholds
# ``Location`` on every response, so it must never surface to the container.
_WITHHELD_LOCATION = "https://example.invalid/never-relayed"
# A header on the *intermediate* 302 — proves the broker relays the final
# response it followed to, not the redirect it consumed.
_INTERMEDIATE_ONLY_HEADER = "x-intermediate-only"


def _capture(request: Any, body: bytes) -> CapturedRequest:
    """Snapshot an inbound request so the test can assert what a hop saw."""
    return CapturedRequest(
        method=request.method,
        path=request.path,
        headers=dict(request.headers),
        body=body,
    )


# ── Fixtures: a redirecting upstream + a broker routed at it ─────────────────


@dataclass
class RedirectUpstreamState:
    """Live handle to the two-route mock provider — URL + captured traffic."""

    url: str
    requests: list[CapturedRequest] = field(default_factory=list)


@pytest.fixture
async def redirecting_upstream() -> AsyncIterator[RedirectUpstreamState]:
    """A provider whose file endpoint 302s to a storage path that returns the blob.

    Records every inbound request so the test can assert what each hop saw —
    crucially that the real credential reached the provider API but *not* the
    storage host the broker followed to.
    """
    from aiohttp import web

    state = RedirectUpstreamState(url="")

    async def _provider(request: web.Request) -> web.Response:
        state.requests.append(_capture(request, await request.read()))
        # Idempotent GET → the broker resolves this hop itself.
        return web.Response(
            status=302,
            headers={"Location": _STORAGE_PATH, _INTERMEDIATE_ONLY_HEADER: "nope"},
        )

    async def _storage(request: web.Request) -> web.Response:
        state.requests.append(_capture(request, await request.read()))
        return web.Response(
            body=_FINAL_BLOB,
            headers={
                "ETag": _RELAYED_ETAG,
                _RELAYED_TRACE_HEADER: _RELAYED_TRACE_VALUE,
                "Location": _WITHHELD_LOCATION,
            },
        )

    app = web.Application()
    app.router.add_get(_PROVIDER_PATH, _provider)
    app.router.add_get(_STORAGE_PATH, _storage)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=0)
    await site.start()
    sock = site._server.sockets[0]  # type: ignore[attr-defined]
    state.url = f"http://{sock.getsockname()[0]}:{sock.getsockname()[1]}"
    try:
        yield state
    finally:
        await runner.cleanup()


@dataclass
class RelayEnv:
    """Wired-up broker: the bind-mountable socket plus the tokens to drive it."""

    socket_path: Path
    phantom_token: str
    real_key: str


@pytest.fixture
async def relay_broker_socket(
    populated_vault_db: tuple[Path, str, dict[str, Any]],
    redirecting_upstream: RedirectUpstreamState,
    tmp_path: Path,
) -> AsyncIterator[RelayEnv]:
    """Run the real token broker on a UNIX socket, routed at the redirecting upstream.

    The same ``_build_app`` factory production uses — only the route table's
    upstream points at this story's two-route mock instead of a live provider.
    The socket is ``chmod 0666`` so a container peer with a different rootless
    UID can reach it through the bind-mount (the file lives in a per-test tmp
    dir, so the looser mode never escapes the sandbox).
    """
    from aiohttp import web
    from terok_sandbox.vault.daemon.token_broker import _build_app

    db_path, phantom_token, real_credential = populated_vault_db
    routes_path = tmp_path / "routes.json"
    routes_path.write_text(
        json.dumps(
            {
                "claude": {
                    "upstream": redirecting_upstream.url,
                    "auth_header": "Authorization",
                    "auth_prefix": "Bearer ",
                }
            }
        )
    )
    app = _build_app(str(db_path), str(routes_path))

    socket_path = tmp_path / "vault.sock"
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, path=str(socket_path))
    await site.start()
    socket_path.chmod(0o666)
    try:
        yield RelayEnv(
            socket_path=socket_path,
            phantom_token=phantom_token,
            real_key=real_credential["key"],
        )
    finally:
        await runner.cleanup()


# ── The story ────────────────────────────────────────────────────────────────


def _split_response(raw: str) -> tuple[str, str]:
    """Split a ``curl -i`` dump into (header-block, body) across CRLF or LF endings."""
    for sep in ("\r\n\r\n", "\n\n"):
        head, found, body = raw.partition(sep)
        if found:
            return head, body
    return raw, ""


@pytest.mark.needs_podman
async def test_provider_redirect_resolved_and_headers_relayed_through_container(
    relay_broker_socket: RelayEnv,
    redirecting_upstream: RedirectUpstreamState,
    _pull_image: None,
) -> None:
    """A socket-pinned container GET transparently yields the redirected blob.

    Container view: one ``curl --unix-socket`` GET to the provider's file
    endpoint (no ``-L``).  It must return ``200`` with the storage blob and the
    provider's relayed headers — the broker followed the 302 the container's
    socket-pinned client could not.

    Host view: the provider hop saw the *real* key (phantom swapped in); the
    storage hop the broker followed to saw *no* provider credential (dropped on
    the cross-origin follow); the phantom reached neither hop.

    The broker (running in this test's event loop via the fixture) keeps
    servicing while the blocking ``podman exec curl`` is offloaded with
    ``asyncio.to_thread`` — a sync test would park the loop and the in-container
    curl would hang waiting on a response the broker never gets to send.
    """
    if not shutil.which("podman"):
        pytest.skip("podman not on PATH")

    phantom = relay_broker_socket.phantom_token
    real_key = relay_broker_socket.real_key
    host_socket = relay_broker_socket.socket_path
    env = podman_subprocess_env()

    name = f"{PODMAN_CONTAINER_PREFIX}-relay-{uuid.uuid4().hex[:8]}"
    qualified_image = (
        PODMAN_TEST_IMAGE if "/" in PODMAN_TEST_IMAGE else f"localhost/{PODMAN_TEST_IMAGE}"
    )
    try:
        run = await asyncio.to_thread(
            subprocess.run,
            [
                "podman",
                "run",
                "-d",
                "--rm",
                "--pull",
                "never",
                "--security-opt",
                "label=disable",
                "--name",
                name,
                "-v",
                f"{host_socket}:/vault.sock",
                qualified_image,
                "sleep",
                "60",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        assert run.returncode == 0, (
            f"podman run failed (exit {run.returncode}):\n"
            f"  stdout: {run.stdout!r}\n  stderr: {run.stderr!r}"
        )

        # ``-i`` so the agent's observed response *headers* land in stdout; no
        # ``-L`` — the broker, not curl, is what must follow the redirect.
        curl = await asyncio.to_thread(
            subprocess.run,
            [
                "podman",
                "exec",
                name,
                "curl",
                "-sS",
                "-i",
                "--unix-socket",
                "/vault.sock",
                "-H",
                f"Authorization: Bearer {phantom}",
                f"http://localhost{_PROVIDER_PATH}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        assert curl.returncode == 0, (
            f"in-container curl failed (exit {curl.returncode}):\n"
            f"  stdout: {curl.stdout!r}\n  stderr: {curl.stderr!r}"
        )

        header_block, body = _split_response(curl.stdout)
        status_line = header_block.splitlines()[0]
        headers_lc = header_block.lower()

        # 1. The container saw the *final* 200 + blob — never the 302 it can't follow.
        assert status_line.split()[1] == "200", (
            f"expected 200 (broker should resolve the redirect), got {status_line!r}"
        )
        assert body.strip() == _FINAL_BLOB.decode(), (
            f"container did not receive the storage blob; body={body!r}"
        )

        # 2. Header relay (#380): provider headers reach the container verbatim...
        assert f"{_RELAYED_TRACE_HEADER}: {_RELAYED_TRACE_VALUE}" in headers_lc
        assert f"etag: {_RELAYED_ETAG}" in headers_lc
        # ...but framing-sensitive / redirect headers are withheld.
        assert "location:" not in headers_lc, "broker leaked the withheld Location header"
        assert _INTERMEDIATE_ONLY_HEADER not in headers_lc, (
            "broker relayed the intermediate 302's headers, not the final response's"
        )

        # 3. Two host-side hops: the provider API, then the storage URL.
        by_path = {r.path: r for r in redirecting_upstream.requests}
        assert set(by_path) == {_PROVIDER_PATH, _STORAGE_PATH}, (
            f"unexpected upstream hops: {sorted(by_path)}"
        )
        provider_hop, storage_hop = by_path[_PROVIDER_PATH], by_path[_STORAGE_PATH]

        # The provider hop carried the *real* key (phantom swapped in on the way out).
        assert provider_hop.headers.get("Authorization") == f"Bearer {real_key}"
        # The storage hop the broker followed to carried *no* provider credential.
        assert not any(name.lower() == "authorization" for name in storage_hop.headers), (
            "real credential leaked onto the cross-origin redirect follow"
        )
        assert real_key not in " ".join(storage_hop.headers.values())

        # The phantom never reached either hop — it dies at the broker.
        for hop in (provider_hop, storage_hop):
            assert phantom not in " ".join(hop.headers.values()), (
                f"phantom token leaked to the upstream at {hop.path}"
            )
    finally:
        await asyncio.to_thread(
            subprocess.run,
            ["podman", "rm", "-f", name],
            capture_output=True,
            timeout=30,
            env=env,
        )
