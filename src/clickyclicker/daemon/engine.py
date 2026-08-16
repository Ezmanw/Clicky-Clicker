"""The engine: turns physical input events into macro runs and remaps.

This is where a keypress becomes behaviour.  It owns the input source, the
input sink and the executor, and it is the only place that knows how a
:class:`~clickyclicker.models.binding.Binding` translates into an action.

Trigger and playback interact, so the rule is stated once here and nowhere else:

* the **trigger mode** decides when a run *starts* and *stops*;
* the **playback mode** decides how many passes a run makes.

``Repeat while held`` and ``Toggle`` are playback modes whose bound comes from
the trigger rather than from a counter, so they pin the trigger mode --
:meth:`~clickyclicker.models.macro.Macro.effective_trigger` resolves that, and
the interface shows the resulting behaviour in plain English.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from ..input import create_sink, create_source
from ..input.backend import InputEvent, InputSource, KeyState
from ..input.evdev_backend import SUPPRESS_ANY_DEVICE
from ..macros.executor import MacroExecutor
from ..models import Binding, BindingKind, Macro, Settings, TriggerMode
from ..persistence import MacroStore

log = logging.getLogger(__name__)

__all__ = ["Engine"]


class Engine:
    """Installs bindings and reacts to input."""

    def __init__(self, store: MacroStore | None = None) -> None:
        self._store = store or MacroStore()
        self._lock = threading.RLock()

        self._source: InputSource | None = None
        self._executor: MacroExecutor | None = None

        self._settings = Settings()
        self._macros: dict[str, Macro] = {}
        self._bindings: list[Binding] = []
        self._by_code: dict[str, list[Binding]] = {}

        self._physically_held: set[str] = set()
        self._emergency_armed = False

        self.on_status_changed: Callable[[], None] | None = None
        """Called when the set of running macros changes, so the server can
        push a status update to any connected interface."""

        self.last_error: str = ""

    # --- Lifecycle ------------------------------------------------------

    def start(self) -> None:
        """Open the devices and begin dispatching.  Blocks until :meth:`stop`.

        :raises BackendUnavailableError: if python-evdev is missing.
        :raises UinputUnavailableError: if the virtual devices cannot be made.
        :raises PermissionDeniedError: if no input device can be read.
        """
        self.reload()

        sink = create_sink()
        sink.open()
        executor = MacroExecutor(sink)
        executor.on_changed = self._status_changed
        executor.on_error = self._macro_errored

        source = create_source(excluded_devices=set(self._settings.excluded_devices))
        with self._lock:
            self._executor = executor
            self._source = source

        self._apply_suppression()
        log.info("engine started with %d binding(s)", len(self._bindings))
        try:
            source.listen(self._on_event)
        finally:
            executor.shutdown()
            with self._lock:
                self._executor = None
                self._source = None
            log.info("engine stopped")

    def stop(self) -> None:
        """Ask the engine to shut down.  Safe to call from another thread."""
        with self._lock:
            source, executor = self._source, self._executor
        if executor is not None:
            executor.stop_all()
        if source is not None:
            source.stop()

    # --- Configuration --------------------------------------------------

    def reload(self) -> None:
        """Re-read macros, bindings and settings from disk and reinstall them.

        Called at start-up and whenever the interface reports that it saved
        something, so edits take effect without restarting the service.
        """
        settings = self._store.load_settings()
        macros = {macro.id: macro for macro in self._store.load_macros()}
        binding_set = self._store.load_bindings()

        usable: list[Binding] = []
        for binding in binding_set.bindings:
            if not binding.enabled:
                continue
            if not binding.is_valid(set(macros)):
                log.warning(
                    "ignoring binding on %s: it is incomplete or its macro is missing",
                    binding.input,
                )
                continue
            usable.append(binding)

        by_code: dict[str, list[Binding]] = {}
        if settings.enabled:
            for binding in usable:
                by_code.setdefault(binding.input, []).append(binding)

        with self._lock:
            self._settings = settings
            self._macros = macros
            self._bindings = usable
            self._by_code = by_code

        self._apply_suppression()
        log.info(
            "loaded %d macro(s), %d active binding(s), master switch %s",
            len(macros),
            len(by_code),
            "on" if settings.enabled else "off",
        )

    def _apply_suppression(self) -> None:
        """Tell the source which codes to withhold from the rest of the session."""
        with self._lock:
            source = self._source
            bindings = list(self._bindings)
            enabled = self._settings.enabled

        if source is None:
            return

        suppressed: dict[str, set[str] | None] = {}
        if enabled:
            for binding in bindings:
                if not binding.effective_suppress():
                    continue
                target = binding.device_id or SUPPRESS_ANY_DEVICE
                suppressed.setdefault(target, set()).add(binding.input)  # type: ignore[union-attr]
        source.set_suppressed(suppressed)

    def set_screen_size(self, width: int, height: int) -> None:
        """Record the desktop size used to scale absolute pointer coordinates.

        The daemon has no display connection, so the interface supplies this.
        """
        with self._lock:
            executor = self._executor
        if executor is not None:
            sink = executor.sink
            setter = getattr(sink, "set_screen_size", None)
            if setter is not None:
                setter(width, height)

    # --- Status ---------------------------------------------------------

    def status(self) -> dict[str, object]:
        """A snapshot for the interface's status display."""
        with self._lock:
            executor = self._executor
            enabled = self._settings.enabled
            macro_count = len(self._macros)
            binding_count = len(self._bindings)

        running = executor.running_keys() if executor is not None else []
        running_macros = []
        with self._lock:
            for key in running:
                binding = next((b for b in self._bindings if b.id == key), None)
                macro_id = binding.macro_id if binding else key
                macro = self._macros.get(macro_id or "")
                if macro is not None:
                    running_macros.append({"id": macro.id, "name": macro.name})

        return {
            "enabled": enabled,
            "macros": macro_count,
            "bindings": binding_count,
            "running": running_macros,
            "last_error": self.last_error,
        }

    def stop_all_macros(self) -> int:
        """Stop every running macro.  Returns how many were stopped."""
        with self._lock:
            executor = self._executor
        return executor.stop_all() if executor is not None else 0

    def run_macro(self, macro_id: str, *, once: bool = False) -> bool:
        """Start a macro on demand, for the interface's Test command.

        :param once: run a single pass regardless of the macro's playback mode,
            which is what Test does so a "repeat forever" macro can be tried
            safely.
        :returns: whether a run was started.
        """
        with self._lock:
            executor = self._executor
            macro = self._macros.get(macro_id)
        if executor is None or macro is None:
            return False
        return executor.start(macro, iterations=1 if once else None) is not None

    # --- Event dispatch -------------------------------------------------

    def _on_event(self, event: InputEvent) -> None:
        """Handle one physical transition.  Runs on the input reader thread."""
        if event.state is KeyState.HELD:
            # Auto-repeat: the key is already down, so it must not re-trigger.
            return

        pressed = event.state is KeyState.PRESSED
        if pressed:
            self._physically_held.add(event.code)
        else:
            self._physically_held.discard(event.code)

        if self._check_emergency_stop():
            return

        with self._lock:
            bindings = list(self._by_code.get(event.code, ()))
            executor = self._executor

        if executor is None:
            return

        for binding in bindings:
            if binding.device_id and binding.device_id != event.device_id:
                continue
            try:
                self._apply(binding, pressed, executor)
            except Exception as exc:  # noqa: BLE001 - one bad binding must not stop the loop
                self.last_error = str(exc)
                log.exception("binding on %s failed", binding.input)

    def _check_emergency_stop(self) -> bool:
        """Stop everything if the emergency combination is fully held.

        Latched so that holding the combination stops macros once rather than on
        every subsequent key event.  Never suppressed and never disabled by the
        master switch: this is the guaranteed way out of a runaway macro.
        """
        with self._lock:
            settings = self._settings
        if not settings.emergency_stop_enabled or not settings.emergency_stop:
            return False

        combination = set(settings.emergency_stop)
        if combination.issubset(self._physically_held):
            if not self._emergency_armed:
                self._emergency_armed = True
                stopped = self.stop_all_macros()
                log.warning("emergency stop: halted %d macro(s)", stopped)
            return True

        self._emergency_armed = False
        return False

    def _apply(self, binding: Binding, pressed: bool, executor: MacroExecutor) -> None:
        """Carry out one binding's effect for a press or release."""
        if binding.kind is BindingKind.DISABLE:
            return

        if binding.kind is BindingKind.REMAP:
            self._apply_remap(binding, pressed, executor)
            return

        with self._lock:
            macro = self._macros.get(binding.macro_id or "")
        if macro is None:
            return

        mode = binding.trigger_override or macro.effective_trigger()
        run_key = binding.id

        if mode is TriggerMode.ON_PRESS:
            if pressed:
                executor.start(macro, key=run_key)
        elif mode is TriggerMode.ON_RELEASE:
            if not pressed:
                executor.start(macro, key=run_key)
        elif mode is TriggerMode.WHILE_HELD:
            if pressed:
                # Unbounded: the release is what ends it.
                executor.start(macro, key=run_key, iterations=None)
            else:
                executor.stop(run_key)
        elif mode is TriggerMode.TOGGLE:
            if pressed:
                executor.toggle(macro, key=run_key)
        elif mode is TriggerMode.ONE_SHOT and pressed and not executor.is_running(run_key):
            executor.start(macro, key=run_key)

    def _apply_remap(self, binding: Binding, pressed: bool, executor: MacroExecutor) -> None:
        """Emit the replacement keys for a remapped input.

        Emitted in order on press and reverse order on release, so a remap to a
        combination behaves the way a real chord does.
        """
        if not binding.output:
            return
        sink = executor.sink
        codes = binding.output if pressed else list(reversed(binding.output))
        for code in codes:
            sink.key(code, pressed)

    # --- Observers ------------------------------------------------------

    def _status_changed(self) -> None:
        if self.on_status_changed is not None:
            try:
                self.on_status_changed()
            except Exception:  # noqa: BLE001
                log.exception("status observer failed")

    def _macro_errored(self, macro: Macro, exc: Exception) -> None:
        self.last_error = f"{macro.name}: {exc}"
