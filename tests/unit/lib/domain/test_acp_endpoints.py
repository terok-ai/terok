# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for :meth:`Project.acp_endpoints` — discovery surface for ``terok acp list``."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from terok_executor import ACPEndpointStatus

from terok.lib.domain.project import (
    ACPEndpoint,
    Project,
    _read_bound_agent,
    _task_has_any_authed_agent,
)


class _FakeMeta:
    """Minimal stand-in for :class:`TaskMeta` — only the fields the helpers read."""

    def __init__(self, mode: str | None = None) -> None:
        self.mode = mode


class _FakeTask:
    """Minimal stand-in for :class:`Task` — exposes the public API surface
    (``id``, ``mode``, ``meta``) the listing helpers consume.  Mirrors the
    real :class:`terok.lib.domain.task.Task`'s shape; ``task_id`` is
    intentionally absent so accidental ``task.task_id`` reads regress."""

    def __init__(self, task_id: str, mode: str | None = None) -> None:
        self.id = task_id
        self.mode = mode
        self.meta = _FakeMeta(mode=mode)


class TestReadBoundAgent:
    """Daemon's ``.bound`` JSON sidecar drives the ``bound_agent`` field."""

    def test_returns_none_when_file_missing(self, tmp_path: Path, monkeypatch) -> None:
        """No sidecar file ⇒ ``None`` (daemon hasn't written it yet)."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        assert _read_bound_agent("proj", "task-1") is None

    def test_returns_agent_name_from_json(self, tmp_path: Path, monkeypatch) -> None:
        """A well-formed sidecar yields the agent name."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        bound_dir = tmp_path / "terok" / "acp" / "proj"
        bound_dir.mkdir(parents=True)
        (bound_dir / "task-1.bound").write_text(json.dumps({"agent": "claude"}))
        assert _read_bound_agent("proj", "task-1") == "claude"

    def test_tolerates_malformed_json(self, tmp_path: Path, monkeypatch) -> None:
        """Partial / corrupt JSON during atomic-replace returns ``None``."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        bound_dir = tmp_path / "terok" / "acp" / "proj"
        bound_dir.mkdir(parents=True)
        (bound_dir / "task-1.bound").write_text("not-json{")
        assert _read_bound_agent("proj", "task-1") is None

    def test_tolerates_unexpected_shape(self, tmp_path: Path, monkeypatch) -> None:
        """JSON without an ``agent`` string field returns ``None``."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        bound_dir = tmp_path / "terok" / "acp" / "proj"
        bound_dir.mkdir(parents=True)
        (bound_dir / "task-1.bound").write_text(json.dumps({"foo": "bar"}))
        assert _read_bound_agent("proj", "task-1") is None


class TestTaskHasAnyAuthedAgent:
    """Auth-intersect-image classification for ``ready`` vs ``unsupported``."""

    def test_intersection_yields_true(self) -> None:
        """Image declares an authed agent → endpoint is ``ready``."""
        with mock.patch(
            "terok.lib.domain.project._image_agents_for_task",
            return_value={"claude", "codex"},
        ):
            task = _FakeTask("task-1", mode="cli")
            assert (
                _task_has_any_authed_agent(
                    "proj", task, {"claude"}, sandbox=mock.Mock(), label_cache={}
                )
                is True
            )

    def test_disjoint_yields_false(self) -> None:
        """Image's agents and the authed set don't overlap → ``unsupported``."""
        with mock.patch(
            "terok.lib.domain.project._image_agents_for_task",
            return_value={"vibe"},
        ):
            task = _FakeTask("task-1", mode="cli")
            assert (
                _task_has_any_authed_agent(
                    "proj", task, {"claude"}, sandbox=mock.Mock(), label_cache={}
                )
                is False
            )

    def test_empty_image_label_yields_false(self) -> None:
        """No agents in the image label ⇒ surface as ``unsupported``."""
        with mock.patch(
            "terok.lib.domain.project._image_agents_for_task",
            return_value=set(),
        ):
            task = _FakeTask("task-1", mode="cli")
            assert (
                _task_has_any_authed_agent(
                    "proj", task, {"claude"}, sandbox=mock.Mock(), label_cache={}
                )
                is False
            )


