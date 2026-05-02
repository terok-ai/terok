# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Centralised YAML I/O — round-trip mode everywhere.

**Facade** over ``ruamel.yaml``'s ceremony-heavy ``YAML()`` class: callers get
a minimal ``load`` / ``dump`` / ``YAMLError`` surface instead of instance
creation, typ selection, stream management, and config attributes.

**Adapter** from ruamel.yaml's stream-oriented API to the string-based
convention used throughout the codebase (``path.read_text()`` → ``load(text)``
→ modify → ``dump(data)`` → ``path.write_text(text)``).

``CommentedMap`` is a ``dict`` subclass — ``isinstance(x, dict)``, ``.get()``,
``x["key"]``, ``.setdefault()`` all work transparently.  Pydantic v2
``model_validate()`` accepts dict subclasses, so read-side validation is
unchanged.
"""

from __future__ import annotations

from io import StringIO
from typing import Any

from ruamel.yaml import YAML, YAMLError  # noqa: F401 — re-exported

__all__ = ["load", "dump", "YAMLError"]


def _rt() -> YAML:
    # A fresh instance per call: ``YAML`` carries composer/parser/scanner state
    # on the object, so a shared module-level instance races under concurrent
    # ``load`` / ``dump`` from Textual worker threads — symptom is
    # ``'MappingEndEvent' object has no attribute 'anchor'``.
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    return yaml


def load(text: str) -> Any:
    """Round-trip load from a YAML string, preserving comments and order."""
    return _rt().load(text)


def dump(data: Any, *, default_flow_style: bool = False) -> str:
    """Round-trip dump to a YAML string, preserving comments and order.

    Key order is preserved (insertion order for new dicts, original order for
    round-tripped data).  ``sort_keys`` is always ``False`` — the caller never
    needs to pass it.
    """
    emitter = _rt()
    if default_flow_style:
        emitter.default_flow_style = True
    buf = StringIO()
    emitter.dump(data, buf)
    return buf.getvalue()
