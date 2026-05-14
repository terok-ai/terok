# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task ID generation, validation, and prefix resolution.

Task IDs are Crockford-base32 with a structural signature: a 16-letter
non-hex head + a digit + three full-alphabet chars.  The shape makes a
terok task ID unmistakable from a podman container ID or git SHA at a
glance.  Pre-0.8.0 hex IDs are still accepted (with a deprecation
warning) on the read path.
"""

import re
import secrets
import string
import warnings

from .meta import iter_task_ids, task_exists, tasks_meta_dir

_TASK_ID_HEAD_CHARS = "ghjkmnpqrstvwxyz"
"""Crockford-legal lowercase letters outside hex (``g-z`` minus ``i l o u``, 16 chars).

First character of every task ID, chosen so the ID is unmistakably non-hex
within the first character — disambiguating terok task IDs from podman
container IDs, git SHAs, and other hex blobs at a glance.
"""

_TASK_ID_BODY_CHARS = "0123456789abcdefghjkmnpqrstvwxyz"
"""Full Crockford base32 alphabet, lowercase (``0-9 a-z`` minus ``i l o u``, 32 chars)."""

_TASK_ID_LEN = 5
"""Task ID length.  16 · 10 · 32^3 ≈ 5.2M ids per project (~22.3 bits,
effectively Crockford-4.5 by entropy) — ample for the retry-on-collision
loop given realistic project sizes."""

_TASK_ID_CROCKFORD_4_5_RE = re.compile(r"[ghjkmnp-tv-z][0-9][0-9a-hjkmnp-tv-z]{3}")
"""Canonical full-form validator for current-format task IDs.

Called *Crockford-4.5* because the structural-signature chars at
positions 1–2 (16-letter head, 10 digits) carry only 4.64 bits
instead of 10 — total entropy 22.32 bits, equivalent to 4.46 full
Crockford chars rather than 5.
"""

_TASK_ID_PREFIX_RE = re.compile(r"[ghjkmnp-tv-z](?:[0-9][0-9a-hjkmnp-tv-z]{0,3})?")
"""Prefix-match regex for a current-format task ID (1 to `_TASK_ID_LEN` chars)."""

_LEGACY_HEX_TASK_ID_PREFIX_RE = re.compile(r"[0-9a-f]{1,8}")
"""Prefix-match regex for pre-0.8.0 hex task IDs.  Deprecated in 0.8.0; removal in 0.9.0."""

_LEGACY_HEX_TASK_ID_FULL_RE = re.compile(r"[0-9a-f]{8}")
"""Full-form validator for pre-0.8.0 hex task IDs.  Deprecated; removal in 0.9.0."""

_TASK_ID_AMBIGUOUS_LETTERS = "ilo"
"""Crockford's visually ambiguous letters: ``I``/``L`` → ``1`` and ``O`` → ``0``.

