# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Cross-package "story" integration tests.

A small, curated set of end-to-end tests that exercise the wire-level
contracts between terok and its sibling packages — every IPC the
high-level features actually traverse: vault sockets, hub event sockets,
varlink, the D-Bus session bus.

Each test is written as a narrative: one user-facing feature, one
file, real components wherever feasible, mocks only at the outer
boundary (mock upstream API server, mock D-Bus notification daemon).

These tests are deliberately *in-process* for now — they spin up real
subprocesses for the sockets and brokers, but do not launch a podman
container.  Container-launching variants are tracked as follow-ups in
the per-test docstrings; the in-process form already verifies all the
wire protocols at full fidelity.
"""
