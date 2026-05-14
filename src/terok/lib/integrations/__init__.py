# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Single-source adapters for the sibling wheels terok depends on.

Every ``terok_executor`` / ``terok_sandbox`` / ``terok_clearance`` import
in the codebase routes through this package.  When a sibling release
renames, splits, or relocates a symbol, only the corresponding adapter
needs to change — the rest of terok keeps reading the same
``terok.lib.integrations.X`` name.

The boundary is enforced by ``.importlinter``'s ``*-boundary``
contracts: outside this package, importing the sibling wheel directly
is a contract violation.  Shield is the exception — it has no adapter
of its own because terok never imports it directly; access is via
sandbox's re-exports.
"""
