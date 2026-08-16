"""Entry point for ``clicky-clicker-daemon``.

The daemon is what makes mappings work when the window is closed.  It is
started by a **systemd user service** rather than by a hand-rolled background
process: systemd already handles starting it at login, restarting it if it
crashes, capturing its log, and stopping it at logout, and using it means there
is no bespoke supervision code to get wrong.

It holds no display connection and imports no GTK, so it runs identically under
GNOME, COSMIC, a bare compositor, or a TTY.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import threading

from ..input.errors import InputError
from ..persistence import paths
from .engine import Engine
from .server import ControlServer

log = logging.getLogger(__name__)

__all__ = ["main"]


def _configure_logging(verbose: bool) -> None:
    """Log to stderr under systemd, and to a file otherwise.

    systemd captures stderr into the journal, so a file would be redundant --
    and worse, would be the only copy if the journal is where the user looks.
    """
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]

    if not os.environ.get("JOURNAL_STREAM") and not os.environ.get("INVOCATION_ID"):
        try:
            paths.state_dir().mkdir(parents=True, exist_ok=True)
            handlers.append(
                logging.handlers.RotatingFileHandler(
                    paths.log_file(), maxBytes=512_000, backupCount=2, encoding="utf-8"
                )
            )
        except OSError:
            pass  # stderr alone is enough; a missing log file is not fatal.

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="clicky-clicker-daemon",
        description=(
            "Background service that reads input devices and applies Clicky "
            "Clicker's mappings and macros."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log at debug level")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that input devices and /dev/uinput are usable, then exit",
    )
    return parser.parse_args(argv)


def _run_check() -> int:
    """Report on the prerequisites without starting anything."""
    from ..input import probe  # noqa: PLC0415 - only needed for this path

    report = probe()
    for capability in (report.evdev_module, report.device_read, report.uinput_write):
        marker = "ok  " if capability.available else "FAIL"
        print(f"[{marker}] {capability.summary}")
        if not capability.available and capability.remedy:
            for line in capability.remedy.splitlines():
                print(f"        {line}")
    print()
    print("Ready." if report.ready else "Not ready — resolve the items above.")
    return 0 if report.ready else 1


def main(argv: list[str] | None = None) -> int:
    """Run the daemon until it is asked to stop."""
    args = _parse_args(argv)
    _configure_logging(args.verbose)

    if args.check:
        return _run_check()

    paths.ensure_directories()

    engine = Engine()
    server = ControlServer(engine, paths.socket_path())
    stopping = threading.Event()

    def request_stop(*_: object) -> None:
        if not stopping.is_set():
            stopping.set()
            log.info("shutting down")
            engine.stop()

    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, request_stop)

    try:
        server.start()
    except OSError as exc:
        log.error("cannot start the control socket: %s", exc)
        return 1

    # The engine blocks in its read loop, so the shutdown command has to be
    # watched from elsewhere.
    watcher = threading.Thread(
        target=_watch_shutdown, args=(server, request_stop), name="shutdown-watch", daemon=True
    )
    watcher.start()

    try:
        engine.start()
    except InputError as exc:
        log.error("%s: %s", exc.title, exc.detail or "no further detail")
        if exc.remedy:
            for line in exc.remedy.splitlines():
                log.error("  %s", line)
        return 1
    except Exception:  # noqa: BLE001 - always log the reason before exiting
        log.exception("the engine stopped unexpectedly")
        return 1
    finally:
        server.stop()

    return 0


def _watch_shutdown(server: ControlServer, request_stop: "object") -> None:
    server.on_shutdown_requested.wait()
    request_stop()  # type: ignore[operator]


if __name__ == "__main__":
    raise SystemExit(main())
