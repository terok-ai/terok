# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""A task's clearance prompts: addressing its hub by the short id, and muting it honestly.

The hub keys everything by the container's 12-character short id; a mute sent
under the podman name or the task name is a silent no-op.  These tests use a
distinct string for each identity, so passing the wrong one cannot pass.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tests.testfs import MOCK_BASE

from terok.lib.domain import clearance as mod

_PROJECT = "proj"
_TASK = "task1"
_CONTAINER_NAME = "terok-proj-cli-task1"
"""What podman calls the container — a key the hub has never seen."""
_FULL_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
"""What ``podman inspect`` would return — not the hub's key either."""
_SHORT_ID = _FULL_ID[:12]
"""The one key the hub matches: the 12-character short id the OCI hook records."""
_RUNTIME = Path(MOCK_BASE) / "run"


def _project(clearance: str = "prompt") -> SimpleNamespace:
    """A loaded project double in *clearance* mode."""
    return SimpleNamespace(name=_PROJECT, clearance=clearance, tasks_root=Path(MOCK_BASE) / "tasks")


def _manager(*, short_id: str | None = _SHORT_ID, audit_enabled: bool = True) -> MagicMock:
    """A task shield manager double with a recorded short id and audit flag."""
    manager = MagicMock()
    manager.container_short_id = short_id
    manager.harvest.return_value = SimpleNamespace(audit_enabled=audit_enabled, entries=())
    return manager


@pytest.fixture
def hub() -> Iterator[AsyncMock]:
    """Patch the one-shot ``SetMute`` call; it echoes the requested state, like the hub."""

    async def _echo(_socket: Path, _container: str, muted: bool) -> bool:
        return muted

    with (
        patch(
            "terok.lib.integrations.clearance.set_container_mute", new=AsyncMock(side_effect=_echo)
        ) as call,
        patch.object(mod, "runtime_dir", return_value=_RUNTIME),
        patch(
            "terok.lib.orchestration.task_runners.resolve_container_uuid",
            side_effect=AssertionError("addressing a hub must not cost a podman inspect"),
        ),
    ):
        yield call


def _mute(
    manager: MagicMock,
    *,
    muted: bool = True,
    clearance: str = "prompt",
    started: str | None = "prompt",
) -> mod.MuteResult:
    """Run the task-menu mute against *manager*.

    *clearance* is the project's mode in the config now; *started* the mode
    the task's container started under, as its sidecar records it.
    """
    with (
        patch.object(mod, "load_project", return_value=_project(clearance)),
        patch.object(mod, "task_shield_manager", return_value=manager),
        patch.object(mod, "_task_started_clearance", return_value=started),
    ):
        return mod.set_task_prompts_muted(_PROJECT, _TASK, muted=muted)


def test_hub_socket_sits_in_the_short_id_directory() -> None:
    """``sun_path`` holds 108 bytes, so the supervisor names the directory with 12 chars."""
    with patch.object(mod, "runtime_dir", return_value=_RUNTIME):
        socket = mod.clearance_hub_socket(_SHORT_ID)

    assert socket == _RUNTIME / "clearance" / _SHORT_ID / "hub.sock"


def test_mute_addresses_the_hub_by_the_recorded_short_id(hub: AsyncMock) -> None:
    """Socket and container key are both the short id — never the name, never the full id."""
    result = _mute(_manager())

    socket, container, muted = hub.call_args.args
    assert container == _SHORT_ID
    assert container not in {_CONTAINER_NAME, _FULL_ID}
    assert socket == _RUNTIME / "clearance" / _SHORT_ID / "hub.sock"
    assert muted is True
    assert result.muted is True
    assert result.severity == "information"
    assert "still enforced and logged" in result.message


def test_unmute_says_the_prompts_are_back(hub: AsyncMock) -> None:
    """Unmute is its own explicit state, and the notice names the task."""
    result = _mute(_manager(), muted=False)

    assert hub.call_args.args[2] is False
    assert result.muted is False
    assert f"{_PROJECT}/{_TASK}" in result.message


def test_mute_with_audit_off_warns_that_nothing_records_the_refusals(hub: AsyncMock) -> None:
    """A muted task with audit off is refused things nobody hears about — say so."""
    result = _mute(_manager(audit_enabled=False))

    assert result.muted is True
    assert result.severity == "warning"
    assert "neither prompted nor recorded" in result.message


def test_unmute_does_not_read_the_audit_log(hub: AsyncMock) -> None:
    """Only a mute needs the audit warning; an unmute leaves the log unread."""
    manager = _manager()

    _mute(manager, muted=False)

    manager.harvest.assert_not_called()


