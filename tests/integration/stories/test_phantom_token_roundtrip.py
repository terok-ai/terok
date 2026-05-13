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

The container layer is *not* launched here — that would require real
podman + the host-side networking wiring (host.containers.internal,
vault transport mode) which is the bit I cannot validate without the
dev hardware.  Container-launching variant is tracked as a follow-up
(see this directory's ``__init__.py``).
"""

from __future__ import annotations

import pytest

from tests.integration.stories.conftest import MockUpstreamState, VaultEnv

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
