"""The control protocol shared by the daemon and the interface.

Newline-delimited JSON over a Unix stream socket.  Chosen over D-Bus because
the daemon must run without a session bus -- it starts before, and can outlive,
any graphical session -- and because a socket in ``XDG_RUNTIME_DIR`` gets the
right access control from the filesystem for free.

The socket accepts commands that synthesise input, so it is treated as a
privileged interface: its directory is created mode 0700 and the socket itself
mode 0600, restricting it to the owning user.

Communication is request/response only.  The interface polls
:data:`Command.STATUS` while its window is visible rather than the daemon
pushing updates, which keeps both ends simple and stateless; a poll costs a few
hundred bytes and there is nothing to resynchronise after a reconnect.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

__all__ = ["PROTOCOL_VERSION", "Command", "encode", "decode", "ok", "error"]

#: Bumped when a change would make an older interface misread a daemon reply.
PROTOCOL_VERSION = 1

#: Guard against a malformed or hostile peer sending an unbounded line.
MAX_MESSAGE_BYTES = 1 << 20


class Command(str, Enum):
    """Commands the interface may send to the daemon."""

    PING = "ping"
    """Liveness and version check."""

    STATUS = "status"
    """Current master switch, counts, and which macros are running."""

    RELOAD = "reload"
    """Re-read macros, bindings and settings from disk."""

    STOP_ALL = "stop_all"
    """Stop every running macro and release anything held."""

    RUN_MACRO = "run_macro"
    """Play a macro on demand; used by the editor's Test command."""

    SET_SCREEN = "set_screen"
    """Report the desktop size, for scaling absolute pointer coordinates."""

    SHUTDOWN = "shutdown"
    """Ask the daemon to exit."""


def encode(payload: dict[str, Any]) -> bytes:
    """Serialise one message, including its terminating newline."""
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def decode(line: bytes) -> dict[str, Any]:
    """Parse one message.

    :raises ValueError: if the line is oversized, not UTF-8, not JSON, or not a
        JSON object.
    """
    if len(line) > MAX_MESSAGE_BYTES:
        raise ValueError("message too large")
    try:
        payload = json.loads(line.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("message is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"message is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("message must be a JSON object")
    return payload


def ok(**fields: Any) -> dict[str, Any]:
    """Build a success reply."""
    return {"ok": True, **fields}


def error(message: str) -> dict[str, Any]:
    """Build a failure reply."""
    return {"ok": False, "error": message}
