# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the shared argcomplete completers and their parser-attachment helpers."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from terok.cli.commands._completers import (
    add_project_name,
    add_task_id,
    complete_project_names,
    complete_task_ids,
    set_completer,
)

# ---------------------------------------------------------------------------
# Individual completer functions
# ---------------------------------------------------------------------------


class TestCompleteProjectIds:
    """``complete_project_names`` returns project names matching the prefix."""

    def test_lists_all_ids_for_empty_prefix(self) -> None:
        with patch(
            "terok.cli.commands._completers.list_projects",
            return_value=[SimpleNamespace(name="alpha"), SimpleNamespace(name="beta")],
        ):
            result = complete_project_names("", argparse.Namespace())
        assert sorted(result) == ["alpha", "beta"]

    def test_filters_by_prefix(self) -> None:
        with patch(
            "terok.cli.commands._completers.list_projects",
            return_value=[SimpleNamespace(name="alpha"), SimpleNamespace(name="ally")],
        ):
            result = complete_project_names("al", argparse.Namespace())
        assert sorted(result) == ["ally", "alpha"]

    def test_returns_empty_on_failure(self) -> None:
        """Completers must never raise — silent empty list on error."""
        with patch(
            "terok.cli.commands._completers.list_projects",
            side_effect=RuntimeError("boom"),
        ):
            assert complete_project_names("", argparse.Namespace()) == []


# ---------------------------------------------------------------------------


class TestCompleteTaskIds:
    """``complete_task_ids`` scopes to ``parsed_args.project_name``."""

    def test_empty_when_no_project_typed_yet(self) -> None:
        """Without a project_name on parsed_args, no task suggestions."""
        assert complete_task_ids("", argparse.Namespace()) == []
        assert complete_task_ids("", argparse.Namespace(project_name=None)) == []

    def test_lists_tasks_for_project(self) -> None:
        tasks = [SimpleNamespace(task_id="k3v8h"), SimpleNamespace(task_id="p7fmn")]
        with patch("terok.cli.commands._completers.get_tasks", return_value=tasks) as mock:
            result = complete_task_ids("", argparse.Namespace(project_name="myproj"))
        mock.assert_called_once_with("myproj")
        assert sorted(result) == ["k3v8h", "p7fmn"]

    def test_filters_by_prefix(self) -> None:
        tasks = [SimpleNamespace(task_id="k3v8h"), SimpleNamespace(task_id="p7fmn")]
        with patch("terok.cli.commands._completers.get_tasks", return_value=tasks):
            result = complete_task_ids("k", argparse.Namespace(project_name="p"))
        assert result == ["k3v8h"]

    @pytest.mark.parametrize(
        "typed, expected",
        [
            ("K3V8", ["k3v8h"]),  # uppercase → lowercase
            ("k3-v8", ["k3v8h"]),  # hyphen separator
            ("K3VO", ["k3v01"]),  # O → 0 on a body position
            ("K3-V-I", ["k3v1m"]),  # hyphens + I → 1
            ("P7F", ["p7fmn"]),  # sanity: no ambiguous letters
        ],
    )
    def test_normalises_typed_prefix(self, typed: str, expected: list[str]) -> None:
        """Typed-prefix variants resolve to canonical IDs (bash then rewrites the word)."""
        tasks = [
            SimpleNamespace(task_id="k3v8h"),
            SimpleNamespace(task_id="k3v01"),
            SimpleNamespace(task_id="k3v1m"),
            SimpleNamespace(task_id="p7fmn"),
        ]
        with patch("terok.cli.commands._completers.get_tasks", return_value=tasks):
            result = complete_task_ids(typed, argparse.Namespace(project_name="p"))
        assert result == expected

    def test_skips_tasks_without_ids(self) -> None:
        """Tasks with a falsy ``task_id`` (defensive: shouldn't happen) are skipped."""
        tasks = [SimpleNamespace(task_id="k3v8h"), SimpleNamespace(task_id="")]
        with patch("terok.cli.commands._completers.get_tasks", return_value=tasks):
            result = complete_task_ids("", argparse.Namespace(project_name="p"))
        assert result == ["k3v8h"]

    def test_returns_empty_on_failure(self) -> None:
        with patch(
            "terok.cli.commands._completers.get_tasks",
            side_effect=RuntimeError("boom"),
        ):
            assert complete_task_ids("", argparse.Namespace(project_name="p")) == []


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Parser-attachment helpers — pin the completer to the argparse action
# ---------------------------------------------------------------------------


