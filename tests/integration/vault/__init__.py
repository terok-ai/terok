# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Vault integration tests.

Tests here exercise the real vault pipeline: credential DB, phantom tokens,
vault server, and the environment integration that wires them into task
containers.

Marker: ``needs_vault`` — these tests create temporary DBs and
vault servers but do NOT require external network or podman.
"""