class TestAuthedVocabulary:
    """The auth set fed to the intersection must speak the image label's language.

    The vault keys credentials by provider (``anthropic`` — sandbox's v3
    re-keying); image labels list agent names (``claude``).  These tests pin
    the whole pipeline against the *real* storage path — a hand-built authed
    set can't catch a vocabulary drift between the two sibling packages.
    """

    def test_stored_credential_surfaces_under_agent_name(self) -> None:
        """A credential stored the way ``terok auth`` stores it maps back to the agent name."""
        from terok_executor import store_api_key

        from terok.lib.domain.auth import stored_credential_entries

        store_api_key("claude", "sk-unit-test")  # real path: lands under 'anthropic'
        entries = stored_credential_entries("default")
        assert "claude" in entries
        assert "anthropic" not in entries  # vault vocabulary must not leak through

    @pytest.mark.parametrize(
        ("agent", "expected_vault_key"),
        [("claude", "anthropic"), ("codex", "openai"), ("gh", "github")],
    )
    def test_vault_stores_under_provider_key(self, agent: str, expected_vault_key: str) -> None:
        """Pin the executor-side keying this module's translation depends on.

        If a future sibling release changes how credentials are keyed, this
        fails loudly here instead of silently emptying the READY intersection.
        """
        from terok_executor import credential_provider

        assert credential_provider(agent) == expected_vault_key

    def test_running_task_with_stored_credential_is_ready(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """End-to-end regression for the v3 re-keying bug: READY must survive it.

        Stores a claude credential through the executor's real storage path
        (vault key ``anthropic``), then lists endpoints for a running task
        whose image label declares ``claude``.  Before the fix the raw vault
        key was intersected with the label and the endpoint degraded to
        ``unsupported`` despite valid auth.
        """
        from terok_executor import store_api_key

        store_api_key("claude", "sk-unit-test")
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))  # no live socket anywhere
        project = Project(
            SimpleNamespace(name="proj", security_class="online", credential_set="default")  # type: ignore[arg-type]
        )
        task = _FakeTask("t1", mode="cli")
        with (
            mock.patch.object(Project, "list_tasks", return_value=[task]),
            mock.patch("terok.lib.domain.project.make_sandbox_config"),
            mock.patch("terok.lib.integrations.sandbox.Sandbox"),
            # Label side pinned to its real vocabulary: agent names, as
            # written by the executor's L1 build into ``ai.terok.agents``.
            mock.patch(
                "terok.lib.domain.project._image_agents_for_task",
                return_value={"claude", "gh"},
            ),
        ):
            (endpoint,) = project.acp_endpoints()
        assert endpoint.status is ACPEndpointStatus.READY

    def test_running_task_without_credential_stays_unsupported(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Empty vault ⇒ the same task classifies as ``unsupported``, not READY."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        project = Project(
            SimpleNamespace(name="proj", security_class="online", credential_set="default")  # type: ignore[arg-type]
        )
        task = _FakeTask("t1", mode="cli")
        with (
            mock.patch.object(Project, "list_tasks", return_value=[task]),
            mock.patch("terok.lib.domain.project.make_sandbox_config"),
            mock.patch("terok.lib.integrations.sandbox.Sandbox"),
            mock.patch(
                "terok.lib.domain.project._image_agents_for_task",
                return_value={"claude", "gh"},
            ),
        ):
            (endpoint,) = project.acp_endpoints()
        assert endpoint.status is ACPEndpointStatus.UNSUPPORTED


class TestACPEndpointDataclass:
    """The ``ACPEndpoint`` value object is a frozen dataclass."""

    def test_construction_minimal(self, tmp_path: Path) -> None:
        """All fields are positional/keyword and survive equality."""
        ep1 = ACPEndpoint(
            project_name="p",
            task_id="t",
            socket_path=tmp_path / "x.sock",
            status=ACPEndpointStatus.READY,
        )
        ep2 = ACPEndpoint(
            project_name="p",
            task_id="t",
            socket_path=tmp_path / "x.sock",
            status=ACPEndpointStatus.READY,
        )
        assert ep1 == ep2
        assert ep1.bound_agent is None

    def test_with_bound_agent(self, tmp_path: Path) -> None:
        """``bound_agent`` is set only when the daemon has bound a session."""
        ep = ACPEndpoint(
            project_name="p",
            task_id="t",
            socket_path=tmp_path / "x.sock",
            status=ACPEndpointStatus.ACTIVE,
            bound_agent="claude",
        )
        assert ep.status is ACPEndpointStatus.ACTIVE
        assert ep.bound_agent == "claude"
