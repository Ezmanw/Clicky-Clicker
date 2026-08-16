"""Recording live input into an editable macro.

The recorder converts a stream of :class:`~clickyclicker.input.backend.InputEvent`
into ordinary :class:`~clickyclicker.models.action.MacroAction` objects.  There
is no separate "recorded macro" type: a recording is just a macro, so it lands
in the normal visual editor and can be trimmed, retimed and extended like any
other.

Presses and releases are recorded separately rather than being condensed into
taps.  That preserves overlapping keys -- holding Shift while typing, for
instance -- which condensing would silently destroy.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from ..input.backend import InputEvent, KeyState
from ..models import MacroAction
from ..models.action import ActionType
from ..models.keys import is_mouse_button

__all__ = ["MacroRecorder"]


@dataclass
class MacroRecorder:
    """Accumulates input events into a list of actions.

    Thread-safe: :meth:`feed` is called from the input reader's thread while the
    interface reads :meth:`actions` from the main loop.
    """

    capture_delays: bool = True
    """Whether to insert Wait actions reflecting the real timing between events."""

    minimum_delay_ms: int = 1
    """Gaps below this are dropped, which keeps recordings readable without
    meaningfully changing how they play back."""

    ignored_codes: set[str] = field(default_factory=set)
    """Inputs to leave out, such as the key used to stop recording."""

    on_changed: Callable[[int], None] | None = None
    """Called with the new action count whenever something is recorded."""

    _actions: list[MacroAction] = field(default_factory=list, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _last_timestamp: float | None = field(default=None, init=False)
    _recording: bool = field(default=False, init=False)

    # --- Control --------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        """Begin a new recording, discarding anything previously captured."""
        with self._lock:
            self._actions = []
            self._last_timestamp = None
            self._recording = True
        self._notify()

    def stop(self) -> None:
        """Stop recording.  Captured actions remain available."""
        with self._lock:
            self._recording = False

    def discard(self) -> None:
        """Stop recording and throw away everything captured."""
        with self._lock:
            self._recording = False
            self._actions = []
            self._last_timestamp = None
        self._notify()

    # --- Capture --------------------------------------------------------

    def feed(self, event: InputEvent) -> None:
        """Record one input event.  Safe to call from the reader thread."""
        if event.state is KeyState.HELD:
            # Keyboard auto-repeat, not a real transition.
            return

        with self._lock:
            if not self._recording or event.code in self.ignored_codes:
                return

            if self.capture_delays and self._last_timestamp is not None:
                gap_ms = int(round((event.timestamp - self._last_timestamp) * 1000))
                if gap_ms >= self.minimum_delay_ms:
                    self._actions.append(
                        MacroAction.create(ActionType.WAIT, duration_ms=gap_ms)
                    )
            self._last_timestamp = event.timestamp

            pressed = event.state is KeyState.PRESSED
            if is_mouse_button(event.code):
                action_type = ActionType.BUTTON_PRESS if pressed else ActionType.BUTTON_RELEASE
                self._actions.append(MacroAction.create(action_type, button=event.code))
            else:
                action_type = ActionType.KEY_PRESS if pressed else ActionType.KEY_RELEASE
                self._actions.append(MacroAction.create(action_type, key=event.code))

            count = len(self._actions)

        self._notify(count)

    # --- Results --------------------------------------------------------

    @property
    def count(self) -> int:
        """How many actions have been captured so far."""
        with self._lock:
            return len(self._actions)

    def actions(self) -> list[MacroAction]:
        """A copy of the captured actions, ready to append to a macro.

        A trailing Wait is stripped: it only measures the pause before the user
        stopped recording, which is not part of the macro.
        """
        with self._lock:
            captured = [action.duplicate() for action in self._actions]
        while captured and captured[-1].type is ActionType.WAIT:
            captured.pop()
        return captured

    def _notify(self, count: int | None = None) -> None:
        if self.on_changed is not None:
            self.on_changed(self.count if count is None else count)