def test_a_running_hub_is_asked_whatever_the_project_says_now(hub: AsyncMock) -> None:
    """A project switched to ``off`` while its task runs: the task's hub still prompts.

    The config reaches a container only at its next start, so the hub it
    started with is the one to mute — refusing up front on the config would
    leave a prompting task unmutable and claim nobody is asked.
    """
    result = _mute(_manager(), clearance="off", started="prompt")

    hub.assert_called_once()
    assert result.muted is True
    assert result.severity == "information"


def test_a_task_started_with_clearance_off_has_nothing_to_mute() -> None:
    """No hub answers, and the container's own record says it started under ``off``."""
    absent = AsyncMock(side_effect=FileNotFoundError("no hub.sock"))
    with (
        patch("terok.lib.integrations.clearance.set_container_mute", new=absent),
        patch.object(mod, "runtime_dir", return_value=_RUNTIME),
    ):
        result = _mute(_manager(), clearance="prompt", started="off")

    assert result.muted is None
    assert result.severity == "warning"
    assert "started with clearance off" in result.message
    assert "services.clearance: off" in result.message


def test_never_started_task_has_no_hub_to_mute(hub: AsyncMock) -> None:
    """No recorded short id means the container never started: no hub, no call."""
    result = _mute(_manager(short_id=None))

    hub.assert_not_called()
    assert result.muted is None
    assert "has not started" in result.message


def test_unreachable_hub_is_an_error_naming_the_reason() -> None:
    """A hub that does not answer leaves the state unknown, reported as an error."""
    failing = AsyncMock(side_effect=ConnectionRefusedError("no hub"))
    with (
        patch("terok.lib.integrations.clearance.set_container_mute", new=failing),
        patch.object(mod, "runtime_dir", return_value=_RUNTIME),
    ):
        result = _mute(_manager())

    assert result.muted is None
    assert result.severity == "error"
    assert "did not answer" in result.message
    assert "no hub" in result.message


def test_a_hub_that_keeps_its_state_is_reported_as_such() -> None:
    """The hub's answer, not the request, decides the notice."""
    stubborn = AsyncMock(return_value=False)
    with (
        patch("terok.lib.integrations.clearance.set_container_mute", new=stubborn),
        patch.object(mod, "runtime_dir", return_value=_RUNTIME),
    ):
        result = _mute(_manager(), muted=True)

    assert result.muted is False
    assert result.severity == "error"


def test_a_wedged_hub_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one-shot call is bounded: a hub that never answers becomes an error.

    The ``TimeoutError`` it raises carries no message, so the notice names the
    exception type instead of printing empty parentheses.
    """
    import asyncio

    async def _hang(_socket: Path, _container: str, _muted: bool) -> bool:
        await asyncio.Event().wait()
        return True

    monkeypatch.setattr(mod, "_HUB_TIMEOUT_S", 0.01)
    with (
        patch("terok.lib.integrations.clearance.set_container_mute", new=_hang),
        patch.object(mod, "runtime_dir", return_value=_RUNTIME),
    ):
        result = _mute(_manager())

    assert result.muted is None
    assert result.severity == "error"
    assert result.message.endswith("did not answer (TimeoutError)")


@pytest.mark.parametrize(
    ("audit_enabled", "severity", "phrase"),
    [
        pytest.param(True, "information", "still enforced and logged", id="audit-on"),
        pytest.param(None, "information", "still enforced and logged", id="audit-unknown"),
        pytest.param(False, "warning", "neither prompted nor recorded", id="audit-off"),
    ],
)
def test_muted_notice_follows_the_audit_flag(
    audit_enabled: bool | None, severity: str, phrase: str
) -> None:
    """Unknown reads as the shield's default (on); only an explicit off warns."""
    notice = mod.muted_notice(_CONTAINER_NAME, audit_enabled=audit_enabled)

    assert notice.muted is True
    assert notice.severity == severity
    assert phrase in notice.message
    assert _CONTAINER_NAME in notice.message


def test_task_audit_enabled_reads_the_tasks_own_flag() -> None:
    """The container keeps the flag it was created with; the harvest report carries it."""
    with (
        patch.object(mod, "load_project", return_value=_project()),
        patch.object(mod, "task_shield_manager", return_value=_manager(audit_enabled=False)),
    ):
        assert mod.task_audit_enabled(_PROJECT, _TASK) is False


def test_task_audit_enabled_is_unknown_when_nothing_can_be_read() -> None:
    """A project that cannot load, or a harvest that fails, is unknown — never a crash."""
    with patch.object(mod, "load_project", side_effect=SystemExit("no such project")):
        assert mod.task_audit_enabled(_PROJECT, _TASK) is None

    broken = MagicMock()
    broken.harvest.side_effect = OSError("unreadable")
    with (
        patch.object(mod, "load_project", return_value=_project()),
        patch.object(mod, "task_shield_manager", return_value=broken),
    ):
        assert mod.task_audit_enabled(_PROJECT, _TASK) is None


