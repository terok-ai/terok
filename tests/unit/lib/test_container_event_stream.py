# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``container_event_stream`` probes the runtime and degrades gracefully.

Container events are a backend extension, not part of the ``ContainerRuntime``
protocol, so the query feature-detects ``PodmanRuntime.events`` and returns
``None`` when it's absent or a subscribe fails — the caller then leans on the
periodic resync.  These tests stand in a fake runtime at the integration seam.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import terok.lib.integrations.sandbox as sandbox
from terok.lib.api import container_event_stream

PID = "proj"


def _patch_runtime(monkeypatch: Any, runtime_factory: Any) -> None:
    """Swap the ``PodmanRuntime`` the query constructs for *runtime_factory*."""
    monkeypatch.setattr(sandbox, "PodmanRuntime", runtime_factory)


def test_returns_stream_when_runtime_supports_events(monkeypatch: Any) -> None:
    sentinel = object()

    class Runtime:
        def events(self, prefix: str) -> object:
            assert prefix == PID
            return sentinel

    _patch_runtime(monkeypatch, Runtime)
    assert container_event_stream(PID) is sentinel


def test_returns_none_when_runtime_lacks_events(monkeypatch: Any) -> None:
    class OldRuntime:
        """A sandbox build predating the event stream."""

    _patch_runtime(monkeypatch, OldRuntime)
    assert container_event_stream(PID) is None


def test_returns_none_when_subscribe_raises(monkeypatch: Any) -> None:
    class Runtime:
        def events(self, prefix: str) -> object:
            raise OSError("podman not found")

    _patch_runtime(monkeypatch, Runtime)
    assert container_event_stream(PID) is None


def test_constructs_runtime_only_once(monkeypatch: Any) -> None:
    """The probe shouldn't spin up more than one runtime per call."""
    factory = mock.Mock(return_value=mock.Mock(events=mock.Mock(return_value=object())))
    _patch_runtime(monkeypatch, factory)
    container_event_stream(PID)
    factory.assert_called_once()
