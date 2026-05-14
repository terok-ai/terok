# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Story: a phantom token sent by an agent reaches the upstream as the *real* key.

The container's view: it has an environment variable like
``ANTHROPIC_API_KEY=terok-p-<32hex>`` and treats it as a normal API
key.  The host's view: the vault token broker (``terok_sandbox.vault.
token_broker``) intercepts the agent's HTTP traffic, looks the phantom
up in an encrypted SQLite DB, swaps it for the real OAuth/API key, and
forwards to the real upstream.  The container itself never sees the
real credential.

End-to-end, this story exercises:

- :class:`terok_sandbox.CredentialDB` (sqlite, AES-encrypted at rest)
- The broker's phantom→real swap in ``_handle_request``
- The route table loaded from ``routes.json`` (same format the agent
  roster generates in production)
- An aiohttp upstream that records what authorization header it sees

In-process tests at the bottom of the file run the broker in the
same Python process and skip the container layer; the container-
launching test (``test_phantom_swap_through_container_socket``)
adds the missing transport hop: a real podman container with the
broker's UNIX socket bind-mounted, exec'ing ``curl --unix-socket``
to drive the request from a separate process / namespace.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid

import pytest

from tests.integration.helpers import PODMAN_CONTAINER_PREFIX, PODMAN_TEST_IMAGE
from tests.integration.stories.conftest import (
    MockUpstreamState,
    VaultEnv,
    VaultSocketEnv,
)

# All stories in this file talk to a real DB + broker.
pytestmark = pytest.mark.needs_vault


async def test_api_key_phantom_swapped_to_real_at_upstream(
    running_token_broker: VaultEnv,
    mock_upstream_api: MockUpstreamState,
) -> None:
    """Phantom token in → real API key out at the upstream.

    This is the core security guarantee of the vault: the container
    presents a token that is *useless* on its own (it only works
    through the broker), and the broker substitutes the real key
    before the request leaves the host.
    """
    import aiohttp

    phantom = running_token_broker.phantom_token
    real_key = running_token_broker.real_credential["key"]

    # 1. Send a request to the broker as the container would
    #    (Authorization: Bearer <phantom>).
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{running_token_broker.broker_url}/v1/messages",
            headers={"Authorization": f"Bearer {phantom}"},
        ) as response:
            assert response.status == 200, (
                f"broker rejected phantom-bearing request: status={response.status}, "
                f"body={await response.text()!r}"
            )

    # 2. The upstream should have seen exactly one request, with the
    #    *real* key in the Authorization header.
    assert len(mock_upstream_api.requests) == 1
    captured = mock_upstream_api.last
    assert captured.path == "/v1/messages"
    assert captured.headers.get("Authorization") == f"Bearer {real_key}"

    # 3. And the phantom must NOT have leaked through.
    raw_headers = " ".join(captured.headers.values())
    assert phantom not in raw_headers, (
        "phantom token leaked through to the upstream — broker did not strip it"
    )


