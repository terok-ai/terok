# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``terok agents`` CLI subcommand group.

Verbs are :code:`list` and :code:`set` — the dispatcher routes via the
``agents_cmd`` slot the subparser fills.  ``set`` validates against the
roster before delegating the write to the executor's adapter.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from terok.cli.commands import agents


def _ns_list(*, all_flag: bool = False) -> argparse.Namespace:
    """Namespace shaped as ``terok agents list`` post-parse."""
    return argparse.Namespace(cmd="agents", agents_cmd="list", **{"all": all_flag})


def _ns_set(selection: str | None = None) -> argparse.Namespace:
    """Namespace shaped as ``terok agents set [SELECTION]`` post-parse."""
    return argparse.Namespace(cmd="agents", agents_cmd="set", selection=selection)


def _fake_roster(
    *,
    agent_names: tuple[str, ...] = ("claude", "codex"),
    all_names: tuple[str, ...] = ("claude", "codex", "gh"),
    labels: dict[str, str] | None = None,
) -> SimpleNamespace:
    """Stand-in for [`AgentRoster`][terok.lib.integrations.executor.AgentRoster] reading what the dispatcher needs."""
    if labels is None:
        labels = {"claude": "Anthropic Claude", "codex": "OpenAI Codex", "gh": "GitHub CLI"}
    providers = {name: SimpleNamespace(label=labels.get(name, name)) for name in all_names}
    auth_providers: dict[str, SimpleNamespace] = {}

    def _resolve(_selection: object) -> tuple[str, ...]:
        return agent_names

    return SimpleNamespace(
        agent_names=agent_names,
        all_names=all_names,
        providers=providers,
        auth_providers=auth_providers,
        resolve_selection=_resolve,
    )


# ── dispatcher routing ────────────────────────────────────────────────


def test_dispatch_returns_false_for_other_cmds() -> None:
    """The dispatcher must let unrelated commands fall through."""
    assert agents.dispatch(argparse.Namespace(cmd="not-agents")) is False


def test_dispatch_bare_agents_prints_group_help(capsys: pytest.CaptureFixture[str]) -> None:
    """``terok agents`` with no subverb prints the group's usage to stderr."""
    ns = argparse.Namespace(cmd="agents", agents_cmd=None)
    assert agents.dispatch(ns) is True
    err = capsys.readouterr().err
    assert "list" in err
    assert "set" in err


# ── list ──────────────────────────────────────────────────────────────


def test_list_prints_agents_only_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    """Default invocation prints only agent rows, not tool entries."""
    with patch("terok.lib.api.agents.get_roster", return_value=_fake_roster()):
        assert agents.dispatch(_ns_list()) is True
    out = capsys.readouterr().out
    assert "claude" in out
    assert "codex" in out
    assert "gh" not in out  # tool entry hidden behind --all


def test_list_includes_tool_entries_with_all_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """``--all`` widens the listing to include tool / sidecar entries."""
    with patch("terok.lib.api.agents.get_roster", return_value=_fake_roster()):
        agents.dispatch(_ns_list(all_flag=True))
    out = capsys.readouterr().out
    assert "claude" in out
    assert "gh" in out


def test_list_renders_label_alongside_name(capsys: pytest.CaptureFixture[str]) -> None:
    """The output table carries each agent's human-readable label."""
    with patch("terok.lib.api.agents.get_roster", return_value=_fake_roster()):
        agents.dispatch(_ns_list())
    out = capsys.readouterr().out
    assert "Anthropic Claude" in out
    assert "OpenAI Codex" in out


def test_list_handles_empty_roster(capsys: pytest.CaptureFixture[str]) -> None:
    """An empty roster prints an explanatory line on stderr instead of an empty table."""
    empty = _fake_roster(agent_names=(), all_names=())
    with patch("terok.lib.api.agents.get_roster", return_value=empty):
        assert agents.dispatch(_ns_list()) is True
    err = capsys.readouterr().err
    assert "No agents registered" in err


