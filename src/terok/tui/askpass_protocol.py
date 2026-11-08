# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Wire protocol between ``terok-askpass`` and the TUI askpass listener.

Newline-delimited UTF-8 JSON — one object per line, both directions.
Passphrases are JSON strings so any newlines or quotes inside them are
escaped; we never need binary framing.

Shape:

- helper → TUI: ``{"request_id": "<uuid>", "prompt": "<ssh-prompt>",
  "action_id": 1, "external": false}``
- TUI → helper (accept): ``{"request_id": "<uuid>", "answer": "<pass>"}``
- TUI → helper (cancel): ``{"request_id": "<uuid>", "cancel": true}``

``action_id`` associates the prompt with a console action; ``null`` means
unattributed. For external GUI helpers, the connection stays open until
the helper exits. No answer passes through this protocol in that case.

The module has no I/O.  [`encode`][terok.tui.askpass_protocol.encode] turns a dict into the wire
bytes, [`decode`][terok.tui.askpass_protocol.decode] turns a line of wire bytes back into a dict,
and [`parse_request`][terok.tui.askpass_protocol.parse_request] / [`parse_reply`][terok.tui.askpass_protocol.parse_reply] validate the decoded
dicts on the receiving side.  Callers adapt the bytes to whatever
transport they have — a blocking ``socket.recv`` loop in the helper,
an ``asyncio.StreamReader.readline`` in the service.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

ACTION_ID_ENV = "TEROK_CONSOLE_ACTION_ID"
GUI_HELPER_ENV = "TEROK_ASKPASS_GUI_HELPER"


class AskpassProtocolError(ValueError):
    """Raised on a malformed frame — missing keys, wrong types, bad JSON."""


def encode(obj: dict[str, Any]) -> bytes:
    """Serialise *obj* to a newline-terminated UTF-8 JSON frame."""
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def decode(line: bytes) -> dict[str, Any]:
    """Parse a single UTF-8 JSON line; strip the trailing ``\\n`` if present.

    Raises [`AskpassProtocolError`][terok.tui.askpass_protocol.AskpassProtocolError] on malformed input rather than
    the stdlib [`json.JSONDecodeError`][json.JSONDecodeError] / [`UnicodeDecodeError`][UnicodeDecodeError],
    so callers have one exception type to catch.
    """
    try:
        text = line.decode("utf-8").rstrip("\n")
        obj = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AskpassProtocolError(f"malformed frame: {exc}") from exc
    if not isinstance(obj, dict):
        raise AskpassProtocolError(f"frame is not a JSON object: {type(obj).__name__}")
    return obj


def make_request(
    prompt: str,
    *,
    request_id: str | None = None,
    action_id: int | None = None,
    external: bool = False,
) -> dict[str, Any]:
    """Build a helper → TUI request.  *request_id* defaults to a fresh uuid4."""
    return {
        "request_id": request_id or uuid.uuid4().hex,
        "prompt": prompt,
        "action_id": action_id,
        "external": external,
    }


def make_answer(request_id: str, answer: str) -> dict[str, Any]:
    """Build a TUI → helper accept reply."""
    return {"request_id": request_id, "answer": answer}


def make_cancel(request_id: str) -> dict[str, Any]:
    """Build a TUI → helper cancel reply — helper exits non-zero."""
    return {"request_id": request_id, "cancel": True}


@dataclass(frozen=True)
class AskpassRequest:
    """One SSH prompt and the console action waiting for its answer."""

    request_id: str
    prompt: str
    action_id: int | None = None
    external: bool = False


def parse_request(frame: dict[str, Any]) -> AskpassRequest:
    """Validate the prompt, action identity, and external-helper flag."""
    request_id = frame.get("request_id")
    prompt = frame.get("prompt")
    if not isinstance(request_id, str) or not request_id:
        raise AskpassProtocolError("request missing non-empty 'request_id'")
    if not isinstance(prompt, str):
        raise AskpassProtocolError("request missing string 'prompt'")
    action_id = frame.get("action_id")
    if action_id is not None and (type(action_id) is not int or action_id < 1):
        raise AskpassProtocolError("request 'action_id' must be a positive integer or null")
    external = frame.get("external", False)
    if type(external) is not bool:
        raise AskpassProtocolError("request 'external' must be a boolean")
    return AskpassRequest(request_id, prompt, action_id, external)


def parse_reply(frame: dict[str, Any]) -> tuple[str, str | None]:
    """Validate a TUI → helper reply, return ``(request_id, answer_or_None)``.

    ``answer_or_None`` is ``None`` for cancel, the passphrase string for
    accept.  An empty string is a valid passphrase (some keys have none)
    — only the explicit ``cancel: true`` marker aborts the helper.

    Cancel and answer are mutually exclusive: a frame that carries both
    is ambiguous (which wins?) and almost certainly a sender bug, so
    raise rather than silently picking one — the [`make_cancel`][terok.tui.askpass_protocol.make_cancel] /
    [`make_answer`][terok.tui.askpass_protocol.make_answer] factories are the only sanctioned way to build
    a reply on our side.
    """
    request_id = frame.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise AskpassProtocolError("reply missing non-empty 'request_id'")
    is_cancel = frame.get("cancel") is True
    has_answer = "answer" in frame
    if is_cancel and has_answer:
        raise AskpassProtocolError(
            "reply has both 'cancel: true' and 'answer' — exactly one is allowed"
        )
    if is_cancel:
        return request_id, None
    answer = frame.get("answer")
    if not isinstance(answer, str):
        raise AskpassProtocolError("reply missing 'cancel' or string 'answer'")
    return request_id, answer
