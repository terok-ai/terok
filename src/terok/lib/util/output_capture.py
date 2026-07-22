# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tee build/run output to a durable sink while the live terminal stays intact.

[`tee_output`][terok.lib.util.output_capture.tee_output] wraps an operation
(image build, task launch) and copies every byte its subprocesses write to
stdout/stderr into a durable **sink**, without disturbing the live terminal:

* a pseudo-terminal fronts the wrapped block when stdout is a TTY, so podman
  still sees ``isatty(1) == True`` and keeps its colour + progress output —
  the forwarded bytes are byte-for-byte what the operator would have seen;
* a plain pipe is used when stdout is not a TTY (redirected / CI), matching
  today's non-interactive behaviour while still capturing the stream.

The sink is chosen by environment, not by the caller:

* **journald** (native datagram protocol, no third-party binding) when its
  socket is present — retention/rotation then belongs to journald, and the
  entries are queryable with ``journalctl -t terok``;
* an **unlimited per-project log file** otherwise, documented as the fallback
  for non-systemd hosts (see ``examples/logrotate/terok.conf`` to cap growth).

Only the capture seam lives here; the CLI command handlers decide *what* to
wrap, so the TUI — which drives the same launch/build code through its own
output capture — is never touched.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import struct
import sys
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

_JOURNALD_SOCKET = Path("/run/systemd/journal/socket")
"""Local journald datagram socket — its presence is the systemd-is-here probe."""

_SYSLOG_IDENTIFIER = "terok"
"""``SYSLOG_IDENTIFIER`` stamped on every journald entry (``journalctl -t terok``)."""

_PRIORITY_INFO = "6"
"""syslog ``info`` priority for captured output lines."""

_READ_CHUNK = 65536
"""Bytes pulled per pump-thread read from the capture fd."""

_LOG_FILE_MODE = 0o600
"""Owner-only permissions for a bespoke log file (matches the terok log posture)."""

_SIGWINCH = getattr(signal, "SIGWINCH", None)
"""Terminal-resize signal, or ``None`` on platforms without it."""


# ── journald native protocol ───────────────────────────────────────────


def _encode_field(name: str, value: bytes) -> bytes:
    """Encode one journal field in the native export format.

    Newline-free values use the compact ``NAME=value\\n`` form; values that
    contain a newline switch to the ``NAME\\n<64-bit LE length><value>\\n``
    binary form journald mandates for multi-line data.
    """
    if b"\n" in value:
        return name.encode() + b"\n" + struct.pack("<Q", len(value)) + value + b"\n"
    return name.encode() + b"=" + value + b"\n"


def _encode_fields(fields: dict[str, str]) -> bytes:
    """Concatenate encoded journal fields into one datagram body."""
    return b"".join(_encode_field(k, v.encode()) for k, v in fields.items())


def _journald_available() -> bool:
    """Return True when a local journald datagram socket is accepting."""
    try:
        return _JOURNALD_SOCKET.is_socket()
    except OSError:
        return False


# ── sinks ──────────────────────────────────────────────────────────────


class _Sink(Protocol):
    """A durable destination for captured output bytes."""

    def write(self, data: bytes) -> None:
        """Absorb a chunk of captured bytes (never raises to the pump)."""

    def close(self) -> None:
        """Flush any buffered tail and release resources."""

    def hint(self) -> str:
        """One-line, operator-facing pointer to where the output landed."""


class _FileSink:
    """Append captured bytes verbatim to a per-operation log file."""

    def __init__(self, path: Path) -> None:
        """Create (truncating) the owner-only log file at *path*."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _LOG_FILE_MODE)

    def write(self, data: bytes) -> None:
        """Write *data* to the log file, ignoring partial-write short counts."""
        _write_all(self._fd, data)

    def close(self) -> None:
        """Close the underlying file descriptor."""
        os.close(self._fd)

    def hint(self) -> str:
        """Point the operator at the saved log file."""
        return f"output saved to {self._path}"


class _JournaldSink:
    """Line-buffer captured bytes into structured journald entries.

    Splits the byte stream on ``\\n`` and emits one entry per line;
    carriage-return progress redraws collapse to their final rendered
    segment so an in-place progress bar becomes a single tidy line rather
    than a wall of overwrites.  Static fields (identifier, priority, the
    terok project/task/kind) are encoded once and reused per line.
    """

    def __init__(self, static_fields: dict[str, str]) -> None:
        """Connect the datagram socket and precompute the static field block."""
        self._prefix = _encode_fields(static_fields)
        self._task = static_fields.get("TEROK_TASK")
        self._kind = static_fields.get("TEROK_KIND", "")
        self._buf = bytearray()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._sock.connect(str(_JOURNALD_SOCKET))

    def write(self, data: bytes) -> None:
        """Buffer *data* and flush every complete line to the journal."""
        self._buf += data
        while (nl := self._buf.find(b"\n")) != -1:
            line = bytes(self._buf[:nl])
            del self._buf[: nl + 1]
            self._emit(line)

    def close(self) -> None:
        """Emit any buffered tail (no trailing newline) and close the socket."""
        if self._buf:
            self._emit(bytes(self._buf))
            self._buf.clear()
        self._sock.close()

    def hint(self) -> str:
        """Point the operator at the matching ``journalctl`` query."""
        query = f"journalctl -t {_SYSLOG_IDENTIFIER} TEROK_KIND={self._kind}"
        if self._task:
            query += f" TEROK_TASK={self._task}"
        return f"output logged to journald — {query}"

    def _emit(self, line: bytes) -> None:
        """Send one line as a journal entry (best-effort; never raises)."""
        if b"\r" in line:
            line = line.rsplit(b"\r", 1)[-1]  # keep only the final redraw state
        datagram = self._prefix + _encode_field("MESSAGE", line)
        with contextlib.suppress(OSError):
            self._sock.send(datagram)


def _write_all(fd: int, data: bytes) -> None:
    """Write every byte of *data* to *fd*, looping over short writes."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


