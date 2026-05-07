# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for image CLI commands."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from terok.cli.commands.image import _cmd_cleanup, _cmd_list
from terok.lib.domain.image_cleanup import CleanupResult, ImageInfo

IMAGES = [
    ImageInfo("terok-l0", "ubuntu-24.04", "sha256:aaa", "500MB", "2 days ago"),
    ImageInfo("myproj", "l2-cli", "sha256:bbb", "1.5GB", "1 day ago"),
]


def assert_output_contains(output: str, expected_lines: list[str]) -> None:
    """Assert that all expected lines appear in the command output."""
    for expected in expected_lines:
        assert expected in output


@pytest.mark.parametrize(
    ("images", "expected_lines"),
    [
        ([], ["No terok images found"]),
        (IMAGES, ["terok-l0:ubuntu-24.04", "myproj:l2-cli", "2 image(s)"]),
    ],
    ids=["empty", "with-images"],
)
def test_cmd_list_outputs_expected(
    images: list[ImageInfo],
    expected_lines: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("terok.cli.commands.image.list_images", return_value=images):
        _cmd_list(None)
    assert_output_contains(capsys.readouterr().out, expected_lines)


@pytest.mark.parametrize(
    ("result", "expected_lines"),
    [
        (CleanupResult(removed=[], failed=[], dry_run=False), ["No orphaned terok images found"]),
        (
            CleanupResult(removed=["old-proj:l2-cli"], failed=[], dry_run=True),
            ["Would remove", "1 image(s) would be removed"],
        ),
        (
            CleanupResult(
                removed=["old-proj:l2-cli"],
                failed=["in-use-proj:l2-cli"],
                dry_run=False,
            ),
            ["Removed", "Failed", "1 failed"],
        ),
    ],
    ids=["nothing-to-clean", "dry-run", "with-failures"],
)
def test_cmd_cleanup_outputs_expected(
    result: CleanupResult,
    expected_lines: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("terok.cli.commands.image.cleanup_images", return_value=result):
        _cmd_cleanup(dry_run=result.dry_run)
    assert_output_contains(capsys.readouterr().out, expected_lines)


class TestImageDispatch:
    """The ``image`` group routes list, cleanup, and usage subcommands correctly.

    (``usage`` dispatch is also exercised through its render pipelines in
    ``test_cli_image_usage.py``; the minimal routing check lives here so all
    three match-arms are visible in one place.)
    """

    def test_ignores_non_image(self) -> None:
        """Dispatch returns False for commands outside the image group."""
        import argparse

        from terok.cli.commands.image import dispatch

        assert dispatch(argparse.Namespace(cmd="task")) is False

    def test_list_invokes_handler_with_project_filter(self) -> None:
        """``image list <project>`` forwards the project filter."""
        import argparse

        from terok.cli.commands.image import dispatch

        args = argparse.Namespace(cmd="image", image_cmd="list", project_id="myproj")
        with patch("terok.cli.commands.image._cmd_list") as mock:
            assert dispatch(args) is True
        mock.assert_called_once_with("myproj")

    def test_list_without_project_defaults_to_none(self) -> None:
        """``image list`` with no positional passes None through."""
        import argparse

        from terok.cli.commands.image import dispatch

        args = argparse.Namespace(cmd="image", image_cmd="list")
        with patch("terok.cli.commands.image._cmd_list") as mock:
            assert dispatch(args) is True
        mock.assert_called_once_with(None)

    def test_cleanup_forwards_dry_run_flag(self) -> None:
        """``image cleanup --dry-run`` forwards the flag."""
        import argparse

        from terok.cli.commands.image import dispatch

        args = argparse.Namespace(cmd="image", image_cmd="cleanup", dry_run=True)
        with patch("terok.cli.commands.image._cmd_cleanup") as mock:
            assert dispatch(args) is True
        mock.assert_called_once_with(dry_run=True)

    def test_usage_forwards_project_and_json_flags(self) -> None:
        """``image usage --project X --json`` routes to the usage helper."""
        import argparse

        from terok.cli.commands.image import dispatch

        args = argparse.Namespace(
            cmd="image", image_cmd="usage", project="myproj", json_output=True
        )
        with patch("terok.cli.commands.image._cmd_usage") as mock:
            assert dispatch(args) is True
        mock.assert_called_once_with(project_id="myproj", json_output=True)

    def test_build_dispatch_forwards_flags(self) -> None:
        """``image build --rebuild --sidecar`` routes to the build helper."""
        import argparse

        from terok.cli.commands.image import dispatch

        args = argparse.Namespace(
            cmd="image",
            image_cmd="build",
            base=None,
            agents=None,
            family=None,
            rebuild=True,
            full_rebuild=False,
            sidecar=True,
        )
        with patch("terok.cli.commands.image._cmd_build") as mock:
            assert dispatch(args) is True
        mock.assert_called_once_with(
            base=None,
            agents=None,
            family=None,
            rebuild=True,
            full_rebuild=False,
            sidecar=True,
        )


class TestCmdBuild:
    """``_cmd_build`` reads config defaults and delegates to the executor primitive."""

    def test_uses_config_defaults_when_no_overrides(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from unittest.mock import MagicMock, sentinel

        from terok.cli.commands.image import _cmd_build

        fake_images = MagicMock(l0="terok-l0:fedora-43", l1="terok-l1-cli:fedora-43")
        # Sentinel return value verifies _cmd_build forwards parse_agent_selection's
        # output verbatim to build_base_images — a passthrough lambda would let an
        # accidental "use the raw input string" regression sneak past.
        with (
            patch(
                "terok.lib.core.config.get_global_image_base_image",
                return_value="fedora:43",
            ),
            patch("terok.lib.core.config.get_global_image_agents", return_value="all"),
            patch(
                "terok_executor.parse_agent_selection",
                return_value=sentinel.RESOLVED_AGENTS,
            ) as mock_parse,
            patch("terok_executor.build_base_images", return_value=fake_images) as mock_build,
            patch("terok_executor.build_sidecar_image"),
        ):
            _cmd_build(
                base=None,
                agents=None,
                family=None,
                rebuild=False,
                full_rebuild=False,
                sidecar=False,
            )

        mock_parse.assert_called_once_with("all")
        kwargs = mock_build.call_args.kwargs
        assert kwargs["base_image"] == "fedora:43"
        assert kwargs["agents"] is sentinel.RESOLVED_AGENTS
        assert kwargs["tag_as_default"] is True
        out = capsys.readouterr().out
        assert "terok-l0:fedora-43" in out
        assert "terok-l1-cli:fedora-43" in out

    def test_overrides_take_precedence_over_config(self) -> None:
        from unittest.mock import MagicMock, sentinel

        from terok.cli.commands.image import _cmd_build

        fake_images = MagicMock(l0="terok-l0:test", l1="terok-l1-cli:test")
        with (
            patch(
                "terok.lib.core.config.get_global_image_base_image",
                return_value="fedora:43",  # would normally be picked
            ),
            patch("terok.lib.core.config.get_global_image_agents", return_value="all"),
            patch(
                "terok_executor.parse_agent_selection",
                return_value=sentinel.RESOLVED_AGENTS,
            ) as mock_parse,
            patch("terok_executor.build_base_images", return_value=fake_images) as mock_build,
            patch("terok_executor.build_sidecar_image"),
        ):
            _cmd_build(
                base="ubuntu:24.04",
                agents="claude,codex",
                family="deb",
                rebuild=True,
                full_rebuild=False,
                sidecar=False,
            )

        # Overrides reach parse_agent_selection (config default is bypassed) ...
        mock_parse.assert_called_once_with("claude,codex")
        kwargs = mock_build.call_args.kwargs
        assert kwargs["base_image"] == "ubuntu:24.04"
        # ... and parse_agent_selection's output makes it through to build_base_images.
        assert kwargs["agents"] is sentinel.RESOLVED_AGENTS
        assert kwargs["family"] == "deb"
        assert kwargs["rebuild"] is True

    def test_sidecar_flag_triggers_sidecar_build(self, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import MagicMock

        from terok.cli.commands.image import _cmd_build

        fake_images = MagicMock(l0="L0", l1="L1")
        with (
            patch(
                "terok.lib.core.config.get_global_image_base_image",
                return_value="ubuntu:24.04",
            ),
            patch("terok.lib.core.config.get_global_image_agents", return_value="all"),
            patch("terok_executor.parse_agent_selection", side_effect=lambda v: v),
            patch("terok_executor.build_base_images", return_value=fake_images),
            patch(
                "terok_executor.build_sidecar_image",
                return_value="terok-l1-sidecar:ubuntu-24.04",
            ) as mock_sidecar,
        ):
            _cmd_build(
                base=None,
                agents=None,
                family=None,
                rebuild=False,
                full_rebuild=False,
                sidecar=True,
            )

        mock_sidecar.assert_called_once()
        assert "terok-l1-sidecar:ubuntu-24.04" in capsys.readouterr().out

    def test_build_error_exits_cleanly(self) -> None:
        from terok_executor import BuildError

        from terok.cli.commands.image import _cmd_build

        with (
            patch(
                "terok.lib.core.config.get_global_image_base_image",
                return_value="ubuntu:24.04",
            ),
            patch("terok.lib.core.config.get_global_image_agents", return_value="all"),
            patch("terok_executor.parse_agent_selection", side_effect=lambda v: v),
            patch(
                "terok_executor.build_base_images",
                side_effect=BuildError("podman missing"),
            ),
        ):
            with pytest.raises(SystemExit, match="podman missing"):
                _cmd_build(
                    base=None,
                    agents=None,
                    family=None,
                    rebuild=False,
                    full_rebuild=False,
                    sidecar=False,
                )
