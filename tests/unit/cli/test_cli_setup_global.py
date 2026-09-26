# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``terok setup`` — global bootstrap command.

Setup now delegates the service stack (shield + vault + gate + clearance)
to [`terok_executor.ensure_sandbox_ready`][terok_executor.ensure_sandbox_ready]; terok's own phases
shrink to desktop-entry install.  Every service-level assertion now
lives in the executor / sandbox test suites.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from terok_util import SetupCheck, SetupDowngradeError, SetupStatus

from terok.cli.commands.setup import cmd_setup, dispatch


@pytest.fixture(autouse=True)
def _ready_dependencies():
    """Keep command tests independent of real host provisioning."""
    with patch("terok.lib.core.setup.executor", SimpleNamespace(check_setup=Mock(return_value=()))):
        yield


# ── dispatch wiring ──────────────────────────────────────────────────


class TestComponentSubcommands:
    """``terok setup selinux|apparmor`` routes to sandbox's interactive flow."""

    def _dispatch(self, component: str, *, show: bool = False) -> int:
        import argparse

        ns = argparse.Namespace(cmd="setup", component=component, show=show)
        with (
            patch("terok.lib.api.setup.handle_setup_component", return_value=3) as handler,
            patch("terok.cli.commands.setup.tee_output") as tee,
            pytest.raises(SystemExit) as exc_info,
        ):
            dispatch(ns)
        assert tee.call_count == 0, "component flow must bypass the output tee"
        self.handler = handler
        return int(exc_info.value.code)

    def test_component_routes_and_forwards_the_exit_code(self) -> None:
        assert self._dispatch("selinux") == 3
        args, kwargs = self.handler.call_args
        assert args == ("selinux",)
        assert kwargs == {"show_only": False}, (
            "config and the sandbox-live root resolve inside sandbox, from the "
            "same files and env this process reads — nothing to plumb"
        )

    def test_show_flag_is_forwarded(self) -> None:
        self._dispatch("apparmor", show=True)
        assert self.handler.call_args.kwargs["show_only"] is True

    def test_parser_accepts_the_components_and_rejects_others(self) -> None:
        import argparse

        from terok.cli.commands.setup import register

        parser = argparse.ArgumentParser()
        register(parser.add_subparsers(dest="cmd"))
        ns = parser.parse_args(["setup", "selinux", "--show"])
        assert (ns.component, ns.show) == ("selinux", True)
        assert parser.parse_args(["setup"]).component is None
        with pytest.raises(SystemExit):
            parser.parse_args(["setup", "bogus"])

    def test_parser_choices_come_from_the_sandbox_roster(self) -> None:
        """The valid components are sandbox's list, not a terok-local copy."""
        import argparse

        from terok.cli.commands.setup import register
        from terok.lib.api.setup import SETUP_COMPONENTS

        parser = argparse.ArgumentParser()
        register(parser.add_subparsers(dest="cmd"))
        for component in SETUP_COMPONENTS:
            assert parser.parse_args(["setup", component]).component == component

    def test_show_without_component_never_runs_the_full_setup(self) -> None:
        """``terok setup --show`` routes to the flow, which answers it — not to a host install."""
        import argparse

        ns = argparse.Namespace(cmd="setup", component=None, show=True)
        with (
            patch("terok.cli.commands.setup.cmd_setup") as full_setup,
            patch(
                "terok.lib.api.setup.handle_setup_component",
                side_effect=SystemExit("--show needs a component: terok setup <selinux|apparmor>"),
            ) as handler,
            pytest.raises(SystemExit) as exc_info,
        ):
            dispatch(ns)
        full_setup.assert_not_called()
        handler.assert_called_once_with(None, show_only=True)
        assert "needs a component" in str(exc_info.value.code)

    def test_full_setup_flags_are_rejected_with_a_component(self) -> None:
        """Flags the component flow cannot honour error out, never vanish."""
        import argparse

        ns = argparse.Namespace(
            cmd="setup", component="selinux", show=False, passphrase_tier="keyring"
        )
        with (
            patch("terok.lib.api.setup.handle_setup_component") as handler,
            pytest.raises(SystemExit) as exc_info,
        ):
            dispatch(ns)
        handler.assert_not_called()
        assert "--passphrase-tier" in str(exc_info.value.code)