class TestParserAttachment:
    """``add_project_name`` / ``add_task_id`` attach the right completer."""

    def test_add_project_name_attaches_completer(self) -> None:
        parser = argparse.ArgumentParser()
        action = add_project_name(parser)
        assert action.dest == "project_name"
        # argcomplete reads the ``completer`` attribute — verify it's bound
        # to the right function (not just "something callable").
        assert action.completer is complete_project_names

    def test_add_task_id_attaches_completer(self) -> None:
        parser = argparse.ArgumentParser()
        action = add_task_id(parser)
        assert action.dest == "task_id"
        assert action.completer is complete_task_ids

    def test_add_project_name_forwards_kwargs(self) -> None:
        """Custom nargs / metavar / help must flow through to argparse."""
        parser = argparse.ArgumentParser()
        action = add_project_name(parser, nargs="?", metavar="project", help="...")
        assert action.nargs == "?"
        assert action.metavar == "project"


# ---------------------------------------------------------------------------
# Coverage sweep — every parser that takes project_name/task_id has a completer
# ---------------------------------------------------------------------------


def _walk_actions(parser: argparse.ArgumentParser, seen: set[int] | None = None) -> list:
    """Yield every non-help action across the parser tree (including subparsers)."""
    seen = seen if seen is not None else set()
    if id(parser) in seen:
        return []
    seen.add(id(parser))
    actions = []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if isinstance(action, argparse._SubParsersAction):
            for sp in action.choices.values():
                actions.extend(_walk_actions(sp, seen))
        else:
            actions.append(action)
    return actions


@pytest.mark.parametrize("prog", ["terok", "terokctl"])
def test_every_project_name_and_task_id_has_completer(prog: str) -> None:
    """Sweep the full parser tree — no ``project_name``/``task_id`` without a completer.

    Catches regressions where someone adds a new ``project_name`` positional
    but forgets ``add_project_name`` / ``add_task_id``.
    """
    import argparse as _ap

    parser = _ap.ArgumentParser(prog=prog)
    sub = parser.add_subparsers(dest="cmd")

    # Register every command module — mirrors main.py's registration order
    # but without the wire_group sibling paths (those live outside our dest
    # naming convention).
    from terok.cli.commands import (
        auth,
        clearance,
        completions,
        image,
        info,
        panic,
        project,
        setup,
        shield,
        sickbay,
        task,
    )

    panic.register(sub)
    setup.register(sub)
    auth.register(sub)
    project.register(sub)
    task.register(sub, prog=prog)
    image.register(sub)
    clearance.register(sub)
    sickbay.register(sub)
    shield.register(sub)
    info.register(sub)
    completions.register(sub)

    missing: list[str] = []
    for action in _walk_actions(parser):
        if action.dest in ("project_name", "task_id"):
            if not hasattr(action, "completer") or action.completer is None:
                missing.append(f"{action.dest} (option_strings={action.option_strings})")

    assert not missing, f"Missing completers on: {missing}"


def test_set_completer_attaches_callable() -> None:
    """``set_completer`` is the low-level helper; both helpers go through it."""

    def my_completer(prefix, parsed_args, **kw):
        return ["a", "b"]

    parser = argparse.ArgumentParser()
    action = parser.add_argument("--thing")
    set_completer(action, my_completer)
    assert action.completer is my_completer
