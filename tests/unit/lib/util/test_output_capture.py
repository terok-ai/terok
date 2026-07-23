# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the terok output-capture glue over terok_util.tee_output."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from terok.lib.util import output_capture as oc


def test_log_file_path_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(oc, "_logs_dir", lambda project: tmp_path)
    run_path = oc._log_file_path("run", "proj", "t1")
    build_path = oc._log_file_path("build", "proj", None)
    assert run_path.name.startswith("run-t1-") and run_path.suffix == ".log"
    assert build_path.name.startswith("build-") and "None" not in build_path.name


def test_logs_dir_scopes_by_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("terok.lib.core.paths.core_state_dir", lambda: tmp_path)
    assert oc._logs_dir("proj") == tmp_path / "projects" / "proj" / "logs"
    assert oc._logs_dir(None) == tmp_path / "logs"
    assert oc._logs_dir("../evil") == tmp_path / "logs"  # unsafe name falls back to global


def test_tee_output_delegates_and_persists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "run.log"
    monkeypatch.setattr(oc, "_log_file_path", lambda kind, project, task_id: log_path)
    from terok_util import output_capture as util_oc

    monkeypatch.setattr(util_oc, "journald_available", lambda: False)

    with oc.tee_output("run", project="proj", task_id="t1"):
        os.write(1, b"hello-capture\n")
        subprocess.run(["printf", "sub-line\\n"], check=True)

    live = capfd.readouterr()
    logged = log_path.read_text()
    assert "hello-capture" in live.out and "sub-line" in live.out
    assert "hello-capture" in logged and "sub-line" in logged