def test_dispatch_returns_false_for_other_cmds() -> None:
    import argparse

    ns = argparse.Namespace(cmd="not-setup")
    assert dispatch(ns) is False


def test_dispatch_invokes_cmd_setup_with_flag() -> None:
    import argparse

    ns = argparse.Namespace(
        cmd="setup",
        no_desktop_entry=True,
        install_desktop_entry=False,
        with_images=None,
        family=None,
    )
    with patch("terok.cli.commands.setup.cmd_setup") as mock:
        assert dispatch(ns) is True
    mock.assert_called_once_with(
        no_desktop_entry=True,
        install_desktop_entry=False,
        with_images=None,
        family=None,
        passphrase_tier=None,
    )


def test_dispatch_forwards_install_desktop_entry() -> None:
    """``--install-desktop-entry`` travels through the dispatcher as a kwarg."""
    import argparse

    ns = argparse.Namespace(
        cmd="setup",
        no_desktop_entry=False,
        install_desktop_entry=True,
        with_images=None,
        family=None,
    )
    with patch("terok.cli.commands.setup.cmd_setup") as mock:
        dispatch(ns)
    mock.assert_called_once_with(
        no_desktop_entry=False,
        install_desktop_entry=True,
        with_images=None,
        family=None,
        passphrase_tier=None,
    )


def test_dispatch_forwards_with_images_and_family() -> None:
    """``--with-images`` + ``--family`` travel through the dispatcher as kwargs."""
    import argparse

    ns = argparse.Namespace(
        cmd="setup",
        no_desktop_entry=False,
        install_desktop_entry=False,
        with_images="fedora:43",
        family="rpm",
    )
    with patch("terok.cli.commands.setup.cmd_setup") as mock:
        dispatch(ns)
    mock.assert_called_once_with(
        no_desktop_entry=False,
        install_desktop_entry=False,
        with_images="fedora:43",
        family="rpm",
        passphrase_tier=None,
    )


# ── cmd_setup orchestration ──────────────────────────────────────────


