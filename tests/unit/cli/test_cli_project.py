# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``terok project`` subcommand group — dispatch + handlers."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from terok.cli.commands.project import (
    _cmd_gate_path,
    _cmd_gate_sync,
    _cmd_presets,
    _cmd_project_delete,
    _cmd_project_derive,
    _cmd_project_list,
    cmd_project_init,
    dispatch,
)

# ---------------------------------------------------------------------------
# Dispatch routing — one case per match arm
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subcommand", "extra_attrs", "target", "expected_kwargs"),
    [
        pytest.param("list", {}, "terok.cli.commands.project._cmd_project_list", {}, id="list"),
        pytest.param(
            "wizard",
            {},
            "terok.cli.commands.project.run_wizard",
            {"needs_init_fn": True},
            id="wizard",
        ),
        pytest.param(
            "derive",
            {"source_id": "src", "new_id": "dst"},
            "terok.cli.commands.project._cmd_project_derive",
            {"positional": ("src", "dst")},
            id="derive",
        ),
        pytest.param(
            "delete",
            {"project_name": "p1", "force": False},
            "terok.cli.commands.project._cmd_project_delete",
            {"positional": ("p1",), "kwargs": {"force": False}},
            id="delete",
        ),
        pytest.param(
            "init",
            {"project_name": "p1"},
            "terok.cli.commands.project.cmd_project_init",
            {"positional": ("p1",)},
            id="init",
        ),
        pytest.param(
            "generate",
            {"project_name": "p1"},
            "terok.cli.commands.project.generate_dockerfiles",
            {"positional": ("p1",)},
            id="generate",
        ),
        pytest.param(
            "build",
            {
                "project_name": "p1",
                "dev": True,
                "refresh_agents": False,
                "full_rebuild": False,
                "agents": None,
            },
            "terok.cli.commands.project.build_images",
            {
                "positional": ("p1",),
                "kwargs": {
                    "include_dev": True,
                    "refresh_agents": False,
                    "full_rebuild": False,
                    "agents": None,
                },
            },
            id="build",
        ),
        pytest.param(
            "ssh-init",
            {"project_name": "p1"},
            "terok.cli.commands.project._cmd_ssh_init",
            {"positional_is_args": True},
            id="ssh-init",
        ),
        pytest.param(
            "gate-path",
            {"project_name": "p1"},
            "terok.cli.commands.project._cmd_gate_path",
            {"positional": ("p1",)},
            id="gate-path",
        ),
        pytest.param(
            "gate-sync",
            {"project_name": "p1"},
            "terok.cli.commands.project._cmd_gate_sync",
            {"positional_is_args": True},
            id="gate-sync",
        ),
        pytest.param(
            "presets",
            {"presets_cmd": "list", "project_name": "p1"},
            "terok.cli.commands.project._cmd_presets",
            {"positional": ("p1",)},
            id="presets-list",
        ),
        pytest.param(
            "agents",
            {"agents_cmd": "set", "project_name": "p1", "selection": "claude,vibe"},
            "terok.cli.commands.project._cmd_agents_set",
            {"positional": ("p1", "claude,vibe")},
            id="agents-set",
        ),
    ],
)
def test_dispatch_routes_to_handler(
    subcommand: str,
    extra_attrs: dict,
    target: str,
    expected_kwargs: dict,
) -> None:
    """Each project subcommand routes to its handler with the right call shape."""
    args = argparse.Namespace(cmd="project", project_cmd=subcommand, **extra_attrs)
    with patch(target) as mock:
        assert dispatch(args) is True

    if expected_kwargs.get("needs_init_fn"):
        # wizard injects `init_fn=cmd_project_init` — pin identity so a
        # refactor that loses the wizard→init linkage fails loudly.
        assert mock.call_args.kwargs.get("init_fn") is cmd_project_init
    elif expected_kwargs.get("positional_is_args"):
        # _cmd_* handlers that take the full Namespace
        mock.assert_called_once_with(args)
    else:
        mock.assert_called_once_with(
            *expected_kwargs.get("positional", ()),
            **expected_kwargs.get("kwargs", {}),
        )