# ── sink selection + placement ─────────────────────────────────────────


def _logs_dir(project: str | None) -> Path:
    """Return the log directory for *project* (or a global dir when None)."""
    from ..core.paths import core_state_dir

    base = core_state_dir()
    if project and os.sep not in project and ".." not in project:
        return base / "projects" / project / "logs"
    return base / "logs"


def _log_file_path(kind: str, project: str | None, task_id: str | None) -> Path:
    """Build a timestamped log-file path for one *kind* of operation."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{kind}-{task_id}-{stamp}" if task_id else f"{kind}-{stamp}"
    return _logs_dir(project) / f"{stem}.log"


def _make_sink(kind: str, project: str | None, task_id: str | None) -> _Sink:
    """Pick the journald sink when systemd is present, else a bespoke file."""
    if _journald_available():
        fields = {
            "SYSLOG_IDENTIFIER": _SYSLOG_IDENTIFIER,
            "PRIORITY": _PRIORITY_INFO,
            "TEROK_KIND": kind,
        }
        if project:
            fields["TEROK_PROJECT"] = project
        if task_id:
            fields["TEROK_TASK"] = task_id
        return _JournaldSink(fields)
    return _FileSink(_log_file_path(kind, project, task_id))


# ── capture seam ───────────────────────────────────────────────────────


def _copy_winsize(src_fd: int, dst_fd: int) -> None:
    """Copy the terminal window size from *src_fd* onto *dst_fd* (best-effort)."""
    import fcntl
    import termios

    with contextlib.suppress(OSError):
        packed = fcntl.ioctl(src_fd, termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(dst_fd, termios.TIOCSWINSZ, packed)


@contextlib.contextmanager
def _capture(sink: _Sink) -> Iterator[None]:
    """Redirect fd 1/2 through a pty (or pipe) and pump every byte to *sink*.

    A reader thread copies bytes to both the real terminal (so the live
    stream is unchanged) and *sink*.  A pty is used when the real stdout is
    a TTY so downstream isatty checks still pass; a plain pipe otherwise.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    saved_out, saved_err = os.dup(1), os.dup(2)
    is_tty = os.isatty(saved_out)
    if is_tty:
        master, slave = os.openpty()
        _copy_winsize(saved_out, slave)
    else:
        master, slave = os.pipe()

    def _pump() -> None:
        """Forward capture-fd bytes to the terminal and the sink until EOF."""
        while True:
            try:
                data = os.read(master, _READ_CHUNK)
            except OSError:
                break  # EIO once the slave side is fully closed
            if not data:
                break
            _write_all(saved_out, data)
            with contextlib.suppress(Exception):
                sink.write(data)  # a failing sink must never break live output

    reader = threading.Thread(target=_pump, name="terok-output-tee", daemon=True)
    prev_winch = None
    try:
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        reader.start()
        if is_tty and _SIGWINCH is not None:
            with contextlib.suppress(ValueError):  # not in the main thread
                prev_winch = signal.getsignal(_SIGWINCH)
                signal.signal(_SIGWINCH, lambda *_: _copy_winsize(saved_out, slave))
        yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        if prev_winch is not None and _SIGWINCH is not None:
            signal.signal(_SIGWINCH, prev_winch)
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(slave)  # drop the last writer so the pump reads EOF
        reader.join()
        for fd in (master, saved_out, saved_err):
            os.close(fd)


@contextlib.contextmanager
def tee_output(
    kind: str, *, project: str | None = None, task_id: str | None = None
) -> Iterator[None]:
    """Capture the wrapped operation's output to journald or a log file.

    *kind* labels the operation (``"build"`` / ``"run"``) and, with
    *project* / *task_id*, drives both the journald fields and the log
    filename.  Capture is best-effort: if the pty/pipe seam cannot be set
    up the operation still runs, un-teed, so logging never blocks a launch.

    Args:
        kind: Operation label — ``"build"`` or ``"run"``.
        project: Owning project name, when known.
        task_id: Owning task id, when known.
    """
    try:
        sink = _make_sink(kind, project, task_id)
    except Exception:  # noqa: BLE001 — durability is a bonus, never a blocker
        yield
        return
    try:
        with _capture(sink):
            yield
    finally:
        with contextlib.suppress(Exception):
            sink.close()
        print(f"↳ {sink.hint()}", file=sys.stderr)
