# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``terok-askpass`` — the ``SSH_ASKPASS`` helper that talks to the TUI.

OpenSSH invokes whatever binary ``SSH_ASKPASS`` points at with the
prompt as ``argv[1]`` and expects the passphrase (or a blank line) on
stdout.  When the TUI spawns a subprocess for a project with
``ssh.use_personal: true``, it points ``SSH_ASKPASS`` at this helper and
``TEROK_ASKPASS_SOCKET`` at a unix socket it's listening on.

Native prompts wait for a reply from the TUI. An advertised GUI helper
instead runs with inherited stdout, so its answer goes straight to OpenSSH.
Its socket stays open until that helper exits, marking the action as waiting
without sending the answer to the TUI.

Exit codes:

- ``0`` — user typed a passphrase (possibly empty); printed to stdout.
- ``1`` — user cancelled, or anything went wrong.  OpenSSH treats any
  non-zero exit as "askpass failed" and aborts the authentication
  attempt immediately, which is exactly what we want for cancel.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys

from . import askpass_protocol as proto

_SOCKET_ENV = "TEROK_ASKPASS_SOCKET"
_FALLBACK_PROMPT = "Passphrase:"


def _read_line(sock: socket.socket) -> bytes:
    """Blocking read from *sock* until ``\\n`` or EOF."""
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    return b"".join(chunks)


def main(argv: list[str] | None = None) -> int:
    """Entry point — returns a POSIX exit code; never raises."""
    args = argv if argv is not None else sys.argv
    prompt = args[1] if len(args) > 1 else _FALLBACK_PROMPT

    socket_path = os.environ.get(_SOCKET_ENV)
    if not socket_path:
        print(f"terok-askpass: {_SOCKET_ENV} not set", file=sys.stderr)
        return 1

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(socket_path)
            action_id = os.environ.get(proto.ACTION_ID_ENV)
            gui_helper = os.environ.get(proto.GUI_HELPER_ENV)
            request = proto.make_request(
                prompt,
                action_id=int(action_id) if action_id else None,
                external=bool(gui_helper),
            )
            sock.sendall(proto.encode(request))
            if gui_helper:
                # The answer travels straight to OpenSSH, never through the TUI log.
                env = {**os.environ, "SSH_ASKPASS": gui_helper}
                env.pop(proto.GUI_HELPER_ENV, None)
                return subprocess.run([gui_helper, *args[1:]], env=env, check=False).returncode
            raw = _read_line(sock)
    except (OSError, ValueError) as exc:
        print(f"terok-askpass: socket error: {exc}", file=sys.stderr)
        return 1

    if not raw:
        print("terok-askpass: TUI closed socket without reply", file=sys.stderr)
        return 1

    try:
        reply = proto.decode(raw)
        reply_id, answer = proto.parse_reply(reply)
    except proto.AskpassProtocolError as exc:
        print(f"terok-askpass: {exc}", file=sys.stderr)
        return 1

    if reply_id != request["request_id"]:
        print("terok-askpass: request_id mismatch", file=sys.stderr)
        return 1

    if answer is None:  # explicit cancel
        return 1

    # OpenSSH reads one line from stdout; println adds the trailing newline.
    print(answer)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    sys.exit(main())
