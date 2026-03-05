# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Emoji display-width utilities for consistent terminal alignment.

Problem
-------
Terminal emulators and Unicode width libraries disagree on how wide certain
emojis are.  Emojis come in two categories:

* **Natively wide** (``East_Asian_Width=W``, ``Emoji_Presentation=Yes``):
  Characters like \U0001f680 \U0001f7e2 \u2705 \u274c that are *always* 2 cells wide.  Both Rich's
  ``cell_len`` and virtually every terminal agree on 2 cells.  These are safe.

* **VS16-dependent** (``East_Asian_Width=N`` or ``A``, plus U+FE0F):
  Characters like \u25b6\ufe0f \u23f8\ufe0f \u2328\ufe0f \U0001f5d1\ufe0f that are 1-cell text symbols by default and
  only become emoji when followed by Variation Selector-16 (U+FE0F).  Rich's
  ``cell_len`` reports 2 cells (per the Unicode spec), but most terminals
  render them as 1 cell.  This 1-cell discrepancy per emoji breaks
  Rich/Textual's internal layout width accounting, causing misaligned columns
  and shifted panel edges that **cannot be fixed by padding alone**.

Solution
--------
All emojis used by terok must be natively wide (``East_Asian_Width=W``).
This ensures Rich, Textual, and the terminal all agree on a 2-cell width.
The ``draw_emoji`` helper pads any sub-2-cell characters with spaces for
alignment, and the test suite includes guard tests that verify every emoji
in the project is natively 2 cells wide.

Emoji definitions are centralised in ``terok.lib.containers.task_display``
(``STATUS_DISPLAY``, ``MODE_DISPLAY``, ``WEB_BACKEND_EMOJI``,
``SECURITY_CLASS_DISPLAY``, ``GPU_DISPLAY``) and
``terok.lib.containers.work_status`` (``WORK_STATUS_DISPLAY``).

**Never use emoji literals directly in code.**  Always define them in a
central dict with at least a ``label`` field, and render via ``draw_emoji``.
This ensures ``--no-emoji`` mode can substitute ``[label]`` for every emoji.

How to check a candidate emoji::

    python3 -c "
    import unicodedata
    e = '\U0001f7e2'  # paste your candidate here
    print(f'eaw={unicodedata.east_asian_width(e)}')  # must be 'W'
    print(f'vs16={chr(0xFE0F) in e}')                # must be False
    "

Future developments to watch
-----------------------------
The terminal ecosystem may eventually converge on correct VS16 handling,
which would lift this restriction:

* **Kitty text sizing protocol** \u2014 Kitty 0.40+ lets clients tell the terminal
  exactly how wide each piece of text should be via ``ESC ] 66``.  If adopted
  by other terminals, apps could use VS16 emojis and override the width.

* **Mode 2027 (grapheme cluster width)** \u2014 An opt-in escape sequence
  (``CSI ? 2027 h``) that tells the terminal to handle grapheme clusters
  properly.  Supported by Kitty and Ghostty; limited adoption elsewhere.

* **Terminal convergence** \u2014 As of 2026, only Kitty and Ghostty render VS16
  emojis as 2 cells.  If major terminals (iTerm2, GNOME Terminal, Windows
  Terminal, Alacritty) follow suit, VS16 emojis will become safe to use.

* **Rich/Textual configuration** \u2014 Neither library currently offers a way to
  override ``cell_len`` for VS16 sequences.  A future Rich release might add
  terminal-capability detection or user-configurable width tables.

References:
  - Unicode UAX #11 (East Asian Width): https://unicode.org/reports/tr11/
  - Unicode UTS #51 (Emoji): https://unicode.org/reports/tr51/
  - Rich FAQ on emoji width: https://github.com/textualize/rich/blob/master/FAQ.md
  - Kitty text sizing protocol: https://sw.kovidgoyal.net/kitty/text-sizing-protocol/
  - Terminal emoji width survey: https://www.jeffquast.com/post/ucs-detect-test-results/
"""

from rich.cells import cell_len

_emoji_enabled: bool = True


def set_emoji_enabled(enabled: bool) -> None:
    """Set global emoji rendering mode.

    When *enabled* is ``False``, ``draw_emoji`` returns ``[label]`` instead
    of the emoji character (when a *label* is supplied).
    """
    global _emoji_enabled  # noqa: PLW0603
    _emoji_enabled = enabled


def is_emoji_enabled() -> bool:
    """Return whether emoji rendering is currently enabled."""
    return _emoji_enabled


def draw_emoji(emoji: str, width: int = 2, *, label: str = "") -> str:
    """Pad emojis to a consistent cell width for list alignment.

    When emoji mode is disabled (via ``set_emoji_enabled(False)``), returns
    ``[label]`` (padded to *width*) if a *label* is provided, or an empty
    string otherwise.
    """
    if not _emoji_enabled:
        if label:
            tag = f"[{label}]"
            tag_width = cell_len(tag)
            if tag_width >= width:
                return tag
            return f"{tag}{' ' * (width - tag_width)}"
        return ""
    if not emoji:
        return ""
    try:
        emoji_width = cell_len(emoji)
    except (TypeError, ValueError):
        return emoji
    if emoji_width >= width:
        return emoji
    return f"{emoji}{' ' * (width - emoji_width)}"