async def test_unknown_phantom_is_rejected(
    running_token_broker: VaultEnv,
    mock_upstream_api: MockUpstreamState,
) -> None:
    """A phantom that isn't in the DB short-circuits to 401 with no upstream hit."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{running_token_broker.broker_url}/v1/messages",
            headers={"Authorization": "Bearer terok-p-ffffffffffffffffffffffffffffffff"},
        ) as response:
            assert response.status == 401

    # The upstream MUST NOT have been called — the broker rejects bad
    # phantoms before forwarding.
    assert mock_upstream_api.requests == [], (
        "broker forwarded a request despite phantom-token rejection"
    )


async def test_revoked_phantom_is_rejected(
    running_token_broker: VaultEnv,
    mock_upstream_api: MockUpstreamState,
) -> None:
    """After ``CredentialDB.revoke_tokens``, the phantom no longer works.

    The container's environment still contains the old token; the
    revocation is enforced at the broker, not at issuance.
    """
    import aiohttp
    from terok_sandbox import CredentialDB

    db = CredentialDB(running_token_broker.db_path, passphrase="integration-test-passphrase")
    revoked = db.revoke_tokens("project-x", "task-42")
    db.close()
    assert revoked >= 1, "expected at least the seeded phantom to be revoked"

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{running_token_broker.broker_url}/v1/messages",
            headers={"Authorization": f"Bearer {running_token_broker.phantom_token}"},
        ) as response:
            assert response.status == 401

    assert mock_upstream_api.requests == [], (
        "revoked phantom reached the upstream — broker did not honour revocation"
    )


# ── Container-launching variant ──────────────────────────────


@pytest.mark.needs_podman
def test_phantom_swap_through_container_socket(
    running_token_broker_socket: VaultSocketEnv,
    mock_upstream_api: MockUpstreamState,
    _pull_image: None,
) -> None:
    """Same swap, but driven by ``curl`` *inside a real podman container*.

    The in-process tests above prove the broker swaps phantom for real
    *given a same-process HTTP client*.  This test adds the transport
    hop the agent uses in production: the container has the broker's
    UNIX socket bind-mounted and reaches it via
    ``curl --unix-socket``.  No more shared event loop, no more
    same-namespace shortcut.

    UNIX socket (rather than ``host.containers.internal``) keeps the
    test reliable across rootless backends — pasta and slirp4netns
    resolve the host gateway differently, and that's not what this
    story is testing.  Production's "socket" vault transport is
    exactly this shape.

    Marker: ``needs_podman`` — skipped where podman is absent (CI,
    laptops without a runtime); the matrix runners exercise it.
    """
    if not shutil.which("podman"):
        pytest.skip("podman not on PATH")

    real_key = running_token_broker_socket.real_credential["key"]
    phantom = running_token_broker_socket.phantom_token
    host_socket = running_token_broker_socket.socket_path

    name = f"{PODMAN_CONTAINER_PREFIX}-phantom-{uuid.uuid4().hex[:8]}"
    try:
        # ``label=disable`` mirrors what the matrix's outer-container run
        # does, so SELinux relabeling doesn't trip nested-rootless podman
        # bind-mounts (the prior ``:z`` flag did the wrong thing — it
        # tried to relabel a file the outer container had marked
        # unlabeled, and podman exited 125).  On non-SELinux hosts it is
        # a no-op.
        result = subprocess.run(
            [
                "podman",
                "run",
                "-d",
                "--security-opt",
                "label=disable",
                "--name",
                name,
                "-v",
                f"{host_socket}:/vault.sock",
                PODMAN_TEST_IMAGE,
                "sleep",
                "60",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"podman run failed (exit {result.returncode}):\n"
            f"  stdout: {result.stdout!r}\n"
            f"  stderr: {result.stderr!r}"
        )

        # 1. Container's curl drives the request through the bind-mounted
        #    socket.  The phantom token is what the agent in production
        #    would send; the broker sees it and substitutes the real key
        #    before forwarding to the upstream.
        exec_result = subprocess.run(
            [
                "podman",
                "exec",
                name,
                "curl",
                "-sS",
                "--fail",
                "--unix-socket",
                "/vault.sock",
                "-H",
                f"Authorization: Bearer {phantom}",
                "http://localhost/v1/messages",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert exec_result.returncode == 0, (
            f"curl from inside the container failed (exit {exec_result.returncode}):\n"
            f"  stdout: {exec_result.stdout!r}\n"
            f"  stderr: {exec_result.stderr!r}"
        )

        # 2. The mock upstream got exactly one request, with the *real* key.
        assert len(mock_upstream_api.requests) == 1
        captured = mock_upstream_api.last
        assert captured.path == "/v1/messages"
        assert captured.headers.get("Authorization") == f"Bearer {real_key}"

        # 3. The phantom never left the host — neither in any header the
        #    upstream saw, nor anywhere in the container's environment.
        raw_headers = " ".join(captured.headers.values())
        assert phantom not in raw_headers
        env_result = subprocess.run(
            ["podman", "exec", name, "env"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert real_key not in env_result.stdout, (
            "real API key leaked into container environment — vault transport "
            "should have kept it host-side"
        )
    finally:
        subprocess.run(["podman", "rm", "-f", name], capture_output=True, timeout=30)
