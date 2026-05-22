# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Install the XDG desktop entry + symbolic SVG icon for ``terok-tui``.

``terok setup`` calls `install_desktop_entry` (or the matching
`uninstall_desktop_entry`) as a default-on phase, so the TUI
appears as *Terok* in GNOME / KDE / XFCE application menus without the
operator knowing the template layout.  Every step soft-fails so a
headless host without ``.local/share`` or without ``xdg-utils`` never
kills the wider ``terok setup`` flow.

Preferred path for the ``.desktop`` file is ``xdg-utils`` —
``xdg-desktop-menu install`` runs ``desktop-file-install`` (validates
the file, catches malformed keys) and refreshes
``update-desktop-database`` for us.  The icon, however, is always
written manually: ``xdg-icon-resource install --size`` only accepts
numeric sizes or ``scalable`` (per the upstream xdg-utils source —
``size argument must be numeric or the word 'scalable'``), so a
symbolic icon (which lives in ``hicolor/symbolic/apps/``) can't be
registered through that path.  We drop the icon directly into the
hicolor tree and kick ``gtk-update-icon-cache`` ourselves.

When ``xdg-utils`` isn't on PATH (minimal container images, some CI
runners) we fall back to writing the ``.desktop`` ourselves too.
This is *best-effort*: the file ends up in the right place on hosts
that match the spec, but there's no ``desktop-file-install``
validation and no cover for DE-specific layout drift.
`install_desktop_entry` returns a `DesktopBackend` so the caller can
surface a gentle warning when the fallback kicks in.

The passive assets (``.desktop`` template, logo SVG) live under
``terok/resources/desktop/`` — this module is the *builder* that
renders them and delegates to the XDG tool of choice.  When ``ptyxis``
is on PATH, `_render_desktop_file` routes the launch through the
bundled ``terok-xdg-terminal-exec.sh`` shim to dodge a Ptyxis
standalone-mode bug — Fedora patches GLib to inject ``ptyxis`` into
GIO's hardcoded ``known_terminals[]`` (right after ``xdg-terminal-exec``)
so a vanilla ``Terminal=true`` launcher ends up as ``ptyxis -- terok-tui``,
which trips the bug.  See the shim's header for the rationale.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess  # nosec B404 — cache refresh binaries are trusted
import tempfile
from enum import StrEnum
from importlib import resources as importlib_resources
from importlib.resources.abc import Traversable
from pathlib import Path

from terok_util import render_template

_log = logging.getLogger(__name__)

#: Base name of the application launcher.
APP_NAME = "terok"

#: Icon name — the ``-symbolic`` suffix is honoured by GTK and Qt as a
#: marker that triggers the toolkit's symbolic-icon rendering pipeline,
#: which substitutes the placeholder fill (``#bebebe`` in our SVG) with
#: the active theme's foreground colour.  Same mechanism as ``Icon=
#: <name>-symbolic`` on every well-behaved GNOME / KDE app.
_ICON_NAME = f"{APP_NAME}-symbolic"

_DESKTOP_FILE = f"{APP_NAME}.desktop"
_ICON_FILE = f"{_ICON_NAME}.svg"
_TEMPLATE_NAME = "terok.desktop.template"
_LOGO_NAME = _ICON_FILE
_PTYXIS_SHIM_NAME = "terok-xdg-terminal-exec.sh"

# XDG Base Directory + Icon Theme spec path fragments.  Named so a
# future theme-dir shift is a single-constant change and so ``grep`` for
# the fragment lands on the canonical definition rather than every join
# site.  Symbolic icons live under ``hicolor/symbolic/apps/`` (the
# ``symbolic`` directory is hicolor's well-known symbolic-icon slot).
_APPLICATIONS_SUBDIR = "applications"
_ICONS_SUBDIR = "icons"
_HICOLOR_THEME = "hicolor"
_APPS_SUBDIR = "apps"
_ICON_SIZE_DIR = "symbolic"
_DEFAULT_DATA_HOME = (".local", "share")  # $HOME/.local/share — XDG fallback

_XDG_MENU_BINARY = "xdg-desktop-menu"
# xdg-icon-resource intentionally NOT used — its ``--size`` accepts only
# numeric values or ``scalable``, never ``symbolic``, so symbolic icons
# can't be registered through it.  Manual write into
# ``hicolor/symbolic/apps/`` instead.