# ── Reading the mute back ─────────────────────────────────────────────


def _mutes(answer: object) -> bool | None:
    """Ask ``container_prompts_muted`` about ``_SHORT_ID`` against a ``Mutes()`` double."""
    if isinstance(answer, BaseException):
        query = AsyncMock(side_effect=answer)
    else:
        query = AsyncMock(return_value=answer)
    with (
        patch("terok.lib.integrations.clearance.container_mutes", new=query),
        patch.object(mod, "runtime_dir", return_value=_RUNTIME),
    ):
        state = mod.container_prompts_muted(_SHORT_ID)
    query.assert_called_once_with(_RUNTIME / "clearance" / _SHORT_ID / "hub.sock")
    return state


@pytest.mark.parametrize(
    ("mutes", "state"),
    [
        pytest.param(frozenset({_SHORT_ID}), True, id="muted"),
        pytest.param(frozenset(), False, id="asking"),
        pytest.param(frozenset({_CONTAINER_NAME, _FULL_ID}), False, id="other-keys-only"),
    ],
)
def test_the_hub_says_whether_the_short_id_is_muted(mutes: frozenset[str], state: bool) -> None:
    """The hub's own socket, by the short id — and only the short id counts as this task."""
    assert _mutes(mutes) is state


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(ConnectionRefusedError("no hub"), id="no-hub"),
        pytest.param(RuntimeError("org.varlink.service.MethodNotFound"), id="hub-predates-mutes"),
    ],
)
def test_a_hub_that_cannot_say_reads_as_unknown(failure: BaseException) -> None:
    """Unknown is never reported as asking: a mute must not be hidden by a dead socket."""
    assert _mutes(failure) is None


def test_a_wedged_hub_reads_as_unknown_quickly(monkeypatch: pytest.MonkeyPatch) -> None:
    """The query is bounded by its own short timeout, so the pane is not held back."""
    import asyncio

    async def _hang(_socket: Path) -> frozenset[str]:
        await asyncio.Event().wait()
        return frozenset()

    monkeypatch.setattr(mod, "_STATE_QUERY_TIMEOUT_S", 0.01)
    with (
        patch("terok.lib.integrations.clearance.container_mutes", new=_hang),
        patch.object(mod, "runtime_dir", return_value=_RUNTIME),
    ):
        assert mod.container_prompts_muted(_SHORT_ID) is None


# ── Who a running container asks: its hub, else its own record ───────


@pytest.mark.parametrize(
    ("muted", "started", "state"),
    [
        pytest.param(True, "off", "muted", id="hub-says-muted"),
        pytest.param(False, "off", "asking", id="hub-says-asking-whatever-the-record"),
        pytest.param(None, "off", "off", id="no-hub-started-off"),
        pytest.param(None, "prompt", "unknown", id="no-hub-should-be-one"),
        pytest.param(None, "mute", "unknown", id="no-hub-started-muted"),
        pytest.param(None, None, "unknown", id="no-hub-no-record"),
    ],
)
def test_the_prompt_state_comes_from_the_container(
    muted: bool | None, started: str | None, state: str
) -> None:
    """The hub's answer first; the start-time mode only says what a silent hub means."""
    with (
        patch.object(mod, "container_prompts_muted", return_value=muted) as hub,
        patch.object(mod, "started_clearance_mode", return_value=started) as record,
    ):
        assert mod.container_prompt_state(_CONTAINER_NAME, _SHORT_ID) == state

    hub.assert_called_once_with(_SHORT_ID)
    if muted is None:
        record.assert_called_once_with(_CONTAINER_NAME)
    else:
        record.assert_not_called()


def test_without_a_short_id_only_the_record_can_answer() -> None:
    """A container whose id was never recorded has no hub to address."""
    with (
        patch.object(mod, "container_prompts_muted") as hub,
        patch.object(mod, "started_clearance_mode", return_value="off"),
    ):
        assert mod.container_prompt_state(_CONTAINER_NAME, None) == "off"

    hub.assert_not_called()


def _write_sidecar(name: str, **fields: object) -> Path:
    """Write a minimal sidecar for container *name* where sandbox keeps it; return its path.

    Only the fields sandbox's reader requires, plus *fields*; the paths in
    it are validated as absolute, never touched.
    """
    import json

    from terok.lib.core.config import make_sandbox_config

    path = make_sandbox_config().sidecar_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "container_name": name,
        "ipc_mode": "socket",
        "broker_context": {"scope": _PROJECT, "subject": _TASK, "credential_set": "default"},
        "db_path": str(Path(MOCK_BASE) / "vault.db"),
        "runtime_dir": str(_RUNTIME),
        **fields,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("fields", "mode"),
    [
        pytest.param({"clearance": "off"}, "off", id="off"),
        pytest.param({"clearance": "mute"}, "mute", id="mute"),
        pytest.param({}, "prompt", id="sidecar-from-before-the-setting"),
        pytest.param({"clearance": "loud"}, "prompt", id="unknown-reads-as-prompt"),
    ],
)
def test_the_started_mode_is_the_sidecars_record(fields: dict[str, object], mode: str) -> None:
    """Read with sandbox's own reader and its lenient rules."""
    _write_sidecar(_CONTAINER_NAME, **fields)

    assert mod.started_clearance_mode(_CONTAINER_NAME) == mode


