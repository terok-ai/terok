# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for module import smoke tests."""

from __future__ import annotations

import importlib


def assert_module_callable(module_name: str, attribute: str = "main") -> None:
    """Assert that *module_name* exposes a callable *attribute*."""
    module = importlib.import_module(module_name)
    assert callable(getattr(module, attribute, None))