class TestCmdSetup:
    """``cmd_setup`` runs sandbox-ready + desktop-entry by default; images are opt-in."""

    def test_default_skips_image_build(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Normal ``terok setup`` doesn't touch the image factory.

        Base images are a per-project decision (each ``project.yml``
        declares its own ``image.base_image``); at host-setup time
        there's nothing sensible to pre-build.  L0/L1 materialises
        lazily on first ``terok task run`` / ``terok project init``.
        """
        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready") as sandbox,
            patch("terok_executor.container.build.build_base_images") as images,
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True) as desktop,
            patch("terok.cli.commands.setup._ensure_shell_completions"),
        ):
            cmd_setup()
        sandbox.assert_called_once()
        images.assert_not_called()
        desktop.assert_called_once()
        assert "Setup complete" in capsys.readouterr().out

    def test_no_desktop_entry_resolves_to_skip_policy(self) -> None:
        """``--no-desktop-entry`` resolves to ``policy="skip"`` for ``_ensure_desktop_entry``.

        The phase is still invoked — the silent-skip branch lives inside
        ``_ensure_desktop_entry`` so the call site stays uniform — but
        the resolved policy is ``"skip"``.
        """
        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready"),
            patch("terok_executor.container.build.build_base_images"),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True) as desktop,
            patch("terok.cli.commands.setup._ensure_shell_completions"),
        ):
            cmd_setup(no_desktop_entry=True)
        desktop.assert_called_once_with(policy="skip")
        from terok.lib.core.setup import check_setup

        assert check_setup()[0].status is SetupStatus.READY

    def test_install_desktop_entry_resolves_to_install_policy(self) -> None:
        """``--install-desktop-entry`` resolves to ``policy="install"``."""
        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready"),
            patch("terok_executor.container.build.build_base_images"),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True) as desktop,
            patch("terok.cli.commands.setup._ensure_shell_completions"),
        ):
            cmd_setup(install_desktop_entry=True)
        desktop.assert_called_once_with(policy="install")

    def test_default_policy_comes_from_config(self) -> None:
        """Without CLI flags, the policy comes from ``tui.desktop_entry`` (default ``auto``)."""
        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready"),
            patch("terok_executor.container.build.build_base_images"),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True) as desktop,
            patch(
                "terok.lib.core.config.get_tui_desktop_entry",
                return_value="install",
            ),
            patch("terok.cli.commands.setup._ensure_shell_completions"),
        ):
            cmd_setup()
        desktop.assert_called_once_with(policy="install")

    def test_with_images_builds_requested_base(self) -> None:
        """``--with-images=ubuntu:24.04`` triggers the factory with that base + auto-detected family."""
        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready"),
            patch("terok_executor.container.build.build_base_images") as images,
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
            patch("terok.cli.commands.setup._ensure_shell_completions"),
        ):
            cmd_setup(with_images="ubuntu:24.04")
        images.assert_called_once()
        assert images.call_args.args[0] == "ubuntu:24.04"
        assert images.call_args.kwargs["family"] is None

    def test_with_images_plus_family_override(self) -> None:
        """``--family`` overrides auto-detection when paired with ``--with-images``."""
        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready"),
            patch("terok_executor.container.build.build_base_images") as images,
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
            patch("terok.cli.commands.setup._ensure_shell_completions"),
        ):
            cmd_setup(with_images="my-registry.example.com/odd-base:1.0", family="rpm")
        images.assert_called_once()
        assert images.call_args.args[0] == "my-registry.example.com/odd-base:1.0"
        assert images.call_args.kwargs["family"] == "rpm"

    def test_image_build_error_exits_nonzero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A ``BuildError`` from the factory surfaces as a FAIL stage line and exit 1."""
        from terok_executor import BuildError

        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready"),
            patch(
                "terok_executor.container.build.build_base_images",
                side_effect=BuildError("dockerfile parse error"),
            ),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
        ):
            with pytest.raises(SystemExit) as exc:
                cmd_setup(with_images="ubuntu:24.04")
        assert exc.value.code == 1
        assert "Image build failed" in capsys.readouterr().out

    def test_sandbox_failure_skips_requested_image_phase(self) -> None:
        """``--with-images`` is still suppressed when the service stack is broken.

        No point burning minutes on L0/L1 against a host that can't
        yet mount it; the user needs to fix the sandbox install first.
        """
        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready", side_effect=SystemExit(1)),
            patch("terok_executor.container.build.build_base_images") as images,
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
        ):
            with pytest.raises(SystemExit):
                cmd_setup(with_images="ubuntu:24.04")
        images.assert_not_called()

    def test_sandbox_failure_exits_nonzero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``ensure_sandbox_ready`` raising ``SystemExit`` is reported + propagates exit 1."""
        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready", side_effect=SystemExit(1)),
            patch("terok_executor.container.build.build_base_images"),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
        ):
            with pytest.raises(SystemExit) as exc:
                cmd_setup()
        assert exc.value.code == 1
        assert "Setup failed" in capsys.readouterr().out

    def test_sandbox_failure_prevents_upper_writes(self) -> None:
        """A failed dependency never permits desktop writes or upper certification."""
        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready", side_effect=SystemExit(1)),
            patch("terok.cli.commands.setup._ensure_desktop_entry") as desktop,
            patch("terok.cli.commands.setup._ensure_shell_completions") as completions,
            patch("terok.lib.api.setup.invalidate_setup") as invalidate,
            patch("terok.lib.api.setup.complete_setup") as complete,
            pytest.raises(SystemExit),
        ):
            cmd_setup()
        desktop.assert_not_called()
        completions.assert_not_called()
        invalidate.assert_not_called()
        complete.assert_not_called()

    def test_desktop_failure_prevents_receipt(self, capsys: pytest.CaptureFixture[str]) -> None:
        """An explicitly requested desktop install must succeed before certification."""
        from terok.lib.core.setup import _receipt

        _receipt().write()
        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready"),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=False),
            patch("terok.cli.commands.setup._ensure_shell_completions") as completions,
            pytest.raises(SystemExit) as exc,
        ):
            cmd_setup(install_desktop_entry=True)
        assert exc.value.code == 1
        assert "reported errors" in capsys.readouterr().out
        assert _receipt().check().status is SetupStatus.MISSING
        completions.assert_not_called()


# ── Desktop entry phase ──────────────────────────────────────────────


class TestEnsureDesktopEntry:
    """The one phase terok still owns: XDG ``.desktop`` + icon install."""

    def test_skip_policy_emits_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``policy="skip"`` returns True silently — no stage line, no install call."""
        from terok.cli.commands.setup import _ensure_desktop_entry

        with patch("terok.cli.commands._desktop_entry.install_desktop_entry") as do_install:
            assert _ensure_desktop_entry(policy="skip") is True
        do_install.assert_not_called()
        assert capsys.readouterr().out == ""

    def test_auto_without_xdg_utils_warns_with_hints(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``auto`` + missing xdg-utils → WARN that names both escape hatches."""
        from terok.cli.commands.setup import _ensure_desktop_entry

        with (
            patch(
                "terok.cli.commands._desktop_entry.xdg_utils_available",
                return_value=False,
            ),
            patch("terok.cli.commands._desktop_entry.install_desktop_entry") as do_install,
        ):
            assert _ensure_desktop_entry(policy="auto") is True
        do_install.assert_not_called()
        out = capsys.readouterr().out
        assert "WARN" in out
        assert "--install-desktop-entry" in out
        assert "tui.desktop_entry: skip" in out

    def test_auto_with_xdg_utils_installs(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``auto`` + xdg-utils present → install via xdg-utils, OK stage line."""
        from terok.cli.commands._desktop_entry import DesktopBackend
        from terok.cli.commands.setup import _ensure_desktop_entry

        with (
            patch(
                "terok.cli.commands._desktop_entry.xdg_utils_available",
                return_value=True,
            ),
            patch("terok.cli.commands.setup.find_host_tool", return_value="/usr/bin/terok-tui"),
            patch(
                "terok.cli.commands._desktop_entry.install_desktop_entry",
                return_value=DesktopBackend.XDG_UTILS,
            ) as do_install,
        ):
            assert _ensure_desktop_entry(policy="auto") is True
        do_install.assert_called_once()
        assert "ok" in capsys.readouterr().out

    def test_install_uses_fallback_when_xdg_utils_missing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``install`` always installs — fallback backend when xdg-utils is missing."""
        from terok.cli.commands._desktop_entry import DesktopBackend
        from terok.cli.commands.setup import _ensure_desktop_entry

        with (
            patch(
                "terok.cli.commands._desktop_entry.xdg_utils_available",
                return_value=False,
            ),
            patch("terok.cli.commands.setup.find_host_tool", return_value="/usr/bin/terok-tui"),
            patch(
                "terok.cli.commands._desktop_entry.install_desktop_entry",
                return_value=DesktopBackend.FALLBACK,
            ) as do_install,
        ):
            assert _ensure_desktop_entry(policy="install") is True
        do_install.assert_called_once()
        assert "WARN" in capsys.readouterr().out

    def test_install_raises_reports_fail(self, capsys: pytest.CaptureFixture[str]) -> None:
        from terok.cli.commands.setup import _ensure_desktop_entry

        with (
            patch("terok.cli.commands.setup.find_host_tool", return_value="/usr/bin/terok-tui"),
            patch(
                "terok.cli.commands._desktop_entry.install_desktop_entry",
                side_effect=PermissionError("read-only xdg dir"),
            ),
        ):
            assert _ensure_desktop_entry(policy="install") is False
        assert "FAIL" in capsys.readouterr().out

    def test_missing_binary_falls_back_to_bare_name(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """pipx install → terok-tui may not be on PATH at setup time; install the unit anyway."""
        from terok.cli.commands._desktop_entry import DesktopBackend
        from terok.cli.commands.setup import _ensure_desktop_entry

        captured_bin_path: list[str] = []

        def _record(bin_path: str) -> DesktopBackend:
            captured_bin_path.append(bin_path)
            return DesktopBackend.XDG_UTILS

        with (
            patch("terok.cli.commands.setup.find_host_tool", return_value=None),
            patch(
                "terok.cli.commands._desktop_entry.install_desktop_entry",
                side_effect=_record,
            ),
        ):
            _ensure_desktop_entry(policy="install")
        assert captured_bin_path == ["terok-tui"]


class TestResolveDesktopPolicy:
    """``--no-desktop-entry`` / ``--install-desktop-entry`` win over the config key."""

    def test_no_flags_returns_config(self) -> None:
        from terok.cli.commands.setup import _resolve_desktop_policy

        with patch(
            "terok.lib.core.config.get_tui_desktop_entry",
            return_value="auto",
        ):
            assert (
                _resolve_desktop_policy(no_desktop_entry=False, install_desktop_entry=False)
                == "auto"
            )

    def test_no_desktop_entry_overrides_config(self) -> None:
        from terok.cli.commands.setup import _resolve_desktop_policy

        with patch(
            "terok.lib.core.config.get_tui_desktop_entry",
            return_value="install",
        ):
            assert (
                _resolve_desktop_policy(no_desktop_entry=True, install_desktop_entry=False)
                == "skip"
            )

    def test_install_desktop_entry_overrides_config(self) -> None:
        from terok.cli.commands.setup import _resolve_desktop_policy

        with patch(
            "terok.lib.core.config.get_tui_desktop_entry",
            return_value="skip",
        ):
            assert (
                _resolve_desktop_policy(no_desktop_entry=False, install_desktop_entry=True)
                == "install"
            )


class TestCmdSetupManualStepExitCode:
    """The aggregator's numeric exit code survives to terok's own exit.

    ``EXIT_MANUAL_STEP_NEEDED`` (e.g. the SELinux policy) is what the
    TUI keys its remediation offer on; collapsing every failure to
    exit 1 made that branch unreachable through ``terok setup``.
    """

    def test_manual_step_code_is_forwarded(self) -> None:
        from terok.lib.api.setup import EXIT_MANUAL_STEP_NEEDED

        with (
            patch(
                "terok.lib.api.agents.ensure_sandbox_ready",
                side_effect=SystemExit(EXIT_MANUAL_STEP_NEEDED),
            ),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
            patch("terok.cli.commands.setup._ensure_shell_completions"),
            pytest.raises(SystemExit) as exc_info,
        ):
            cmd_setup()
        assert exc_info.value.code == EXIT_MANUAL_STEP_NEEDED

    def test_manual_step_defers_upper_setup_and_requested_images(self) -> None:
        """Incomplete lower setup is repaired before any upper setup work."""
        from terok.lib.api.setup import EXIT_MANUAL_STEP_NEEDED

        with (
            patch(
                "terok.lib.api.agents.ensure_sandbox_ready",
                side_effect=SystemExit(EXIT_MANUAL_STEP_NEEDED),
            ),
            patch("terok.cli.commands.setup._run_image_build") as build,
            patch("terok.cli.commands.setup._ensure_desktop_entry") as desktop,
            patch("terok.lib.api.setup.complete_setup") as complete,
            pytest.raises(SystemExit),
        ):
            cmd_setup(with_images="fedora:44")
        build.assert_not_called()
        desktop.assert_not_called()
        complete.assert_not_called()

    def test_manual_step_reads_as_partial_success_not_failure(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Every service phase passed, so the summary must not say they failed."""
        from terok.lib.api.setup import EXIT_MANUAL_STEP_NEEDED

        with (
            patch(
                "terok.lib.api.agents.ensure_sandbox_ready",
                side_effect=SystemExit(EXIT_MANUAL_STEP_NEEDED),
            ),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
            patch("terok.cli.commands.setup._ensure_shell_completions"),
            pytest.raises(SystemExit),
        ):
            cmd_setup()
        out = capsys.readouterr().out
        assert "one manual host step" in out
        assert "Setup failed" not in out

    def test_falsy_exit_code_still_fails_with_one(self) -> None:
        """A raised SystemExit(0) is a failure — the old nonzero invariant holds."""
        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready", side_effect=SystemExit(0)),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
            patch("terok.cli.commands.setup._ensure_shell_completions"),
            pytest.raises(SystemExit) as exc_info,
        ):
            cmd_setup()
        assert exc_info.value.code == 1