_SUBPROCESS_TIMEOUT_S = 10


class DesktopBackend(StrEnum):
    """Which install path `install_desktop_entry` actually took."""

    XDG_UTILS = "xdg-utils"
    FALLBACK = "fallback"


def _resource_dir() -> Traversable:
    """Return a ``Traversable`` rooted at the passive ``resources/desktop/`` assets.

    Uses the namespace-package idiom already used by
    [`terok.lib.core.config.bundled_presets_dir`][terok.lib.core.config.bundled_presets_dir]: walk the top-level
    ``terok`` package into the ``resources`` + ``desktop`` subdirs (no
    ``__init__.py`` anywhere under ``resources/``, matching the project's
    "resources hold only data files" convention).
    """
    return importlib_resources.files("terok").joinpath("resources", "desktop")


def install_desktop_entry(bin_path: str | Path) -> DesktopBackend:
    """Render the launcher + copy the icon, via xdg-utils when available.

    Args:
        bin_path: Absolute path (or bare name) to ``terok-tui``.  The
            freedesktop ``Exec=`` / ``TryExec=`` keys need this — the
            launcher's minimal PATH often misses ``~/.local/bin``, so
            ``shutil.which("terok-tui")``'s absolute result is preferred
            over the short name.

    Returns:
        The `DesktopBackend` actually used.  Callers wire this to
        a status-line warning when the fallback kicks in so the operator
        knows ``xdg-utils`` is missing.
    """
    rendered = _render_desktop_file(str(bin_path))
    logo_bytes = _resource_dir().joinpath(_LOGO_NAME).read_bytes()
    if xdg_utils_available() and _install_via_xdg_utils(rendered, logo_bytes):
        return DesktopBackend.XDG_UTILS
    # xdg-utils missing *or* it barfed (readonly menu dir, timeout, bad
    # DE detection) — land the files ourselves so the operator still
    # gets a working launcher, and report FALLBACK so the caller can
    # warn.  The DEBUG log carries the xdg-utils failure detail.
    _install_manually(rendered, logo_bytes)
    return DesktopBackend.FALLBACK


def uninstall_desktop_entry() -> DesktopBackend:
    """Remove the launcher + icon, via xdg-utils when available.

    Returns:
        The `DesktopBackend` actually used — symmetric with
        `install_desktop_entry`.  XDG_UTILS only when both
        front-ends reported rc 0; on failure (or xdg-utils absent) we
        retry via manual unlinks and report FALLBACK so the teardown
        leaves no stragglers even when xdg-utils misbehaves.
    """
    if xdg_utils_available() and _uninstall_via_xdg_utils():
        return DesktopBackend.XDG_UTILS
    _uninstall_manually()
    return DesktopBackend.FALLBACK


def is_desktop_entry_installed() -> bool:
    """Return True when both the ``.desktop`` and icon files exist on disk.

    Probes the install tree directly rather than asking xdg-utils — both
    backends land the same files in the same XDG-spec locations, so the
    presence check is backend-agnostic.
    """
    return _desktop_entry_path().is_file() and _icon_path().is_file()


# ── xdg-utils backend ─────────────────────────────────────────────────


def xdg_utils_available() -> bool:
    """Return True when xdg-desktop-menu is on PATH (icon side is always manual)."""
    return bool(shutil.which(_XDG_MENU_BINARY))


def _install_via_xdg_utils(desktop_contents: str, logo_bytes: bytes) -> bool:
    """Install the ``.desktop`` via xdg-utils; write the icon manually.

    ``xdg-desktop-menu install`` runs ``desktop-file-install`` (catches
    malformed keys), drops the file under the user's applications dir,
    and kicks ``update-desktop-database``.  We stage to a tempdir
    because xdg-desktop-menu names the installed file after the source
    basename — staging to ``/tmp/.../terok.desktop`` makes the launcher
    register as ``terok``.

    Icon: ``xdg-icon-resource install --size`` accepts only numeric
    sizes or ``scalable``, never ``symbolic``, so we write the symbolic
    icon directly into ``hicolor/symbolic/apps/`` and refresh
    ``gtk-update-icon-cache`` ourselves.

    Returns:
        True only when the ``.desktop`` install reported success.
        Icon install is always attempted (manual write).  A failed
        ``.desktop`` install reads as False so the caller retries via
        the manual path.
    """
    with tempfile.TemporaryDirectory(prefix="terok-desktop-") as td:
        staged_desktop = Path(td) / _DESKTOP_FILE
        staged_desktop.write_text(desktop_contents, encoding="utf-8")
        menu_ok = _run_xdg(
            _XDG_MENU_BINARY,
            "install",
            "--novendor",
            str(staged_desktop),
        )
    if not menu_ok:
        return False
    _write_icon(logo_bytes)
    _refresh_icon_cache()
    return True