See https://www.crockford.com/base32.html.  We encode only in canonical
lowercase, but accept these substitutions at user-facing entry points to
widen the input surface.
"""

_TASK_ID_INPUT_TRANSLATE = str.maketrans(_TASK_ID_AMBIGUOUS_LETTERS, "110")
"""Translate table matching `_TASK_ID_AMBIGUOUS_LETTERS`."""


def normalize_task_id_input(raw: str) -> str:
    """Collapse user-input variants to the canonical lowercase form.

    Strips hyphens, lowercases, and applies the Crockford
    ``I/L → 1``, ``O → 0`` substitutions.  The result is still subject
    to `_TASK_ID_PREFIX_RE` downstream — this only widens what
    we accept, never what we emit.

    **Call-site discipline:** only call this at user-interactive CLI
    boundaries — argparse dispatch handlers and argcomplete completers.
    Internal code paths (``lib/*``, ``tui/*``, TUI pickers, clearance,
    anything reading task IDs from disk, OCI annotations, or runtime
    state) always work with canonical lowercase IDs and must *never*
    re-normalise.  Leaking this tolerance inward quietly defeats the
    "we encode only in canonical form" invariant.
    """
    return raw.replace("-", "").lower().translate(_TASK_ID_INPUT_TRANSLATE)


def is_task_id(text: str) -> bool:
    """Return True if *text* is a well-formed task ID (current or legacy)."""
    return bool(
        _TASK_ID_CROCKFORD_4_5_RE.fullmatch(text) or _LEGACY_HEX_TASK_ID_FULL_RE.fullmatch(text)
    )


def _gen_task_id() -> str:
    """Return a fresh task ID: Crockford-letter head + digit + 3 Crockford chars."""
    alphabets = (_TASK_ID_HEAD_CHARS, string.digits) + (_TASK_ID_BODY_CHARS,) * (_TASK_ID_LEN - 2)
    tid = "".join(map(secrets.choice, alphabets))
    assert _TASK_ID_CROCKFORD_4_5_RE.fullmatch(tid), tid  # generator-output invariant
    return tid


def _generate_unique_id(existing: set[str]) -> str:
    """Generate a unique task ID not present in *existing*."""
    for _ in range(100):
        candidate = _gen_task_id()
        if candidate not in existing:
            return candidate
    raise RuntimeError("Failed to generate unique task ID after 100 attempts")


def _validate_task_id_prefix(prefix: str) -> None:
    """Validate *prefix* and warn on the deprecated legacy-hex format.

    Raises ``SystemExit`` if *prefix* matches neither the current
    Crockford-4.5 format nor the legacy pre-0.8.0 hex format.  Dispatch
    is unambiguous: Crockford IDs always start with ``[g-z]`` minus
    ``i l o u``, which is disjoint from hex.
    """
    if _TASK_ID_PREFIX_RE.fullmatch(prefix):
        return
    if _LEGACY_HEX_TASK_ID_PREFIX_RE.fullmatch(prefix):
        warnings.warn(
            f"Task ID {prefix!r} uses the pre-0.8.0 hex format; "
            "support will be removed in 0.9.0 — recreate the task to "
            "adopt the current format.",
            DeprecationWarning,
            stacklevel=3,
        )
        return
    raise SystemExit(
        f"Invalid task ID prefix {prefix!r}; "
        f"expected 1-{_TASK_ID_LEN} Crockford chars (e.g. 'k3v8h')"
    )


def resolve_task_id(project_id: str, prefix: str) -> str:
    """Resolve a (possibly partial) task ID to its full form.

    The *prefix* is first run through [`normalize_task_id_input`][terok.lib.orchestration.tasks.normalize_task_id_input],
    so callers may pass uppercase, hyphenated, or ambiguous-letter
    variants (``K3-V8H``, ``k3v8I``) — they collapse to the canonical
    lowercase form before validation and lookup.

    Raises ``SystemExit`` with an actionable message on zero or multiple matches.
    """
    # Head position is letter-only, so an ``I/L/O`` there can't be a
    # Crockford body substitution — surface that as a specific error
    # instead of normalising it into a digit and letting downstream
    # mistake it for a legacy-hex prefix.
    stripped = prefix.replace("-", "").lower()
    if stripped[:1] in _TASK_ID_AMBIGUOUS_LETTERS:
        raise SystemExit(
            f"Invalid task ID prefix {prefix!r}; "
            f"task IDs never start with I, L, or O — "
            f"expected one of {_TASK_ID_HEAD_CHARS!r}"
        )
    prefix = stripped.translate(_TASK_ID_INPUT_TRANSLATE)
    _validate_task_id_prefix(prefix)
    meta_dir = tasks_meta_dir(project_id)
    if not meta_dir.is_dir():
        raise SystemExit(f"No tasks found for project {project_id}")
    if task_exists(project_id, prefix):
        return prefix
    matches = [tid for tid in iter_task_ids(meta_dir) if is_task_id(tid) and tid.startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"No task matching '{prefix}' in project {project_id}")
    raise SystemExit(f"Ambiguous task ID '{prefix}' — matches: {', '.join(sorted(matches))}")


__all__ = [
    "is_task_id",
    "normalize_task_id_input",
    "resolve_task_id",
]
