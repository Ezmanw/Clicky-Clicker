"""Macro playback.

Each run happens on its own thread so that neither the interface nor the
daemon's event loop can be blocked by a macro that waits for a minute.  Stopping
is cooperative and immediate: every sleep waits on the stop event rather than on
the clock, so a stop request never has to wait out a pending delay.

Two invariants matter more than anything else here:

* **Nothing stays held.**  A run records every key it presses and releases them
  on the way out, whatever the reason for stopping -- normal completion, a stop
  request, or an exception.  Without that, a macro interrupted between a press
  and its matching release would leave a key stuck down for the whole session.
* **A run is always stoppable.**  :meth:`MacroExecutor.stop_all` is reachable
  from the interface, from the daemon's emergency-stop combination, and from
  daemon shutdown.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from ..input.backend import InputSink
from ..models import Macro, MacroAction
from ..models.action import ActionType

log = logging.getLogger(__name__)

__all__ = ["RunHandle", "MacroExecutor"]

#: Sleeps shorter than this are not worth a thread round-trip; the scheduler
#: cannot honour them anyway.  Waits below it become a no-op yield.
_MIN_SLEEP = 0.0005


class RunHandle:
    """One in-flight macro run."""

    def __init__(
        self,
        macro: Macro,
        sink: InputSink,
        iterations: int | None,
        on_finished: Callable[["RunHandle"], None],
        on_step: Callable[["RunHandle", int], None] | None = None,
        on_error: Callable[["RunHandle", Exception], None] | None = None,
    ) -> None:
        self.macro = macro
        self.key = macro.id
        self._sink = sink
        self._iterations = iterations
        self._on_finished = on_finished
        self._on_step = on_step
        self._on_error = on_error
        self._stop = threading.Event()
        self._held: list[str] = []
        self._thread = threading.Thread(
            target=self._run, name=f"macro-{macro.name[:24]}", daemon=True
        )
        self.completed_iterations = 0
        self.error: Exception | None = None

    # --- Control --------------------------------------------------------

    def start(self) -> None:
        """Begin playback on a background thread."""
        self._thread.start()

    def stop(self) -> None:
        """Ask the run to finish as soon as possible.  Returns immediately."""
        self._stop.set()

    @property
    def is_running(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the run to finish."""
        self._thread.join(timeout)

    # --- Playback -------------------------------------------------------

    def _run(self) -> None:
        gap = self.macro.playback.gap_ms / 1000.0
        try:
            iteration = 0
            while not self._stop.is_set():
                if self._iterations is not None and iteration >= self._iterations:
                    break
                self._run_once()
                iteration += 1
                self.completed_iterations = iteration

                more = self._iterations is None or iteration < self._iterations
                if more and gap and not self._stop.is_set():
                    self._sleep(gap)
        except Exception as exc:  # noqa: BLE001 - reported, never propagated to a thread
            self.error = exc
            log.exception("macro %r failed", self.macro.name)
            if self._on_error is not None:
                self._on_error(self, exc)
        finally:
            self._release_held()
            self._on_finished(self)

    def _run_once(self) -> None:
        """Play a single pass through the action list."""
        for index, action in enumerate(self.macro.actions):
            if self._stop.is_set():
                return
            if not action.enabled:
                continue
            if self._on_step is not None:
                self._on_step(self, index)
            self._perform(action)

    def _perform(self, action: MacroAction) -> None:
        """Dispatch one action onto the sink."""
        params = action.params
        kind = action.type

        if kind is ActionType.WAIT:
            self._sleep(_ms(params.get("duration_ms")) / 1000.0)

        elif kind is ActionType.KEY_PRESS:
            self._press(str(params.get("key", "")))
        elif kind is ActionType.KEY_RELEASE:
            self._release(str(params.get("key", "")))
        elif kind is ActionType.KEY_TAP:
            self._tap(str(params.get("key", "")), _ms(params.get("hold_ms")))
        elif kind is ActionType.KEY_COMBO:
            self._combo([str(k) for k in params.get("keys") or []], _ms(params.get("hold_ms")))

        elif kind is ActionType.BUTTON_PRESS:
            self._press(str(params.get("button", "")))
        elif kind is ActionType.BUTTON_RELEASE:
            self._release(str(params.get("button", "")))
        elif kind is ActionType.BUTTON_CLICK:
            self._tap(str(params.get("button", "")), _ms(params.get("hold_ms")))

        elif kind is ActionType.MOUSE_MOVE:
            self._sink.move_absolute(_int(params.get("x")), _int(params.get("y")))
        elif kind is ActionType.MOUSE_MOVE_RELATIVE:
            self._sink.move_relative(_int(params.get("dx")), _int(params.get("dy")))
        elif kind is ActionType.MOUSE_CLICK_AT:
            self._sink.move_absolute(_int(params.get("x")), _int(params.get("y")))
            # A compositor needs the motion to land before the button event, or
            # the click is delivered at the previous position.
            self._sleep(0.002)
            self._tap(str(params.get("button", "")), _ms(params.get("hold_ms")))

        elif kind is ActionType.SCROLL:
            self._sink.scroll(_int(params.get("amount")), bool(params.get("horizontal")))

        else:  # pragma: no cover - unreachable while ActionType is exhaustive
            log.warning("no handler for action type %r", kind)

    # --- Primitives -----------------------------------------------------

    def _press(self, code: str) -> None:
        if not code:
            return
        self._sink.key(code, True)
        self._held.append(code)

    def _release(self, code: str) -> None:
        if not code:
            return
        self._sink.key(code, False)
        if code in self._held:
            self._held.remove(code)

    def _tap(self, code: str, hold_ms: int) -> None:
        if not code:
            return
        self._press(code)
        if hold_ms:
            self._sleep(hold_ms / 1000.0)
        self._release(code)

    def _combo(self, codes: list[str], hold_ms: int) -> None:
        if not codes:
            return
        for code in codes:
            self._press(code)
        if hold_ms:
            self._sleep(hold_ms / 1000.0)
        for code in reversed(codes):
            self._release(code)

    def _sleep(self, seconds: float) -> None:
        """Wait, but wake immediately if the run is asked to stop.

        Resolution is whatever the kernel scheduler provides, typically around a
        millisecond.  A macro asking for a 1 ms delay will get approximately
        that, not exactly that.
        """
        if seconds < _MIN_SLEEP:
            return
        self._stop.wait(seconds)

    def _release_held(self) -> None:
        """Release everything this run pressed, innermost first."""
        for code in reversed(self._held):
            try:
                self._sink.key(code, False)
            except Exception:  # noqa: BLE001 - cleanup must not raise
                log.exception("failed to release %s", code)
        self._held.clear()


