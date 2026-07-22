# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for build/run output capture (journald + file sinks, pty/pipe tee)."""

from __future__ import annotations

import os
import socket
import struct
import subprocess
from pathlib import Path

import pytest

from terok.lib.util import output_capture as oc

# ── native journald field encoding ─────────────────────────────────────


def test_encode_field_compact_form() -> None:
    assert oc._encode_field("MESSAGE", b"hello") == b"MESSAGE=hello\n"


def test_encode_field_binary_form_for_multiline() -> None:
    value = b"line1\nline2"
    encoded = oc._encode_field("MESSAGE", value)
    assert encoded == b"MESSAGE\n" + struct.pack("<Q", len(value)) + value + b"\n"


# ── journald availability probe ────────────────────────────────────────


def test_journald_available_true_for_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sock_path = tmp_path / "journal.sock"
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver.bind(str(sock_path))
    monkeypatch.setattr(oc, "_JOURNALD_SOCKET", sock_path)
    try:
        assert oc._journald_available() is True
    finally:
        receiver.close()


def test_journald_available_false_for_plain_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "not-a-socket"
    plain.write_text("")
    monkeypatch.setattr(oc, "_JOURNALD_SOCKET", plain)
    assert oc._journald_available() is False


# ── file sink ──────────────────────────────────────────────────────────


def test_file_sink_writes_bytes_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "run.log"
    sink = oc._FileSink(path)
    sink.write(b"first\n")
    sink.write(b"second\n")
    sink.close()
    assert path.read_bytes() == b"first\nsecond\n"
    assert (path.stat().st_mode & 0o777) == 0o600


# ── journald sink (real bound receiver) ────────────────────────────────


def _bind_receiver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> socket.socket:
    sock_path = tmp_path / "journal.sock"
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver.bind(str(sock_path))
    receiver.settimeout(2.0)
    monkeypatch.setattr(oc, "_JOURNALD_SOCKET", sock_path)
    return receiver


def test_journald_sink_emits_one_entry_per_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receiver = _bind_receiver(tmp_path, monkeypatch)
    try:
        sink = oc._JournaldSink({"SYSLOG_IDENTIFIER": "terok", "TEROK_KIND": "run"})
        sink.write(b"alpha\nbeta\n")
        first = receiver.recv(65536)
        second = receiver.recv(65536)
        sink.close()
    finally:
        receiver.close()
    assert b"MESSAGE=alpha\n" in first
    assert b"TEROK_KIND=run\n" in first
    assert b"MESSAGE=beta\n" in second


def test_journald_sink_collapses_carriage_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receiver = _bind_receiver(tmp_path, monkeypatch)
    try:
        sink = oc._JournaldSink({"SYSLOG_IDENTIFIER": "terok"})
        sink.write(b"10%\r55%\r100% done\n")
        datagram = receiver.recv(65536)
        sink.close()
    finally:
        receiver.close()
    assert b"MESSAGE=100% done\n" in datagram
    assert b"10%" not in datagram


def test_journald_sink_flushes_unterminated_tail_on_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receiver = _bind_receiver(tmp_path, monkeypatch)
    try:
        sink = oc._JournaldSink({"SYSLOG_IDENTIFIER": "terok"})
        sink.write(b"no-newline-tail")
        sink.close()
        datagram = receiver.recv(65536)
    finally:
        receiver.close()
    assert b"MESSAGE=no-newline-tail\n" in datagram


# ── sink selection ─────────────────────────────────────────────────────


def test_make_sink_prefers_journald_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oc, "_journald_available", lambda: True)
    captured: dict[str, str] = {}
    monkeypatch.setattr(oc, "_JournaldSink", lambda fields: captured.update(fields) or "J")
    assert oc._make_sink("run", "proj", "t1") == "J"
    assert captured["TEROK_PROJECT"] == "proj"
    assert captured["TEROK_TASK"] == "t1"
    assert captured["TEROK_KIND"] == "run"


def test_make_sink_falls_back_to_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(oc, "_journald_available", lambda: False)
    monkeypatch.setattr(oc, "_log_file_path", lambda kind, project, task_id: tmp_path / "x.log")
    sink = oc._make_sink("build", "proj", None)
    try:
        assert isinstance(sink, oc._FileSink)
    finally:
        sink.close()


def test_log_file_path_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(oc, "_logs_dir", lambda project: tmp_path)
    run_path = oc._log_file_path("run", "proj", "t1")
    build_path = oc._log_file_path("build", "proj", None)
    assert run_path.name.startswith("run-t1-") and run_path.suffix == ".log"
    assert build_path.name.startswith("build-") and "None" not in build_path.name


# ── end-to-end tee (non-tty pipe path, deterministic under capfd) ──────


def test_tee_output_forwards_live_and_persists_to_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "run.log"
    monkeypatch.setattr(oc, "_journald_available", lambda: False)
    monkeypatch.setattr(oc, "_log_file_path", lambda kind, project, task_id: log_path)

    with oc.tee_output("run", project="proj", task_id="t1"):
        os.write(1, b"direct-fd-output\n")
        subprocess.run(["printf", "subprocess-output\\n"], check=True)

    live = capfd.readouterr()
    logged = log_path.read_text()
    # Live stream still reached the terminal (pytest's captured fd)...
    assert "direct-fd-output" in live.out
    assert "subprocess-output" in live.out
    # ...and the same bytes were persisted.
    assert "direct-fd-output" in logged
    assert "subprocess-output" in logged
    # Discoverability hint names the saved file.
    assert str(log_path) in live.err
