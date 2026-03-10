# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared test constants for IP addresses, URLs, and network values.

Centralises magic literals so they can be found and updated in one place.
"""

# ── IP addresses ──────────────────────────────────────────

LOCALHOST = "127.0.0.1"
"""Loopback address used for bind/connect in tests."""

TEST_IP = "198.51.100.42"
"""RFC 5737 TEST-NET-2 address for mock returns."""

TEST_IP_RFC5737 = "203.0.113.42"
"""RFC 5737 TEST-NET-3 address for firewall allow/deny tests."""

SLIRP_GATEWAY = "10.0.2.2"
"""Default slirp4netns gateway address."""

# ── Host alias entries ────────────────────────────────────

HOST_ALIAS_LOOPBACK = f"host.containers.internal:{LOCALHOST}"
"""Podman --add-host value for pasta/rootful mode."""

HOST_ALIAS_SLIRP = f"host.containers.internal:{SLIRP_GATEWAY}"
"""Podman --add-host value for slirp4netns mode."""

# ── Ports ─────────────────────────────────────────────────

GATE_PORT = 9418
"""Default gate server port."""

FAKE_PEER_PORT = 12345
"""Arbitrary port for fake client_address tuples."""

LOCALHOST_PEER = (LOCALHOST, FAKE_PEER_PORT)
"""Fake peer address for HTTP handler tests."""

# ── URLs ──────────────────────────────────────────────────

TEST_EGRESS_URL = "http://example.com"
"""URL used in egress filtering tests."""

EGRESS_DOMAIN = "example.com"
"""Domain name used in egress filtering tests."""


def localhost_url(port: int) -> str:
    """Build an ``http://127.0.0.1:{port}/`` URL."""
    return f"http://{LOCALHOST}:{port}/"
