# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Terminal display helpers — ANSI colors and hanging-indent line wrapping.

Core color functions (``supports_color``, ``color``, ``yellow``, ``blue``,
``green``, ``red``) are defined in ``terok.lib.util.ansi`` so that
service-layer modules can use them without a cross-layer dependency.
This module re-exports them, adds higher-level color helpers, and provides
``wrap_with_hanging_indent`` for prefix-aligned TUI label wrapping.
"""

import textwrap

from rich.cells import cell_len

from terok.lib.util.ansi import (  # noqa: F401  -- re-exports
    blue,
    bold,
    color,
    green,
    hyperlink,
    red,
    supports_color,
    yellow,
)


def yes_no(value: bool, enabled: bool) -> str:
    """Return green ``"yes"`` or red ``"no"`` based on *value* when *enabled*."""
    return color("yes" if value else "no", "32" if value else "31", enabled)


def violet(text: str, enabled: bool) -> str:
    """Return *text* in violet (ANSI 35) when *enabled*."""
    return color(text, "35", enabled)


def gray(text: str, enabled: bool) -> str:
    """Return *text* in gray (ANSI 90) when *enabled*."""
    return color(text, "90", enabled)


def wrap_with_hanging_indent(prefix: str, body: str, suffix: str, width: int) -> str:
    """Render ``prefix + body + suffix`` with continuation lines hanging-indented.

    *body* wraps with [`wrap`][textwrap.wrap] (so dashes are break points and
    overlong segments fold by character).  Continuation lines are prepended
    with spaces aligning to the *cell* width of *prefix*, so they sit
    underneath the start of *body* even when *prefix* contains emoji.

    *suffix* attaches to the last body line if it fits; otherwise it lands
    on its own continuation line with the leading space stripped.

    *width* ≤ 0, or a *prefix* already wider than *width*, disables wrapping
    and returns the inputs concatenated verbatim.
    """
    full = f"{prefix}{body}{suffix}"
    if width <= 0 or cell_len(full) <= width:
        return full

    indent = cell_len(prefix)
    avail = width - indent
    if avail <= 0:
        return full

    # textwrap counts in chars; it lines up with cell width as long as *body*
    # is ASCII (terok task names are).
    lines = textwrap.wrap(body, width=avail) or [""]
    if suffix:
        if cell_len(lines[-1]) + cell_len(suffix) <= avail:
            lines[-1] += suffix
        else:
            lines.append(suffix.lstrip())

    pad = " " * indent
    return prefix + ("\n" + pad).join(lines)
