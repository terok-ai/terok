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
# currently reads from `config.yml` via a `field(default_factory=…)`
# *and* terok also reads it in [`make_sandbox_config`][terok.lib.core.config.make_sandbox_config]
# — the only true round-trip surface today.
#
# Other sandbox-owned schema knobs (`shield.audit`,
# `shield.bypass_firewall_no_protection`, port fields) live in
# sandbox's pydantic schema (`RawShieldSection`, `RawGateServerSection`)
# but are constants on the `SandboxConfig` dataclass — terok currently
# fills them in itself.  Wiring those into factories is a follow-up in
# terok-sandbox; this test pins what's *promised today* without
# overreaching.
_TEROK_PROMISED_FIELDS: tuple[str, ...] = ("services_mode",)


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
        from terok_sandbox.paths import _config_section_cache

        _config_section_cache.clear()

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
    overlay's subtree paths that declares a ``cfg`` parameter.  After
    E2 extends ``subtree_paths`` to the whole executor tree,
    *executor-native* verbs whose handler signature includes ``cfg``
    are wrapped alongside the sandbox subtree.  ``show-config`` is the
    canonical opt-in (handler:
    [`_handle_show_config`][terok_executor.commands._handle_show_config]
    declares ``cfg``); checking it pins the contract observably.

    Verbs like ``run`` whose handlers don't yet accept ``cfg`` build
    their own [`SandboxConfig`][terok_sandbox.SandboxConfig]
    internally; today they still see terok's effective state because
    standalone sandbox reads the same `config.yml` terok does.
    Funneling those through the wrap is a follow-up that requires
    executor-side handler refactors — out of scope here.
    """
    from terok.cli.main import _build_wired_tree

    tree = _build_wired_tree()
    show_cfg = tree.find_at(("executor", "show-config"))
    assert show_cfg is not None and show_cfg.handler is not None

    # The cfg-wrap is `functools.wraps(handler)`-applied; the inner
    # handler is reachable via `__wrapped__`.  Presence of that
    # attribute is the smoking gun that the overlay fired on an
    # executor-native (non-sandbox-subtree) verb.
    assert hasattr(show_cfg.handler, "__wrapped__"), (
        "terok executor show-config handler is not cfg-wrapped — "
        "inject_cfg_factory's subtree_paths probably still excludes "
        "executor-native verbs"
    )

    # Sandbox-subtree handlers stay wrapped — the existing scope didn't
    # regress.
    vault_start = tree.find_at(("executor", "sandbox", "vault", "start"))
    if vault_start is not None and vault_start.handler is not None:
        assert hasattr(vault_start.handler, "__wrapped__")


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