def test_list_falls_back_to_auth_provider_label(capsys: pytest.CaptureFixture[str]) -> None:
    """An agent missing from ``providers`` still renders via ``auth_providers``."""
    roster = _fake_roster(agent_names=("kisski",), all_names=("kisski",), labels={})
    roster.providers = {}
    roster.auth_providers = {"kisski": SimpleNamespace(label="KISSKI AcademicCloud")}
    with patch("terok.lib.api.agents.get_roster", return_value=roster):
        agents.dispatch(_ns_list())
    assert "KISSKI AcademicCloud" in capsys.readouterr().out


def test_list_falls_back_to_name_when_no_label(capsys: pytest.CaptureFixture[str]) -> None:
    """An agent in neither providers nor auth_providers shows the bare name as label."""
    roster = _fake_roster(agent_names=("nolabel",), all_names=("nolabel",), labels={})
    roster.providers = {}
    roster.auth_providers = {}
    with patch("terok.lib.api.agents.get_roster", return_value=roster):
        agents.dispatch(_ns_list())
    assert "nolabel" in capsys.readouterr().out


# ── set ───────────────────────────────────────────────────────────────


def test_set_writes_selection_after_validation(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A valid selection passes through validation and lands in config.yml."""
    target = tmp_path / "config.yml"
    with (
        patch("terok.lib.api.agents.get_roster", return_value=_fake_roster()),
        patch(
            "terok.lib.api.agents.parse_agent_selection",
            side_effect=lambda raw: raw,
        ),
        patch(
            "terok.lib.api.agents.set_global_image_agents",
            side_effect=lambda raw: target,
        ) as write_mock,
    ):
        assert agents.dispatch(_ns_set("claude,codex")) is True
    write_mock.assert_called_once_with("claude,codex")
    out = capsys.readouterr().out
    assert "claude,codex" in out
    assert str(target) in out


def test_set_rejects_unknown_agent(capsys: pytest.CaptureFixture[str]) -> None:
    """``resolve_selection`` raising ValueError → SystemExit(2), nothing written."""
    roster = _fake_roster()
    roster.resolve_selection = lambda _sel: (_ for _ in ()).throw(
        ValueError("Unknown roster entries: foo"),
    )
    with (
        patch("terok.lib.api.agents.get_roster", return_value=roster),
        patch(
            "terok.lib.api.agents.parse_agent_selection",
            side_effect=lambda raw: raw,
        ),
        patch("terok.lib.api.agents.set_global_image_agents") as write_mock,
        pytest.raises(SystemExit) as excinfo,
    ):
        agents.dispatch(_ns_set("foo"))
    assert excinfo.value.code == 2
    write_mock.assert_not_called()
    assert "Invalid agent selection" in capsys.readouterr().err


def test_set_prompts_when_no_argument(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``set`` with no positional → interactive prompt; empty input defaults to ``all``."""
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    target = tmp_path / "config.yml"
    with (
        patch("terok.lib.api.agents.get_roster", return_value=_fake_roster()),
        patch(
            "terok.lib.api.agents.parse_agent_selection",
            side_effect=lambda raw: raw,
        ),
        patch(
            "terok.lib.api.agents.set_global_image_agents",
            side_effect=lambda raw: target,
        ) as write_mock,
    ):
        agents.dispatch(_ns_set(None))
    write_mock.assert_called_once_with("all")


# ── registration ──────────────────────────────────────────────────────


def test_register_creates_group_with_list_and_set() -> None:
    """``register`` wires the ``agents`` group with both subverbs."""
    parser = argparse.ArgumentParser()
    agents.register(parser.add_subparsers(dest="cmd"))

    parsed_list = parser.parse_args(["agents", "list", "--all"])
    assert parsed_list.cmd == "agents"
    assert parsed_list.agents_cmd == "list"
    assert parsed_list.all is True

    parsed_set = parser.parse_args(["agents", "set", "claude,vibe"])
    assert parsed_set.cmd == "agents"
    assert parsed_set.agents_cmd == "set"
    assert parsed_set.selection == "claude,vibe"

    parsed_bare_set = parser.parse_args(["agents", "set"])
    assert parsed_bare_set.selection is None
