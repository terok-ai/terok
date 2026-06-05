# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Centralised YAML I/O — round-trip mode everywhere.

Re-export of the shared [`terok_util.yaml`][terok_util.yaml] facade under
terok's own ``terok.lib.util.yaml`` import path.

The facade hands out a fresh ``ruamel.yaml`` instance per call: a shared
module-level instance carries composer/parser/scanner state and races
under concurrent ``load`` / ``dump`` from Textual worker threads (symptom
``'MappingEndEvent' object has no attribute 'anchor'``).
"""

from __future__ import annotations

from terok_util.yaml import YAMLError, dump, load  # noqa: F401 — re-exported

__all__ = ["load", "dump", "YAMLError"]
