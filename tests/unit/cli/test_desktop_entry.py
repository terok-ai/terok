# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for `terok.cli.commands._desktop_entry` — XDG launcher + icon install."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from terok.cli.commands import _desktop_entry as desktop


@pytest.fixture
def xdg_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``$XDG_DATA_HOME`` to a pytest tmp dir for every test."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path


# ── which-mock side effects — pick which install backend is "available" ──


def _which_no_xdg_utils(name: str) -> str | None:
    """``shutil.which`` side-effect: xdg-utils missing, manual cache bins present."""
    if name == "xdg-desktop-menu":
        return None
    return f"/usr/bin/{name}"


def _which_nothing(name: str) -> str | None:
    """``shutil.which`` side-effect: nothing on PATH at all."""
    return None


def _which_everything(name: str) -> str:
    """``shutil.which`` side-effect: every probed binary reports present."""
    return f"/usr/bin/{name}"


# ── xdg-utils backend (preferred) ─────────────────────────────────────


class TestInstallViaXdgUtils:
    """When xdg-desktop-menu is on PATH, delegate the .desktop install + DB refresh
    to it; the symbolic icon is always written manually (xdg-icon-resource rejects
    ``--size symbolic``)."""

    def test_install_uses_xdg_for_desktop_only(self, xdg_data_home: Path) -> None:
        """Install shells out to xdg-desktop-menu (.desktop side) and
        gtk-update-icon-cache (icon-cache refresh for the manual icon write).
        It must NOT call xdg-icon-resource — that path rejects --size symbolic."""
        calls: list[list[str]] = []

        def record(argv: list[str], *_a, **_kw):
            calls.append(argv)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")

        with (
            mock.patch(
                "terok.cli.commands._desktop_entry.shutil.which",
                side_effect=_which_everything,
            ),
            mock.patch("terok.cli.commands._desktop_entry.subprocess.run", side_effect=record),
        ):
            desktop.install_desktop_entry("/opt/venv/bin/terok-tui")

        binaries = [argv[0].split("/")[-1] for argv in calls]
        assert "xdg-desktop-menu" in binaries
        # xdg-icon-resource only accepts numeric / 'scalable' for --size, so
        # symbolic icons can't go through it.  Guard against regression.
        assert "xdg-icon-resource" not in binaries
        # xdg-desktop-icon is the *wrong* tool — it installs to the user's
        # Desktop folder, skipping the hicolor theme.  Explicit guard.
        assert "xdg-desktop-icon" not in binaries
        # Icon cache refresh runs after the manual icon write.
        assert "gtk-update-icon-cache" in binaries
        # update-desktop-database is xdg-desktop-menu's own job; we don't
        # call it ourselves when xdg-utils handled the .desktop side.
        assert "update-desktop-database" not in binaries

    def test_stages_desktop_file_with_target_basename(self, xdg_data_home: Path) -> None:
        """Staged .desktop handed to xdg-utils uses the final ``terok.desktop`` name.

        xdg-desktop-menu names the installed file after the source basename,
        so staging to ``/tmp/.../foo.desktop`` would register the launcher
        as ``foo``.  Icon staging is gone — symbolic icons go via manual
        write, not xdg-icon-resource.
        """
        calls: list[list[str]] = []

        with (
            mock.patch(
                "terok.cli.commands._desktop_entry.shutil.which",
                side_effect=_which_everything,
            ),
            mock.patch(
                "terok.cli.commands._desktop_entry.subprocess.run",
                side_effect=lambda argv, *a, **kw: (
                    calls.append(argv)
                    or subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"", stderr=b"")
                ),
            ),
        ):
            desktop.install_desktop_entry("terok-tui")

        desktop_call = next(argv for argv in calls if argv[0].endswith("xdg-desktop-menu"))
        assert Path(desktop_call[-1]).name == "terok.desktop"
        # The ``--novendor`` flag is mandatory for ``.desktop`` files not
        # named ``{vendor}-{appname}.desktop``; xdg-utils would otherwise
        # refuse the install.
        assert "--novendor" in desktop_call

    def test_install_returns_xdg_utils_backend(self, xdg_data_home: Path) -> None:
        """Return value advertises which backend was used so callers can warn."""
        fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        with (
            mock.patch(
                "terok.cli.commands._desktop_entry.shutil.which",
                side_effect=_which_everything,
            ),
            mock.patch("terok.cli.commands._desktop_entry.subprocess.run", return_value=fake_proc),
        ):
            assert desktop.install_desktop_entry("terok-tui") is desktop.DesktopBackend.XDG_UTILS

    def test_uninstall_returns_xdg_utils_backend(self, xdg_data_home: Path) -> None:
        """Successful xdg-utils uninstall reports XDG_UTILS."""
        fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        with (
            mock.patch(
                "terok.cli.commands._desktop_entry.shutil.which",
                side_effect=_which_everything,
            ),
            mock.patch("terok.cli.commands._desktop_entry.subprocess.run", return_value=fake_proc),
        ):
            assert desktop.uninstall_desktop_entry() is desktop.DesktopBackend.XDG_UTILS

    def test_uninstall_xdg_failure_falls_back_to_manual(self, xdg_data_home: Path) -> None:
        """xdg-utils uninstall fails → manual unlinks clean up, backend is FALLBACK.

        Same rationale as the install side: a half-completed teardown
        (menu gone, icon still registered) is the trap — retry
        manually so the caller's "did it work?" signal is honest.
        """
        # Seed the XDG tree the way a prior install would have.
        with mock.patch(
            "terok.cli.commands._desktop_entry.shutil.which", side_effect=_which_nothing
        ):
            desktop.install_desktop_entry("terok-tui")
        assert desktop.is_desktop_entry_installed()

        failing = subprocess.CompletedProcess(
            args=[],
            returncode=3,
            stdout=b"",
            stderr=b"xdg-desktop-menu: nothing to uninstall",
        )
        with (
            mock.patch(
                "terok.cli.commands._desktop_entry.shutil.which",
                side_effect=_which_everything,
            ),
            mock.patch("terok.cli.commands._desktop_entry.subprocess.run", return_value=failing),
        ):
            backend = desktop.uninstall_desktop_entry()

        assert backend is desktop.DesktopBackend.FALLBACK
        # Manual unlinks ran, so both files are gone even though
        # xdg-utils claimed failure.
        assert not desktop.is_desktop_entry_installed()

    def test_uninstall_delegates_desktop_to_xdg_utils(self) -> None:
        """``uninstall`` invokes ``xdg-desktop-menu uninstall``; icon unlink is manual."""
        calls: list[list[str]] = []

        with (
            mock.patch(
                "terok.cli.commands._desktop_entry.shutil.which",
                side_effect=_which_everything,
            ),
            mock.patch(
                "terok.cli.commands._desktop_entry.subprocess.run",
                side_effect=lambda argv, *a, **kw: (
                    calls.append(argv)
                    or subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"", stderr=b"")
                ),
            ),
        ):
            desktop.uninstall_desktop_entry()

        verbs = [(argv[0].split("/")[-1], argv[1]) for argv in calls]
        assert ("xdg-desktop-menu", "uninstall") in verbs
        # Icon side: no xdg-icon-resource call — symbolic icons go via manual
        # unlink, with gtk-update-icon-cache picking up the removal.
        binaries = [argv[0].split("/")[-1] for argv in calls]
        assert "xdg-icon-resource" not in binaries
        assert "gtk-update-icon-cache" in binaries

    def test_xdg_subprocess_failure_is_swallowed(self, xdg_data_home: Path) -> None:
        """A hung / broken xdg-utils front-end must not raise."""
        with (
            mock.patch(
                "terok.cli.commands._desktop_entry.shutil.which",
                side_effect=_which_everything,
            ),
            mock.patch(
                "terok.cli.commands._desktop_entry.subprocess.run",
                side_effect=OSError("exec format error"),
            ),
        ):
            desktop.install_desktop_entry("terok-tui")  # must not raise

    def test_xdg_failure_falls_back_to_manual(self, xdg_data_home: Path) -> None:
        """xdg-utils on PATH but calls fail → manual path runs, backend is FALLBACK.

        The whole point of the backend return: XDG_UTILS means xdg-utils
        *actually did the work*, not "we called it and hoped".  A broken
        front-end (readonly menu dir, DE-detection quirk) has to read as
        FALLBACK so the operator sees the WARN.
        """
        failing = subprocess.CompletedProcess(
            args=[],
            returncode=3,
            stdout=b"",
            stderr=b"xdg-desktop-menu: no writable system menu directory found",
        )
        with (
            mock.patch(
                "terok.cli.commands._desktop_entry.shutil.which",
                side_effect=_which_everything,
            ),
            mock.patch(
                "terok.cli.commands._desktop_entry.subprocess.run",
                return_value=failing,
            ),
        ):
            backend = desktop.install_desktop_entry("terok-tui")

        assert backend is desktop.DesktopBackend.FALLBACK
        # Manual path actually landed the files — a FALLBACK label with
        # no files on disk would be the worst of both worlds.
        assert desktop.is_desktop_entry_installed()

    def test_non_zero_xdg_exit_is_logged(
        self,
        xdg_data_home: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A non-zero xdg-utils exit lands in the DEBUG log with stderr + rc."""
        import logging

        caplog.set_level(logging.DEBUG, logger="terok.cli.commands._desktop_entry")
        failing = subprocess.CompletedProcess(
            args=[],
            returncode=3,
            stdout=b"",
            stderr=b"xdg-desktop-menu: no writable system menu directory found",
        )
        with (
            mock.patch(
                "terok.cli.commands._desktop_entry.shutil.which",
                side_effect=_which_everything,
            ),
            mock.patch("terok.cli.commands._desktop_entry.subprocess.run", return_value=failing),
        ):
            desktop.install_desktop_entry("terok-tui")
        assert "exited with 3" in caplog.text
        assert "no writable system menu directory" in caplog.text


# ── Manual fallback (no xdg-utils) ────────────────────────────────────


class TestInstallManualFallback:
    """Without xdg-utils, write the XDG tree directly + call cache bins by hand."""

    def test_writes_desktop_file_with_templated_bin(self, xdg_data_home: Path) -> None:
        """``{{BIN}}`` / ``{{TRY_EXEC}}`` land as the resolved binary path."""
        with mock.patch(
            "terok.cli.commands._desktop_entry.shutil.which", side_effect=_which_nothing
        ):
            desktop.install_desktop_entry("/usr/local/bin/terok-tui")
        content = (xdg_data_home / "applications" / "terok.desktop").read_text()
        assert "Exec=/usr/local/bin/terok-tui" in content
        assert "TryExec=/usr/local/bin/terok-tui" in content
        # ``Icon=terok-symbolic`` — the ``-symbolic`` suffix is the GTK/Qt
        # marker that triggers the symbolic-icon rendering pipeline so the
        # placeholder fill gets substituted with the theme foreground colour.
        assert "Icon=terok-symbolic" in content
        assert "Terminal=true" in content

    def test_writes_icon_into_hicolor_tree(self, xdg_data_home: Path) -> None:
        """The bundled SVG ends up under hicolor/symbolic/apps/terok-symbolic.svg."""
        with mock.patch(
            "terok.cli.commands._desktop_entry.shutil.which", side_effect=_which_nothing
        ):
            desktop.install_desktop_entry("terok-tui")
        icon = xdg_data_home / "icons" / "hicolor" / "symbolic" / "apps" / "terok-symbolic.svg"
        assert icon.is_file()
        # SVG root marker — cheap check that it's the real file, not an empty write.
        assert b"<svg" in icon.read_bytes()[:512]

    def test_runs_cache_refresh_binaries_when_present(self, xdg_data_home: Path) -> None:
        """Manual path invokes ``update-desktop-database`` + ``gtk-update-icon-cache``."""
        fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        calls: list[list[str]] = []

        with (
            mock.patch(
                "terok.cli.commands._desktop_entry.shutil.which",
                side_effect=_which_no_xdg_utils,
            ),
            mock.patch(
                "terok.cli.commands._desktop_entry.subprocess.run",
                side_effect=lambda argv, *a, **kw: calls.append(argv) or fake_proc,
            ),
        ):
            desktop.install_desktop_entry("terok-tui")

        binaries = [argv[0].split("/")[-1] for argv in calls]
        assert "update-desktop-database" in binaries
        assert "gtk-update-icon-cache" in binaries

    def test_install_returns_fallback_backend(self, xdg_data_home: Path) -> None:
        """With xdg-utils absent the return value says so — drives the setup warning."""
        with mock.patch(
            "terok.cli.commands._desktop_entry.shutil.which", side_effect=_which_nothing
        ):
            assert desktop.install_desktop_entry("terok-tui") is desktop.DesktopBackend.FALLBACK

    def test_cache_refresh_skipped_when_binaries_missing(self, xdg_data_home: Path) -> None:
        """Nothing on PATH at all → no subprocess fired, install still succeeds."""
        with (
            mock.patch(
                "terok.cli.commands._desktop_entry.shutil.which", side_effect=_which_nothing
            ),
            mock.patch("terok.cli.commands._desktop_entry.subprocess.run") as run,
        ):
            desktop.install_desktop_entry("terok-tui")
        run.assert_not_called()

    def test_cache_refresh_swallows_subprocess_failure(self, xdg_data_home: Path) -> None:
        """A hung / broken cache refresh binary can't derail the install."""
        with (
            mock.patch(
                "terok.cli.commands._desktop_entry.shutil.which",
                side_effect=_which_no_xdg_utils,
            ),
            mock.patch(
                "terok.cli.commands._desktop_entry.subprocess.run",
                side_effect=OSError("exec format error"),
            ),
        ):
            desktop.install_desktop_entry("terok-tui")  # must not raise

    def test_non_zero_cache_refresh_exit_is_logged(
        self,
        xdg_data_home: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A non-zero ``update-desktop-database`` / ``gtk-update-icon-cache`` exit is DEBUG-logged."""
        import logging

        caplog.set_level(logging.DEBUG, logger="terok.cli.commands._desktop_entry")
        failing = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=b"",
            stderr=b"gtk-update-icon-cache: Cache file is up to date",
        )
        with (
            mock.patch(
                "terok.cli.commands._desktop_entry.shutil.which",
                side_effect=_which_no_xdg_utils,
            ),
            mock.patch("terok.cli.commands._desktop_entry.subprocess.run", return_value=failing),
        ):
            desktop.install_desktop_entry("terok-tui")
        assert "exited with 1" in caplog.text
        assert "Cache file is up to date" in caplog.text


class TestUninstallDesktopEntry:
    """``uninstall_desktop_entry`` removes both files + refreshes caches."""

    def test_unlinks_desktop_file_and_icon(self, xdg_data_home: Path) -> None:
        with mock.patch(
            "terok.cli.commands._desktop_entry.shutil.which", side_effect=_which_nothing
        ):
            desktop.install_desktop_entry("terok-tui")
            assert desktop.is_desktop_entry_installed()
            desktop.uninstall_desktop_entry()
        assert not desktop.is_desktop_entry_installed()

    def test_uninstall_when_not_installed_is_noop(self, xdg_data_home: Path) -> None:
        """Running the teardown on a clean host doesn't raise."""
        with mock.patch(
            "terok.cli.commands._desktop_entry.shutil.which", side_effect=_which_nothing
        ):
            desktop.uninstall_desktop_entry()
        assert not desktop.is_desktop_entry_installed()


class TestIsDesktopEntryInstalled:
    """Presence check honours both files existing."""

    def test_returns_false_when_neither_present(self, xdg_data_home: Path) -> None:
        assert desktop.is_desktop_entry_installed() is False

    def test_returns_false_when_only_desktop_file(self, xdg_data_home: Path) -> None:
        (xdg_data_home / "applications").mkdir(parents=True)
        (xdg_data_home / "applications" / "terok.desktop").write_text("")
        assert desktop.is_desktop_entry_installed() is False

    def test_returns_true_when_both_present(self, xdg_data_home: Path) -> None:
        with mock.patch(
            "terok.cli.commands._desktop_entry.shutil.which", side_effect=_which_nothing
        ):
            desktop.install_desktop_entry("terok-tui")
        assert desktop.is_desktop_entry_installed() is True


class TestBackendSelection:
    """``xdg_utils_available`` gates whether the .desktop install delegates to
    xdg-desktop-menu.  The icon install is always manual regardless."""

    def test_xdg_desktop_menu_alone_is_enough(self) -> None:
        """Only ``xdg-desktop-menu`` is required — the icon side never uses xdg-utils."""

        def only_menu(name: str) -> str | None:
            return "/usr/bin/xdg-desktop-menu" if name == "xdg-desktop-menu" else None

        with mock.patch("terok.cli.commands._desktop_entry.shutil.which", side_effect=only_menu):
            assert desktop.xdg_utils_available() is True

    def test_xdg_desktop_icon_alone_is_not_enough(self) -> None:
        """Presence of ``xdg-desktop-icon`` (wrong tool) must not flip the gate on.

        Older code probed for ``xdg-desktop-icon``, but that front-end
        installs icons to the user's Desktop folder instead of the
        theme.  Make the regression explicit.
        """

        def only_wrong_icon_tool(name: str) -> str | None:
            return "/usr/bin/xdg-desktop-icon" if name == "xdg-desktop-icon" else None

        with mock.patch(
            "terok.cli.commands._desktop_entry.shutil.which",
            side_effect=only_wrong_icon_tool,
        ):
            assert desktop.xdg_utils_available() is False

    def test_returns_false_when_xdg_desktop_menu_missing(self) -> None:
        """Without xdg-desktop-menu on PATH → manual fallback for .desktop too."""
        with mock.patch(
            "terok.cli.commands._desktop_entry.shutil.which", side_effect=_which_nothing
        ):
            assert desktop.xdg_utils_available() is False


# ── Ptyxis-gate render-form selection ─────────────────────────────────


def _which_only(present: set[str]) -> object:
    """``shutil.which`` side-effect: report only the names in *present*."""
    return lambda name: f"/usr/bin/{name}" if name in present else None


class TestPtyxisGate:
    """When ptyxis is on PATH, route Exec through the shim — else standard form.

    The gate fires on ptyxis alone because Fedora patches GLib to inject
    ptyxis into GIO's hardcoded ``known_terminals[]`` table, so any
    ``Terminal=true`` launcher on F40+ becomes ``ptyxis -- terok-tui``
    and trips Ptyxis's standalone-mode bug (no container tabs).
    """

    @pytest.mark.parametrize(
        "present",
        [set(), {"xdg-terminal-exec"}],
        ids=["nothing", "no-ptyxis"],
    )
    def test_gate_inactive_renders_standard_form(
        self,
        xdg_data_home: Path,
        present: set[str],
    ) -> None:
        with mock.patch(
            "terok.cli.commands._desktop_entry.shutil.which",
            side_effect=_which_only(present),
        ):
            desktop.install_desktop_entry("/usr/local/bin/terok-tui")
        content = (xdg_data_home / "applications" / "terok.desktop").read_text()
        assert "Terminal=true" in content
        assert "Exec=/usr/local/bin/terok-tui" in content
        assert "TryExec=/usr/local/bin/terok-tui" in content
        assert "terok-xdg-terminal-exec" not in content

    @pytest.mark.parametrize(
        "present",
        [{"ptyxis"}, {"ptyxis", "xdg-terminal-exec"}],
        ids=["only-ptyxis", "ptyxis-and-xdg-terminal-exec"],
    )
    def test_gate_active_routes_through_shim(
        self,
        xdg_data_home: Path,
        present: set[str],
    ) -> None:
        with mock.patch(
            "terok.cli.commands._desktop_entry.shutil.which",
            side_effect=_which_only(present),
        ):
            desktop.install_desktop_entry("/usr/local/bin/terok-tui")
        content = (xdg_data_home / "applications" / "terok.desktop").read_text()
        # Shim path is the bundled resource; assert by suffix to stay
        # site-packages-layout-agnostic.
        assert "Terminal=false" in content
        assert "/terok-xdg-terminal-exec.sh /usr/local/bin/terok-tui" in content
        assert "Exec=/bin/sh " in content
        # ``TryExec`` points at the binary, not the shim, so wheel-installed
        # (non-executable) shims don't cause GNOME to hide the launcher.
        assert "TryExec=/usr/local/bin/terok-tui\n" in content