class MacroExecutor:
    """Owns the sink and every in-flight run."""

    def __init__(self, sink: InputSink) -> None:
        self._sink = sink
        self._lock = threading.RLock()
        self._runs: dict[str, RunHandle] = {}
        self.on_changed: Callable[[], None] | None = None
        """Called whenever the set of running macros changes."""
        self.on_error: Callable[[Macro, Exception], None] | None = None

    # --- Queries --------------------------------------------------------

    @property
    def sink(self) -> InputSink:
        return self._sink

    def is_running(self, key: str) -> bool:
        """Whether a run is active for *key* (a macro id, or a binding id)."""
        with self._lock:
            handle = self._runs.get(key)
            return handle is not None and handle.is_running

    def running_keys(self) -> list[str]:
        """Keys of every active run."""
        with self._lock:
            return [key for key, handle in self._runs.items() if handle.is_running]

    @property
    def active_count(self) -> int:
        return len(self.running_keys())

    # --- Control --------------------------------------------------------

    def start(
        self,
        macro: Macro,
        *,
        key: str | None = None,
        iterations: int | None = None,
        on_step: Callable[[RunHandle, int], None] | None = None,
    ) -> RunHandle | None:
        """Start *macro*, unless a run is already active under the same key.

        :param key: identity of this run, defaulting to the macro id.  The
            daemon passes the binding id so the same macro bound to two inputs
            can run twice at once.
        :param iterations: overrides the macro's own playback count.  Used by
            the interface's Test command, which always runs a single pass.
        :returns: the handle, or ``None`` if a run was already active, or if the
            macro has nothing to do.
        """
        if not any(action.enabled for action in macro.actions):
            return None

        run_key = key or macro.id
        with self._lock:
            existing = self._runs.get(run_key)
            if existing is not None and existing.is_running:
                return None

            count = macro.playback.iterations() if iterations is None else iterations
            handle = RunHandle(
                macro=macro,
                sink=self._sink,
                iterations=count,
                on_finished=self._finished,
                on_step=on_step,
                on_error=self._errored,
            )
            handle.key = run_key
            self._runs[run_key] = handle

        handle.start()
        self._notify()
        return handle

    def stop(self, key: str) -> None:
        """Stop the run registered under *key*, if any."""
        with self._lock:
            handle = self._runs.get(key)
        if handle is not None:
            handle.stop()

    def toggle(self, macro: Macro, *, key: str | None = None) -> bool:
        """Start the macro if idle, stop it if running.

        :returns: ``True`` if a run was started.
        """
        run_key = key or macro.id
        if self.is_running(run_key):
            self.stop(run_key)
            return False
        return self.start(macro, key=run_key) is not None

    def stop_all(self) -> int:
        """Stop every run and release anything still held.

        This is the emergency stop.  It is deliberately blunt: after asking each
        run to stop it also calls ``release_all`` on the sink, so even a run
        that is wedged cannot leave a key held down.

        :returns: how many runs were asked to stop.
        """
        with self._lock:
            handles = list(self._runs.values())

        for handle in handles:
            handle.stop()
        for handle in handles:
            handle.join(timeout=1.0)

        try:
            self._sink.release_all()
        except Exception:  # noqa: BLE001 - emergency path must not raise
            log.exception("failed to release held keys during stop_all")

        with self._lock:
            self._runs = {
                key: handle for key, handle in self._runs.items() if handle.is_running
            }
        self._notify()
        return len(handles)

    def shutdown(self) -> None:
        """Stop everything and close the sink."""
        self.stop_all()
        try:
            self._sink.close()
        except Exception:  # noqa: BLE001
            log.exception("failed to close input sink")

    # --- Internals ------------------------------------------------------

    def _finished(self, handle: RunHandle) -> None:
        with self._lock:
            if self._runs.get(handle.key) is handle:
                del self._runs[handle.key]
        self._notify()

    def _errored(self, handle: RunHandle, exc: Exception) -> None:
        if self.on_error is not None:
            self.on_error(handle.macro, exc)

    def _notify(self) -> None:
        if self.on_changed is not None:
            try:
                self.on_changed()
            except Exception:  # noqa: BLE001 - a bad observer must not kill a run
                log.exception("executor observer failed")


def _ms(value: object) -> int:
    """Coerce a stored duration to a non-negative integer of milliseconds."""
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