def test_dispatch_ignores_non_project_cmd() -> None:
    """Dispatch returns False for other top-level commands."""
    assert dispatch(argparse.Namespace(cmd="task")) is False


# ---------------------------------------------------------------------------
# _cmd_project_list
# ---------------------------------------------------------------------------


def test_list_empty_prints_placeholder(capsys: pytest.CaptureFixture[str]) -> None:
    """Empty project inventory prints the "No projects found" line."""
    with patch("terok.cli.commands.project.list_projects", return_value=[]):
        _cmd_project_list()
    assert "No projects found" in capsys.readouterr().out


def test_list_prints_each_project(capsys: pytest.CaptureFixture[str]) -> None:
    """Each project renders with id, security_class, upstream, config_root."""
    proj = SimpleNamespace(
        name="myproj",
        description=None,
        security_class="online",
        upstream_url="git@github.com:org/repo.git",
        shared_dir=None,
        root="/home/dev/.config/terok/projects/myproj",
    )
    with patch("terok.cli.commands.project.list_projects", return_value=[proj]):
        _cmd_project_list()

    out = capsys.readouterr().out
    assert "Known projects:" in out
    assert "myproj" in out
    assert "[online]" in out
    assert "git@github.com:org/repo.git" in out


def test_list_formats_shared_dir_hint(capsys: pytest.CaptureFixture[str]) -> None:
    """Projects with a shared_dir show the ``shared=`` segment."""
    proj = SimpleNamespace(
        name="p",
        description=None,
        security_class="online",
        upstream_url=None,
        shared_dir="/shared/x",
        root="/r",
    )
    with patch("terok.cli.commands.project.list_projects", return_value=[proj]):
        _cmd_project_list()
    assert "shared=/shared/x" in capsys.readouterr().out


def test_list_prints_description_when_set(capsys: pytest.CaptureFixture[str]) -> None:
    """A project's optional description renders on its own indented line."""
    proj = SimpleNamespace(
        name="p",
        description="My nice project",
        security_class="online",
        upstream_url=None,
        shared_dir=None,
        root="/r",
    )
    with patch("terok.cli.commands.project.list_projects", return_value=[proj]):
        _cmd_project_list()
    assert "My nice project" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _cmd_project_derive
# ---------------------------------------------------------------------------


def test_derive_calls_downstream_and_offers_edit(capsys: pytest.CaptureFixture[str]) -> None:
    """Derive wires source→target and hands the config path to the edit prompt."""
    fake_project = SimpleNamespace(config=SimpleNamespace(root=MagicMock()))
    fake_project.config.root.__truediv__.return_value = "/projects/beta/project.yml"

    with (
        patch(
            "terok.cli.commands.project.derive_project", return_value=fake_project
        ) as mock_derive,
        patch("terok.cli.commands.project.offer_edit_then_init") as mock_offer,
    ):
        _cmd_project_derive("alpha", "beta")

    mock_derive.assert_called_once_with("alpha", "beta")
    mock_offer.assert_called_once()
    assert "beta" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _cmd_project_delete — branches
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_project_for_delete() -> SimpleNamespace:
    """Minimal project namespace to exercise the delete flow."""
    return SimpleNamespace(
        name="doomed",
        root="/root",
        security_class="online",
        upstream_url=None,
        gate_path="/gate/path",
    )


def _patch_delete_flow(fake_project: SimpleNamespace, *, sharing: list | None = None) -> tuple:
    """Return a chain of patches covering everything _cmd_project_delete touches."""
    return (
        patch("terok.cli.commands.project.load_project", return_value=fake_project),
        patch("terok.cli.commands.project.find_projects_sharing_gate", return_value=sharing or []),
        patch("terok.lib.core.config.archive_dir", return_value="/archive"),
        patch(
            "terok.cli.commands.project.delete_project",
            return_value={"deleted": ["/a"], "skipped": [], "archive": "/archive/doomed"},
        ),
    )