def _uninstall_via_xdg_utils() -> bool:
    """Remove the ``.desktop`` via xdg-utils; unlink the icon manually.

    Symmetric with `_install_via_xdg_utils` — xdg-utils can't manage
    symbolic icons, so the icon side is always direct unlink +
    ``gtk-update-icon-cache``.

    Returns:
        True when the xdg-desktop-menu uninstall reports success.
        Icon unlink is always attempted.
    """
    menu_ok = _run_xdg(_XDG_MENU_BINARY, "uninstall", "--novendor", _DESKTOP_FILE)
    _unlink_icon()
    _refresh_icon_cache()
    return menu_ok


def _run_xdg(binary: str, *args: str) -> bool:
    """Invoke an xdg-utils front-end; return True only on rc-0, False otherwise.

    Never raises — a hung / missing / broken front-end lands in DEBUG
    so an operator chasing a weird install state can grep
    ``journalctl --user`` without ``terok setup`` exploding.  The
    return value lets `_install_via_xdg_utils` decide whether to
    hand off to the manual fallback.
    """
    found = shutil.which(binary)
    if not found:  # pragma: no cover — gated by xdg_utils_available
        return False
    # nosec B603 — argv is our own literal binary path plus subcommand/arg tokens.
    try:
        result = subprocess.run(  # noqa: S603  # nosec B603
            [found, *args],
            check=False,
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.debug("%s %s failed: %s", binary, args, exc)
        return False
    if result.returncode != 0:
        _log.debug(
            "%s %s exited with %d: %s",
            binary,
            args,
            result.returncode,
            (result.stderr or b"").decode(errors="replace").strip(),
        )
        return False
    return True


# ── Manual fallback ───────────────────────────────────────────────────


def _install_manually(desktop_contents: str, logo_bytes: bytes) -> None:
    """Write the launcher + icon directly and trigger cache refreshes by hand."""
    desktop_path = _desktop_entry_path()
    desktop_path.parent.mkdir(parents=True, exist_ok=True)
    desktop_path.write_text(desktop_contents, encoding="utf-8")
    desktop_path.chmod(0o644)

    _write_icon(logo_bytes)

    _refresh_desktop_database()
    _refresh_icon_cache()


def _uninstall_manually() -> None:
    """Unlink the launcher + icon and refresh caches so menus forget."""
    try:
        _desktop_entry_path().unlink(missing_ok=True)
    except OSError as exc:
        _log.warning("failed to unlink %s: %s", _desktop_entry_path(), exc)
    _unlink_icon()
    _refresh_desktop_database()
    _refresh_icon_cache()


def _write_icon(logo_bytes: bytes) -> None:
    """Write the symbolic SVG to ``hicolor/symbolic/apps/`` directly.

    Used by both the xdg-utils path and the manual path — xdg-icon-resource
    can't register symbolic icons (its ``--size`` rejects anything but a
    numeric value or ``scalable``), so the symbolic install is always a
    direct write.  Caller is expected to refresh the icon cache afterwards.
    """
    icon_path = _icon_path()
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    icon_path.write_bytes(logo_bytes)
    icon_path.chmod(0o644)


def _unlink_icon() -> None:
    """Remove the installed icon; symmetric with `_write_icon`."""
    icon_path = _icon_path()
    try:
        icon_path.unlink(missing_ok=True)
    except OSError as exc:
        _log.warning("failed to unlink %s: %s", icon_path, exc)


# ── Path derivation ───────────────────────────────────────────────────


def _desktop_entry_path() -> Path:
    """Return ``$XDG_DATA_HOME/applications/terok.desktop`` (XDG default)."""
    return _data_home() / _APPLICATIONS_SUBDIR / _DESKTOP_FILE


def _icon_path() -> Path:
    """Return ``$XDG_DATA_HOME/icons/hicolor/symbolic/apps/terok-symbolic.svg``."""
    return (
        _data_home() / _ICONS_SUBDIR / _HICOLOR_THEME / _ICON_SIZE_DIR / _APPS_SUBDIR / _ICON_FILE
    )


def _data_home() -> Path:
    """Return the user's XDG data home, honouring ``$XDG_DATA_HOME`` when set."""
    override = os.environ.get("XDG_DATA_HOME")
    return Path(override) if override else Path.home().joinpath(*_DEFAULT_DATA_HOME)


# ── Template rendering ────────────────────────────────────────────────


def _render_desktop_file(bin_str: str) -> str:
    """Render ``terok.desktop`` with the right Exec / TryExec / Terminal values."""
    # We gate on `ptyxis` alone.  A more precise "is this a Fedora-
    # patched glib" probe exists — `grep -aow ptyxis /lib64/libgio-2.0.so.0`
    # exits 0 iff Fedora's `default-terminal.patch` injected
    # { "ptyxis", "--" } into gio's hardcoded `known_terminals[]` — but
    # it's (a) un-pythonic (shelling out to grep at a fixed sopath) and
    # (b) not actually sufficient: when `xdg-terminal-exec` is installed
    # it precedes ptyxis in the glib list and consults the user's
    # `xdg-terminals.list`, so the rodata literal mis-predicts the
    # real launch.  Hijacking on PATH-presence is over-eager on hosts
    # where vanilla glib wouldn't pick ptyxis anyway, but that's fine
    # — the user installed Ptyxis on purpose; the shim gives them the
    # container-tabs UI they want.
    if shutil.which("ptyxis"):
        shim = str(_resource_dir().joinpath(_PTYXIS_SHIM_NAME))
        # ``TryExec`` points at the binary, not the shim: pipx (and any
        # PEP 517 wheel installer) ships package data without the
        # executable bit, and GNOME silently hides any launcher whose
        # ``TryExec`` target isn't ``+x``.  ``Exec`` invokes the shim
        # via ``/bin/sh``, so the shim doesn't need to be executable —
        # and the semantic we want to gate on ("is terok-tui actually
        # installed?") is best expressed by ``TryExec``-ing the binary
        # anyway.
        variables = {
            "EXEC": f"/bin/sh {shim} {bin_str}",
            "TRY_EXEC": bin_str,
            "TERMINAL": "false",
        }
    else:
        variables = {"EXEC": bin_str, "TRY_EXEC": bin_str, "TERMINAL": "true"}
    with importlib_resources.as_file(_resource_dir().joinpath(_TEMPLATE_NAME)) as template_path:
        return render_template(template_path, variables)


# ── Manual cache refresh (fallback backend only) ──────────────────────


def _refresh_desktop_database() -> None:
    """Nudge ``update-desktop-database`` if present; silent otherwise."""
    _run_cache_refresh(
        "update-desktop-database",
        [_data_home() / _APPLICATIONS_SUBDIR],
    )


def _refresh_icon_cache() -> None:
    """Nudge ``gtk-update-icon-cache`` on the hicolor theme if present."""
    _run_cache_refresh(
        "gtk-update-icon-cache",
        ["-q", "-t", _data_home() / _ICONS_SUBDIR / _HICOLOR_THEME],
    )


def _run_cache_refresh(binary: str, args: list[str | Path]) -> None:
    """Invoke *binary* with *args*, swallow every failure — caches are optional."""
    found = shutil.which(binary)
    if not found:
        return
    # nosec B603 — argv is a literal + controlled Path; no shell, no user input.
    try:
        result = subprocess.run(  # noqa: S603  # nosec B603
            [found, *[str(a) for a in args]],
            check=False,
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.debug("%s refresh failed: %s", binary, exc)
        return
    if result.returncode != 0:
        # Same DEBUG trail as _run_xdg — ``check=False`` keeps us quiet,
        # the log makes the failure diagnosable after the fact.
        _log.debug(
            "%s exited with %d: %s",
            binary,
            result.returncode,
            (result.stderr or b"").decode(errors="replace").strip(),
        )