def test_no_sidecar_is_no_record() -> None:
    assert mod.started_clearance_mode(_CONTAINER_NAME) is None


def test_an_unreadable_sidecar_is_no_record() -> None:
    _write_sidecar(_CONTAINER_NAME).write_text("{not json", encoding="utf-8")

    assert mod.started_clearance_mode(_CONTAINER_NAME) is None


def test_a_name_no_sidecar_can_have_is_no_record() -> None:
    """A name sandbox refuses as a path component never reaches the filesystem."""
    assert mod.started_clearance_mode("../escape") is None


def test_a_tasks_started_mode_is_read_under_its_container_name() -> None:
    """The task's mode names its container, the key its sidecar is stored under."""
    from terok.lib.orchestration.tasks import container_name

    with (
        patch.object(mod, "load_task_meta", return_value=({"mode": "cli"}, Path(MOCK_BASE))),
        patch.object(mod, "started_clearance_mode", return_value="off") as record,
    ):
        assert mod._task_started_clearance(_PROJECT, _TASK) == "off"

    record.assert_called_once_with(container_name(_PROJECT, "cli", _TASK))


@pytest.mark.parametrize(
    "meta",
    [
        pytest.param(({}, Path(MOCK_BASE)), id="never-run"),
        pytest.param(SystemExit("no such task"), id="unknown-task"),
    ],
)
def test_a_task_without_a_container_has_no_started_mode(meta: object) -> None:
    kwargs = {"side_effect": meta} if isinstance(meta, BaseException) else {"return_value": meta}
    with patch.object(mod, "load_task_meta", **kwargs):
        assert mod._task_started_clearance(_PROJECT, _TASK) is None


# ── Whether the live screen has anything to show ─────────────────────


def _notice(
    tmp_path: Path, *, hub: bool = False, global_mode: str = "off", project: object = None
) -> str | None:
    """Ask for the clearance-off notice with *hub* listening (or not) under a tmp runtime."""
    if hub:
        socket = tmp_path / "clearance" / _SHORT_ID / "hub.sock"
        socket.parent.mkdir(parents=True)
        socket.touch()
    loader = (
        {"side_effect": project}
        if isinstance(project, BaseException)
        else {"return_value": project}
    )
    with (
        patch.object(mod, "runtime_dir", return_value=tmp_path),
        patch.object(mod, "get_services_clearance", return_value=global_mode),
        patch.object(mod, "load_project", **loader),
    ):
        return mod.clearance_off_notice(_PROJECT if project is not None else None)


def test_a_listening_hub_opens_the_screen_whatever_the_config(tmp_path: Path) -> None:
    """A task started before the switch keeps prompting, so the screen has something to show."""
    assert _notice(tmp_path, hub=True, project=_project("off")) is None


def test_clearance_on_globally_opens_the_screen(tmp_path: Path) -> None:
    """With nothing running yet, a task started later may still prompt."""
    assert _notice(tmp_path, global_mode="prompt") is None


def test_a_selected_project_that_prompts_opens_the_screen(tmp_path: Path) -> None:
    """Off globally, but the project overrides it: its next task prompts."""
    assert _notice(tmp_path, project=_project("prompt")) is None


def test_off_everywhere_with_no_hub_says_so_instead(tmp_path: Path) -> None:
    notice = _notice(tmp_path, project=_project("off"))

    assert notice is not None
    assert f"globally and for {_PROJECT}" in notice
    assert "services.clearance: off" in notice
    assert "harvest" in notice
    assert "with shield audit on" in notice  # never claims a refusal is always logged


def test_off_globally_with_no_project_says_so(tmp_path: Path) -> None:
    notice = _notice(tmp_path)

    assert notice is not None
    assert "off globally (" in notice


def test_a_project_that_does_not_load_counts_neither_way(tmp_path: Path) -> None:
    notice = _notice(tmp_path, project=SystemExit("broken project.yml"))

    assert notice is not None
    assert "off globally (" in notice


def test_a_runtime_dir_that_cannot_be_listed_counts_as_no_hub(tmp_path: Path) -> None:
    """A probe that fails says nothing is listening — the config alone then decides."""
    with patch.object(mod, "runtime_dir", side_effect=OSError("no runtime dir")):
        assert mod._any_hub_listening() is False
    assert _notice(tmp_path, global_mode="off") is not None
