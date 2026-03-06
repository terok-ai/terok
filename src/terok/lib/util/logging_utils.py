# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Utility functions for logging."""


def _log_debug(message: str) -> None:
    """Append a simple debug line to the terok library log.

    This is intentionally very small and best-effort so it never interferes
    with normal CLI or TUI behavior. It can be used to compare behavior
    between different frontends (e.g. CLI vs TUI) when calling the shared
    helpers in this module.

    Writes timestamped lines to ``state_root()/terok.log``. Fully
    exception-safe: any IO error is silently ignored so this function never
    raises or affects callers.
    """
    try:
        import time

        from ..core.paths import state_root

        log_path = state_root() / "terok.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass
