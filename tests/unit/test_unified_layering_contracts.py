# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for terok's unified CLI layering with the sibling packages.

Each test pins one user-facing promise:

1. Config-equality — for sandbox/executor-owned `config.yml` schema
   fields, the [`SandboxConfig`][terok_sandbox.SandboxConfig] terok
   constructs equals the one standalone sandbox would have read from
   the same file.
2. Show-config observability — `terok executor show-config` and
   standalone `terok-executor show-config` produce diffable output
   that is equal on the shared schema fields.
3. Cfg-injection scope — every verb under `terok executor` (not just
   the sandbox subtree) sees terok's
   [`SandboxConfig`][terok_sandbox.SandboxConfig] when one is injected.
4. Per-container identity-form acceptance — `terok executor stop` and
   peers accept either a raw container id or a `project/task` form.
5. terok-task slash-form alias — `terok task <verb> p t` and
   `terok task <verb> p/t` produce the same parsed `(project, task)`.
6. Gate-binary ownership — `terok-gate` is published by terok-sandbox,
   not re-exported by terok.

The tests are deliberately broad: each exercises the public surface
end-to-end rather than poking the implementation.  Smaller unit
checks (argparse normalization, overlay corners) live in the focused
test files (e.g. `test_tree.py`) and are filled in during review.
"""

from __future__ import annotations

import importlib.metadata
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

# Fields where sandbox's [`SandboxConfig`][terok_sandbox.SandboxConfig]
# reads from `config.yml` via a `field(default_factory=…)` *and*
# terok also reads it in [`make_sandbox_config`][terok.lib.core.config.make_sandbox_config]
# — the true round-trip surface.
#
# ``shield_bypass`` is deliberately excluded: sandbox keeps it
# hardcoded to ``False`` so a user-writable config-yml scope
# (``~/.config/terok/config.yml``) or a ``TEROK_CONFIG_FILE`` env
# override can never silently disable the egress firewall — even
# though the orchestrator-driven path through ``make_sandbox_config``
# does read it.
_TEROK_PROMISED_FIELDS: tuple[str, ...] = (
    "services_mode",
    "shield_audit",
    "gate_port",
    "token_broker_port",
    "ssh_signer_port",
)


def _write_user_config(tmp_path: Path, body: str) -> Path:
    """Write *body* to *tmp_path*/config.yml and return its path."""
    cfg = tmp_path / "config.yml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def test_config_equality_contract(tmp_path: Path) -> None:
    """Schema fields in config.yml round-trip equally between standalone and terok-built cfg.

    Terok's promise: the
    [`SandboxConfig`][terok_sandbox.SandboxConfig] returned by
    [`make_sandbox_config`][terok.lib.core.config.make_sandbox_config]
    matches what
    [`SandboxConfig`][terok_sandbox.SandboxConfig] would have read
    standalone for the fields sandbox/executor own in `config.yml`.
    Tests `services.mode` and `shield.bypass_firewall_no_protection` /
    `shield.audit` — representative sandbox-owned schema knobs.
    """
    cfg_path = _write_user_config(
        tmp_path,
        "services:\n  mode: tcp\nshield:\n  audit: false\n",
    )

    with patch.dict(os.environ, {"TEROK_CONFIG_FILE": str(cfg_path)}, clear=False):
        # Reset cached resolution so both readers start from the same env.
        # Two layers of cache to bust: the path-level section cache (read_config_section),
        # and the validated-section `@lru_cache` factories that
        # sandbox dataclass field-factories call through.
        from terok_sandbox import config as _sandbox_config
        from terok_sandbox.paths import _config_section_cache

        _config_section_cache.clear()
        _sandbox_config._shield_section.cache_clear()
        _sandbox_config._credentials_section.cache_clear()

        from terok_sandbox import SandboxConfig

        from terok.lib.core.config import make_sandbox_config

        standalone = SandboxConfig()
        terok_built = make_sandbox_config()

        for field in _TEROK_PROMISED_FIELDS:
            assert getattr(terok_built, field) == getattr(standalone, field), (
                f"Field {field!r} diverged: "
                f"terok={getattr(terok_built, field)!r} vs "
                f"standalone={getattr(standalone, field)!r}"
            )


def test_show_config_observable(tmp_path: Path) -> None:
    """`terok executor show-config` and standalone `terok-executor show-config` agree.

    Diffability of the contract: the schema fields visible in one
    output match the schema fields visible in the other when both read
    the same `config.yml`.
    """
    pytest.importorskip("ruamel.yaml")
    from ruamel.yaml import YAML

    cfg_path = _write_user_config(tmp_path, "services:\n  mode: tcp\n")
    env = {**os.environ, "TEROK_CONFIG_FILE": str(cfg_path)}

    standalone_out = subprocess.run(
        ["terok-executor", "show-config"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    terok_out = subprocess.run(
        ["terok", "executor", "show-config"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    yaml = YAML(typ="safe")
    standalone = yaml.load(standalone_out)
    terok = yaml.load(terok_out)

    for field in _TEROK_PROMISED_FIELDS:
        assert terok[field] == standalone[field], (
            f"show-config diverged on {field!r}: "
            f"terok={terok[field]!r} vs standalone={standalone[field]!r}"
        )


def test_terok_executor_run_uses_terok_state() -> None:
    """Cfg-injection wraps every `terok executor *` handler that opts in.

    The mechanism: `inject_cfg_factory` wraps any handler under the
    overlay's subtree paths that declares a ``cfg`` parameter.  ``run``
    (and its siblings ``run-tool`` / ``setup`` / ``uninstall`` /
    ``show-config``) all accept ``cfg``; the cfg-injection therefore
    threads terok's resolved
    [`SandboxConfig`][terok_sandbox.SandboxConfig] into each at
    dispatch.  Sandbox-subtree handlers stay wrapped as well —
    extending the overlay scope to the whole tree did not regress
    them.
    """
    from terok.cli.main import _build_wired_tree

    tree = _build_wired_tree()

    for path in (
        ("executor", "run"),
        ("executor", "show-config"),
        ("executor", "sandbox", "vault", "start"),
    ):
        cmd = tree.find_at(path)
        assert cmd is not None and cmd.handler is not None, f"missing: {path}"
        assert hasattr(cmd.handler, "__wrapped__"), (
            f"{'.'.join(path)} handler is not cfg-wrapped — "
            "inject_cfg_factory's subtree_paths or the handler's "
            "cfg parameter is likely missing"
        )


def test_per_container_identity_forms() -> None:
    """`terok executor stop` accepts `<container-id>` and `<project>/<task>`.

    Observes the resolver-wrap behavior end-to-end on the wired tree:

    - ``handler(name="myproj/mytask")`` triggers a lookup of
      ``(myproj, mytask)`` through terok's task store.
    - ``handler(name="raw-container-id")`` does **not** trigger a
      lookup (no ``/`` separator).
    - After the slash-form lookup resolves to a container id, that id
      is what reaches executor's underlying handler.

    The third assertion is observed by patching
    [`PodmanRuntime`][terok_sandbox.PodmanRuntime] in executor's
    handler module so the call completes without touching podman, and
    capturing the name it sees.
    """
    from unittest.mock import MagicMock

    from terok.cli.main import _build_wired_tree

    tree = _build_wired_tree()
    stop_cmd = tree.find_at(("executor", "stop"))
    assert stop_cmd is not None and stop_cmd.handler is not None

    fake_runtime = MagicMock()
    # `Container.state` is read to decide whether to act; non-None is
    # enough for `_handle_stop` to proceed into `force_remove`.
    fake_container = MagicMock()
    fake_container.state = "running"
    fake_runtime.container.return_value = fake_container

    with (
        patch(
            "terok.lib.orchestration.tasks.lookup_container_by_pt",
        ) as lookup_mock,
        patch("terok_sandbox.PodmanRuntime", return_value=fake_runtime),
    ):
        lookup_mock.return_value = "resolved-container-name"
        stop_cmd.handler(name="myproj/mytask")
        lookup_mock.assert_called_once_with("myproj", "mytask")

        lookup_mock.reset_mock()
        stop_cmd.handler(name="raw-container-id")
        lookup_mock.assert_not_called()

    # Two container-name lookups on the runtime — the resolved one for
    # the slash-form input, the raw one for the raw-id input.
    container_calls = [call.args[0] for call in fake_runtime.container.call_args_list]
    assert container_calls == ["resolved-container-name", "raw-container-id"], (
        f"per-container identity forms not handled: actual = {container_calls}"
    )


def test_terok_task_slash_alias() -> None:
    """`terok task <verb> p t` and `terok task <verb> p/t` parse identically.

    Exercises the argparse layer plus
    [`_normalize_pt`][terok.cli.commands.task._normalize_pt] (which
    [`dispatch`][terok.cli.commands.task.dispatch] runs first) and
    asserts the resulting ``(project_id, task_id)`` is the same for
    both input forms.  Uses ``task stop`` as a representative verb
    that takes both positionals.
    """
    import argparse

    from terok.cli.commands.task import _normalize_pt, register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    register(subparsers)

    space = parser.parse_args(["task", "stop", "myproj", "mytask"])
    _normalize_pt(space)
    slash = parser.parse_args(["task", "stop", "myproj/mytask"])
    _normalize_pt(slash)

    assert (space.project_id, space.task_id) == ("myproj", "mytask")
    assert (slash.project_id, slash.task_id) == ("myproj", "mytask")


def test_lookup_container_by_pt_resolves_and_handles_misses() -> None:
    """`lookup_container_by_pt` returns a container name on hit, None on miss.

    Three branches exercised by patching ``read_task_meta``:
      - unknown task (read_task_meta → None) → None
      - known task with no recorded mode → None
      - known task with mode → synthesized container name
    """
    from terok.lib.orchestration.tasks import lookup_container_by_pt

    with patch("terok.lib.orchestration.tasks.query.read_task_meta") as read_mock:
        read_mock.return_value = None
        assert lookup_container_by_pt("myproj", "unknown") is None

        read_mock.return_value = {"task_id": "no-mode-task"}
        assert lookup_container_by_pt("myproj", "no-mode-task") is None

        read_mock.return_value = {"task_id": "live-task", "mode": "cli"}
        resolved = lookup_container_by_pt("myproj", "live-task")
        assert resolved is not None
        assert "myproj" in resolved
        assert "live-task" in resolved


@pytest.mark.parametrize(
    "slash_form",
    [
        "../etc/passwd",
        "myproj/..",
        "myproj/../other",
        "./bad",
        "/task",  # empty project part
        "myproj/",  # empty task part
        "myproj/a/b",  # nested separators in task
        "a/b/c",  # nested separators in both
    ],
)
def test_normalize_pt_rejects_path_traversal(slash_form: str) -> None:
    """The slash-form split rejects any malformed part — empty, dotty, or nested.

    ``str.partition("/")`` splits at the *first* slash, so each side has
    to be validated independently: an empty leading or trailing part
    (``"/task"`` / ``"proj/"``), a ``.``/``..`` segment, or a tail
    containing further slashes (``"proj/a/b"`` where the task partition
    would be ``"a/b"`` and reach filesystem helpers verbatim).
    """
    import argparse

    from terok.cli.commands.task import _normalize_pt

    args = argparse.Namespace(project_id=slash_form, task_id=None)
    with pytest.raises(SystemExit, match="(?i)invalid"):
        _normalize_pt(args)


@pytest.mark.parametrize(
    ("project_id", "task_id"),
    [
        ("../escape", "a1b2c"),
        ("MYPROJ", "a1b2c"),  # validate_project_id is lowercase-only
        ("myproj", ".."),
        ("myproj", "evil/sub"),
        ("myproj", ""),
    ],
)
def test_lookup_container_by_pt_rejects_unsafe_ids(project_id: str, task_id: str) -> None:
    """`lookup_container_by_pt` returns None for any input that could traverse out of the task store."""
    from terok.lib.orchestration.tasks import lookup_container_by_pt

    assert lookup_container_by_pt(project_id, task_id) is None


@pytest.mark.parametrize("bad", ["", ".", "..", "../escape", "..hidden", "a/b", "a\\b"])
def test_meta_path_builders_reject_unsafe_project_id(bad: str) -> None:
    """The five ``tasks/meta.py`` path-builders raise on a path-unsafe project_id.

    Defense-in-depth: even if a caller smuggles past the CLI / resolver
    guards (``_normalize_pt``, ``lookup_container_by_pt``), the
    path-building layer itself refuses the construction with a loud
    ``SystemExit``.  Covers
    [`tasks_meta_dir`][terok.lib.orchestration.tasks.meta.tasks_meta_dir],
    [`tasks_archive_dir`][terok.lib.orchestration.tasks.meta.tasks_archive_dir],
    and [`agent_config_dir`][terok.lib.orchestration.tasks.meta.agent_config_dir]
    (the project_id-consuming builders).
    """
    from terok.lib.orchestration.tasks.meta import (
        agent_config_dir,
        tasks_archive_dir,
        tasks_meta_dir,
    )

    for builder in (tasks_meta_dir, tasks_archive_dir):
        with pytest.raises(SystemExit, match="project_id"):
            builder(bad)
    with pytest.raises(SystemExit, match="project_id"):
        agent_config_dir(bad, "a1b2c")


@pytest.mark.parametrize("bad", ["", ".", "..", "../escape", "..hidden", "a/b", "a\\b"])
def test_meta_path_builders_reject_unsafe_task_id(bad: str, tmp_path: Path) -> None:
    """The three ``tasks/meta.py`` builders that take task_id refuse path-unsafe values.

    Covers [`dossier_path`][terok.lib.orchestration.tasks.meta.dossier_path],
    [`meta_path`][terok.lib.orchestration.tasks.meta.meta_path], and
    [`agent_config_dir`][terok.lib.orchestration.tasks.meta.agent_config_dir]
    (the task_id-consuming builders).  ``dossier_path`` / ``meta_path``
    are given a real ``meta_dir`` so the failure is the id check, not
    a missing-arg shape.
    """
    from terok.lib.orchestration.tasks.meta import (
        agent_config_dir,
        dossier_path,
        meta_path,
    )

    for builder in (dossier_path, meta_path):
        with pytest.raises(SystemExit, match="task_id"):
            builder(tmp_path, bad)
    with pytest.raises(SystemExit, match="task_id"):
        agent_config_dir("myproj", bad)


def test_sickbay_collects_checks_in_socket_mode(tmp_path: Path) -> None:
    """`_collect_all_checks` must not raise on a socket-mode cfg with ``None`` ports.

    Regression for the false-positive "Sandbox service ports are not all
    configured" SystemExit that fired for every running task on hosts using
    socket-mode services.yml (the default outside of TCP-mode setups).  In
    socket mode the three TCP ports are *supposed* to be ``None`` — the
    downstream assemblers already special-case it — so the early gate must
    only fire in TCP mode.
    """
    from unittest.mock import MagicMock

    from terok_sandbox import SandboxConfig

    from terok.lib.orchestration import container_doctor

    socket_cfg = SandboxConfig(
        services_mode="socket",
        gate_port=None,
        token_broker_port=None,
        ssh_signer_port=None,
    )

    with (
        patch.object(container_doctor, "make_sandbox_config", return_value=socket_cfg),
        patch.object(container_doctor, "get_roster", return_value=MagicMock()),
        patch.object(container_doctor, "load_project") as load_proj,
    ):
        load_proj.return_value = MagicMock(
            human_name="N",
            human_email="n@x",
            security_class="gatekeeping",
        )
        checks = container_doctor._collect_all_checks("any-project", tmp_path)

    assert isinstance(checks, list)
    # Port-drift checks are TCP-only and must be elided in socket mode.
    labels = [c.label for c in checks]
    assert "Token broker port drift" not in labels
    assert "SSH signer port drift" not in labels


def test_terok_doctor_checks_emits_port_drift_in_tcp_mode() -> None:
    """Mirror of the socket-mode test: real ports → drift checks present.

    Exercises the ``if … is not None: checks.append(_port_drift_check(…))``
    branches in [`_terok_doctor_checks`][terok.lib.orchestration.container_doctor._terok_doctor_checks].
    """
    from unittest.mock import MagicMock

    from terok.lib.orchestration import container_doctor

    with patch.object(container_doctor, "load_project") as load_proj:
        load_proj.return_value = MagicMock(
            human_name="N",
            human_email="n@x",
            security_class="gatekeeping",
        )
        checks = container_doctor._terok_doctor_checks(
            "any-project",
            gate_port=18700,
            token_broker_port=18701,
            ssh_signer_port=18702,
        )

    labels = [c.label for c in checks]
    assert "Token broker port drift" in labels
    assert "SSH signer port drift" in labels


def test_terok_gate_ownership() -> None:
    """`terok-gate` console script is provided by terok-sandbox, not terok.

    Iterates installed entry points; the `terok-gate` script must be
    associated with the `terok-sandbox` distribution.  No terok entry
    point re-exports it (every package owns the binaries it authors).
    """
    eps = importlib.metadata.entry_points(group="console_scripts")
    gate_eps = [ep for ep in eps if ep.name == "terok-gate"]

    assert gate_eps, "terok-gate script not installed — terok-sandbox missing?"
    # Each EntryPoint exposes its providing distribution via .dist
    # (Python 3.10+).  At least one provider should be terok-sandbox;
    # none should be terok.
    providers = {ep.dist.name for ep in gate_eps if ep.dist is not None}
    assert "terok-sandbox" in providers
    assert "terok" not in providers, (
        "terok-gate is still re-exported by terok; sandbox should own this binary"
    )
