# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Rootdir conftest — only place pytest accepts ``pytest_plugins``.

Pytest 8+ rejects ``pytest_plugins`` in non-rootdir conftests with a
hard collection error.  We need ``dbusmock.pytest_fixtures`` for the
verdict-loop story in ``tests/integration/stories/test_verdict_loop.py``
— putting the declaration here is the only place pytest will accept it.

The import is guarded so collection still works on hosts where
``python-dbusmock`` (or its ``dbus-python`` transitive) isn't installed
— terok-clearance #114 documents the system packages needed to build
``dbus-python`` from source.  Stories that need the D-Bus fixtures
explicitly request ``dbusmock_session`` / ``notification_daemon``; a
fallback fixture in ``tests/integration/stories/conftest.py`` makes
them skip cleanly when the plugin couldn't load.
"""

try:
    import dbusmock  # noqa: F401

    pytest_plugins = ("dbusmock.pytest_fixtures",)
    DBUS_FIXTURES_AVAILABLE = True
except ImportError:
    DBUS_FIXTURES_AVAILABLE = False
