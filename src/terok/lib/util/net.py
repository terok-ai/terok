# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Small URL helpers — host formatting, authority normalisation."""

from __future__ import annotations

import os
from urllib.parse import urlsplit


def git_remote_host(url: str) -> str | None:
    """The host a git remote points at, or ``None`` when it names a local path.

    Covers both forms git accepts: scheme URLs (``https://``, ``ssh://``),
    and the scp-like ``git@github.com:org/repo`` shorthand — which carries no
    scheme, so ``urlsplit`` reads it hostless and would silently drop the
    host.  The result is lower-cased, since every caller compares it.
    """
    if "://" in url:
        return urlsplit(url).hostname or None
    head, sep, _path = url.partition(":")
    if not sep or "/" in head:
        return None  # no colon, or a path-like prefix — a local repo, not a remote
    return head.rpartition("@")[2].lower() or None


def url_host(host: str) -> str:
    """*host* formatted for an HTTP URL authority.

    IPv6 literals are wrapped in square brackets so ``::1`` becomes
    ``[::1]``; IPv4 addresses and hostnames pass through unchanged.
    Already-bracketed input is left alone to avoid double-wrapping.
    """
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
"""Spellings of this machine's loopback: bound or advertised here, a web endpoint never leaves it."""

ALLOW_CLEARTEXT_ENV = "TEROK_ALLOW_CLEARTEXT"
"""Set to ``1`` to serve plain HTTP beyond loopback on a trusted LAN."""


def is_loopback_host(host: str) -> bool:
    """Whether *host* names this machine's loopback interface."""
    return host.strip().lower() in LOOPBACK_HOSTS


def require_cleartext_opt_in(host: str, *, what: str) -> None:
    """Refuse *what* beyond loopback unless ``TEROK_ALLOW_CLEARTEXT`` says the network is trusted.

    Plain HTTP beyond loopback carries the web TUI's Basic-auth password and
    the task tokens readable to anyone on the path.  The supported shape for
    anything beyond a trusted LAN is a TLS reverse proxy in front of a
    loopback listener; the opt-in states that this network is trusted.
    """
    if is_loopback_host(host):
        return
    if os.environ.get(ALLOW_CLEARTEXT_ENV, "").strip().lower() in ("1", "true", "yes"):
        return
    raise SystemExit(
        f"{what} would travel as plain HTTP beyond loopback ({host}).  Keep the listener on "
        f"127.0.0.1 behind a TLS reverse proxy, or set {ALLOW_CLEARTEXT_ENV}=1 on a trusted LAN."
    )