class TestCmdSetupStringExitCode:
    """A string SystemExit from the aggregator prints as lines, not inside '(exit …)'."""

    def test_string_code_prints_verbatim(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The multi-line refusal hint lands on its own lines with a plain banner after."""
        hint = "setup: no passphrase tier was chosen.\n  pick one explicitly"
        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready", side_effect=SystemExit(hint)),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
            patch("terok.cli.commands.setup._ensure_shell_completions"),
            pytest.raises(SystemExit),
        ):
            cmd_setup()
        out = capsys.readouterr().out
        assert hint in out
        assert "Sandbox aggregator reported failures." in out
        assert "(exit setup:" not in out

    def test_numeric_code_keeps_exit_suffix(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The numeric-code path is unchanged — '(exit N)' stays."""
        with (
            patch("terok.lib.api.agents.ensure_sandbox_ready", side_effect=SystemExit(2)),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
            patch("terok.cli.commands.setup._ensure_shell_completions"),
            pytest.raises(SystemExit),
        ):
            cmd_setup()
        assert "Sandbox aggregator reported failures (exit 2)." in capsys.readouterr().out


class TestSetupOutputPersistence:
    """``setup`` runs under the output-capture tee (terok#1188)."""

    def test_downgrade_precedes_even_the_output_tee(self) -> None:
        """A lower downgrade cannot create a setup log, routes, or desktop artifacts."""
        import argparse

        checks = [SetupCheck("child", "receipt", SetupStatus.DOWNGRADE)]
        with (
            patch("terok.lib.core.setup.check_setup", return_value=checks),
            patch("terok.cli.commands.setup.tee_output") as tee,
            patch("terok.cli.commands.setup.cmd_setup") as command,
            pytest.raises(SetupDowngradeError),
        ):
            dispatch(argparse.Namespace(cmd="setup"))
        tee.assert_not_called()
        command.assert_not_called()

    def test_direct_setup_preflights_before_child_writes(self) -> None:
        """Direct callers receive the same closure preflight as the CLI dispatcher."""
        checks = [SetupCheck("child", "receipt", SetupStatus.DOWNGRADE)]
        with (
            patch("terok.lib.core.setup.check_setup", return_value=checks),
            patch("terok.lib.api.agents.ensure_sandbox_ready") as dependency,
            patch("terok.lib.api.setup.invalidate_setup") as invalidate,
            pytest.raises(SetupDowngradeError),
        ):
            cmd_setup()
        dependency.assert_not_called()
        invalidate.assert_not_called()

    def test_setup_dispatch_tees_its_output(self) -> None:
        import argparse

        from terok.cli.commands.setup import dispatch

        args = argparse.Namespace(cmd="setup")
        with (
            patch("terok.cli.commands.setup.tee_output") as tee,
            patch("terok.cli.commands.setup.cmd_setup"),
        ):
            assert dispatch(args) is True
        tee.assert_called_once_with("setup")
