# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Credential proxy integration tests.

Tests here exercise the real proxy pipeline: credential DB, phantom tokens,
proxy server, and the environment integration that wires them into task
containers.

Marker: ``needs_credential_proxy`` — these tests create temporary DBs and
proxy servers but do NOT require external network or podman.
"""