def test_delete_force_skips_prompt(
    fake_project_for_delete: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--force`` suppresses the input() confirmation."""
    patches = _patch_delete_flow(fake_project_for_delete)
    with patches[0], patches[1], patches[2], patches[3] as mock_delete:
        _cmd_project_delete("doomed", force=True)
    mock_delete.assert_called_once_with("doomed")
    assert "deleted" in capsys.readouterr().out


def test_delete_confirm_matches_proceeds(fake_project_for_delete: SimpleNamespace) -> None:
    """Typing the project name at the prompt proceeds with deletion."""
    patches = _patch_delete_flow(fake_project_for_delete)
    with patches[0], patches[1], patches[2], patches[3] as mock_delete:
        with patch("builtins.input", return_value="doomed"):
            _cmd_project_delete("doomed")
    mock_delete.assert_called_once_with("doomed")


def test_delete_confirm_mismatch_cancels(
    fake_project_for_delete: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Any other input cancels the deletion."""
    patches = _patch_delete_flow(fake_project_for_delete)
    with patches[0], patches[1], patches[2], patches[3] as mock_delete:
        with patch("builtins.input", return_value="wrong"):
            _cmd_project_delete("doomed")
    mock_delete.assert_not_called()
    assert "cancelled" in capsys.readouterr().out.lower()


def test_delete_eof_cancels(
    fake_project_for_delete: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """EOF on stdin cancels gracefully with an actionable hint."""
    patches = _patch_delete_flow(fake_project_for_delete)
    with patches[0], patches[1], patches[2], patches[3] as mock_delete:
        with patch("builtins.input", side_effect=EOFError):
            _cmd_project_delete("doomed")
    mock_delete.assert_not_called()
    assert "--force" in capsys.readouterr().out


def test_delete_prints_upstream_when_set(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Projects with an upstream_url surface it in the deletion header."""
    proj = SimpleNamespace(
        name="doomed",
        root="/root",
        security_class="online",
        upstream_url="git@github.com:org/repo.git",
        gate_path="/gate/path",
    )
    with (
        patch("terok.cli.commands.project.load_project", return_value=proj),
        patch("terok.cli.commands.project.find_projects_sharing_gate", return_value=[]),
        patch("terok.lib.core.config.archive_dir", return_value="/archive"),
        patch(
            "terok.cli.commands.project.delete_project",
            return_value={"deleted": [], "skipped": [], "archive": None},
        ),
    ):
        _cmd_project_delete("doomed", force=True)
    assert "Upstream: git@github.com:org/repo.git" in capsys.readouterr().out


def test_delete_shows_shared_gate_note(
    fake_project_for_delete: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Shared gate ownership surfaces as a visible note before deletion."""
    patches = _patch_delete_flow(fake_project_for_delete, sharing=[("other", "/gate/path")])
    with patches[0], patches[1], patches[2], patches[3]:
        _cmd_project_delete("doomed", force=True)
    out = capsys.readouterr().out
    assert "shared with" in out
    assert "other" in out


def test_delete_lists_removed_and_skipped(
    fake_project_for_delete: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The deletion report enumerates both removed paths and skipped reasons."""
    with (
        patch("terok.cli.commands.project.load_project", return_value=fake_project_for_delete),
        patch("terok.cli.commands.project.find_projects_sharing_gate", return_value=[]),
        patch("terok.lib.core.config.archive_dir", return_value="/archive"),
        patch(
            "terok.cli.commands.project.delete_project",
            return_value={
                "deleted": ["/path/a", "/path/b"],
                "skipped": ["in use by task 1"],
                "archive": "/archive/doomed",
            },
        ),
    ):
        _cmd_project_delete("doomed", force=True)
    out = capsys.readouterr().out
    assert "Removed:" in out and "/path/a" in out and "/path/b" in out
    assert "Skipped:" in out and "in use by task 1" in out


# ---------------------------------------------------------------------------
# _cmd_gate_path — file:// URL for IDEs / host git tools
# ---------------------------------------------------------------------------


def test_gate_path_prints_project_gate_file_url(capsys: pytest.CaptureFixture[str]) -> None:
    """Output is the gate mirror as a ``file://`` URL — pipeable into ``git remote add``."""
    from tests.testfs import FAKE_GATE_DIR

    gate_path = FAKE_GATE_DIR / "p1.git"
    fake = MagicMock(gate_path=gate_path)
    with patch("terok.cli.commands.project.load_project", return_value=fake):
        _cmd_gate_path("p1")
    assert capsys.readouterr().out.strip() == gate_path.as_uri()


# ---------------------------------------------------------------------------
# _cmd_gate_sync — success and failure
# ---------------------------------------------------------------------------


def test_gate_sync_success_prints_summary(capsys: pytest.CaptureFixture[str]) -> None:
    """Successful sync prints the resulting gate path and upstream."""
    fake_gate = MagicMock()
    fake_gate.sync.return_value = {
        "success": True,
        "path": "/gate/p1",
        "upstream_url": "https://github.com/org/repo.git",
        "created": False,
        "cache_refreshed": True,
    }
    args = argparse.Namespace(project_name="p1", force_reinit=False)
    with (
        patch(
            "terok.cli.commands.project.load_project",
            return_value=MagicMock(gate_enabled=True),
        ),
        patch("terok.cli.commands.project.make_git_gate", return_value=fake_gate),
    ):
        _cmd_gate_sync(args)

    out = capsys.readouterr().out
    assert "Gate ready at /gate/p1" in out
    assert "clone cache refreshed" in out


def test_gate_sync_renders_remoteless_upstream_label(capsys: pytest.CaptureFixture[str]) -> None:
    """A remoteless (upstream_url=None) gate renders a human hint, not ``None``."""
    fake_gate = MagicMock()
    fake_gate.sync.return_value = {
        "success": True,
        "path": "/gate/scratch",
        "upstream_url": None,
        "created": True,
        "cache_refreshed": False,
    }
    args = argparse.Namespace(project_name="scratch", force_reinit=False)
    with (
        patch(
            "terok.cli.commands.project.load_project",
            return_value=MagicMock(gate_enabled=True),
        ),
        patch("terok.cli.commands.project.make_git_gate", return_value=fake_gate),
    ):
        _cmd_gate_sync(args)

    assert "upstream: (none — local-only bare repo)" in capsys.readouterr().out


def test_gate_sync_failure_raises(capsys: pytest.CaptureFixture[str]) -> None:
    """A failure verdict turns into a SystemExit carrying the error detail."""
    fake_gate = MagicMock()
    fake_gate.sync.return_value = {"success": False, "errors": ["clone timed out"]}
    args = argparse.Namespace(project_name="broken", force_reinit=False)
    with (
        patch(
            "terok.cli.commands.project.load_project",
            return_value=MagicMock(gate_enabled=True),
        ),
        patch("terok.cli.commands.project.make_git_gate", return_value=fake_gate),
        pytest.raises(SystemExit, match="clone timed out"),
    ):
        _cmd_gate_sync(args)


def test_gate_sync_refuses_when_gate_disabled() -> None:
    """``gate.enabled: false`` rejects at the CLI rather than touching sandbox."""
    args = argparse.Namespace(project_name="noproj", force_reinit=False)
    no_gate = MagicMock(gate_enabled=False)
    no_gate.name = "noproj"
    with (
        patch(
            "terok.cli.commands.project.load_project",
            return_value=no_gate,
        ),
        patch("terok.cli.commands.project.make_git_gate") as mock_make,
        pytest.raises(SystemExit, match="gate.enabled: false"),
    ):
        _cmd_gate_sync(args)
    mock_make.assert_not_called()


# ---------------------------------------------------------------------------
# _cmd_presets — empty and populated
# ---------------------------------------------------------------------------


def test_presets_empty_prints_placeholder(capsys: pytest.CaptureFixture[str]) -> None:
    """No presets found → placeholder message."""
    with patch("terok.cli.commands.project.list_presets", return_value=[]):
        _cmd_presets("p1")
    assert "No presets found" in capsys.readouterr().out


def test_presets_populated_prints_each(capsys: pytest.CaptureFixture[str]) -> None:
    """Each preset renders with its name and source."""
    presets = [
        SimpleNamespace(name="solo", source="bundled"),
        SimpleNamespace(name="team", source="user"),
    ]
    with patch("terok.cli.commands.project.list_presets", return_value=presets):
        _cmd_presets("p1")
    out = capsys.readouterr().out
    assert "Presets for 'p1':" in out
    assert "solo (bundled)" in out
    assert "team (user)" in out


# ---------------------------------------------------------------------------
# _cmd_agents_set — validate + write
# ---------------------------------------------------------------------------


def _fake_roster_for_agents() -> SimpleNamespace:
    """Roster stand-in: every selection resolves cleanly."""
    roster = SimpleNamespace(
        agent_names=("claude", "vibe"),
        providers={
            "claude": SimpleNamespace(label="Claude"),
            "vibe": SimpleNamespace(label="Vibe"),
        },
        auth_providers={},
        resolve_selection=lambda _sel: ("claude", "vibe"),
        parse_selection=staticmethod(lambda raw: raw or "all"),
        prompt_selection=lambda: input("[all]: ").strip() or "all",
    )

    def _validate(raw: str) -> None:
        # Late-bind so tests that override ``resolve_selection`` after
        # construction still hit their fake.
        try:
            roster.resolve_selection(roster.parse_selection(raw))
        except ValueError as exc:
            import sys

            print(f"Invalid agent selection: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc

    roster.validate_selection = _validate
    return roster


def test_agents_set_writes_after_validation(capsys: pytest.CaptureFixture[str]) -> None:
    """Valid selection passes validation and reaches the writer."""
    from pathlib import Path

    from terok.cli.commands.project import _cmd_agents_set

    target = Path("/tmp/terok-testing/p1/project.yml")
    with (
        patch(
            "terok.lib.integrations.executor.AgentRoster.shared",
            return_value=_fake_roster_for_agents(),
        ),
        patch(
            "terok.lib.integrations.executor.AgentRoster.parse_selection",
            side_effect=lambda raw: raw,
        ),
        patch(
            "terok.cli.commands.project.set_project_image_agents",
            side_effect=lambda _pid, _sel: target,
        ) as write_mock,
    ):
        _cmd_agents_set("p1", "claude,vibe")
    write_mock.assert_called_once_with("p1", "claude,vibe")
    out = capsys.readouterr().out
    assert "claude,vibe" in out
    assert str(target) in out


def test_agents_set_rejects_unknown_agent(capsys: pytest.CaptureFixture[str]) -> None:
    """``resolve_selection`` raising → SystemExit(2) and no write."""
    from terok.cli.commands.project import _cmd_agents_set

    roster = _fake_roster_for_agents()
    roster.resolve_selection = lambda _sel: (_ for _ in ()).throw(
        ValueError("Unknown roster entries: foo")
    )
    with (
        patch("terok.lib.integrations.executor.AgentRoster.shared", return_value=roster),
        patch(
            "terok.lib.integrations.executor.AgentRoster.parse_selection",
            side_effect=lambda raw: raw,
        ),
        patch("terok.cli.commands.project.set_project_image_agents") as write_mock,
        pytest.raises(SystemExit) as excinfo,
    ):
        _cmd_agents_set("p1", "foo")
    assert excinfo.value.code == 2
    write_mock.assert_not_called()
    assert "Invalid agent selection" in capsys.readouterr().err


def test_agents_set_prompts_when_no_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty input from the interactive picker defaults to ``"all"``."""
    from terok.cli.commands.project import _cmd_agents_set

    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    with (
        patch(
            "terok.lib.integrations.executor.AgentRoster.shared",
            return_value=_fake_roster_for_agents(),
        ),
        patch(
            "terok.lib.integrations.executor.AgentRoster.parse_selection",
            side_effect=lambda raw: raw,
        ),
        patch(
            "terok.cli.commands.project.set_project_image_agents",
            side_effect=lambda _pid, sel: SimpleNamespace(__str__=lambda _self: sel),
        ) as write_mock,
    ):
        _cmd_agents_set("p1", None)
    write_mock.assert_called_once_with("p1", "all")
