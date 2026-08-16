"""The macro document: a named sequence of actions plus how it should play.

A macro is self-contained and portable -- it knows nothing about which physical
input runs it.  That relationship lives in :mod:`clickyclicker.models.binding`,
which is what lets one macro be assigned to several inputs.

Because a macro is portable, a saved macro *is* a preset: exporting one is a
file copy, and importing is the same in reverse.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .action import MacroAction

__all__ = [
    "PlaybackMode",
    "TriggerMode",
    "PlaybackConfig",
    "TriggerConfig",
    "Macro",
    "FILE_VERSION",
]

#: Schema version written into every saved macro.  Bump when a change would
#: stop an older build from loading the file correctly.
FILE_VERSION = 1

#: Ceiling on repeat counts, and on gap length in milliseconds.
MAX_REPEAT_COUNT = 100_000
MAX_GAP_MS = 3_600_000


class PlaybackMode(Enum):
    """How many times a macro repeats once it has been started.

    ``WHILE_HELD`` and ``TOGGLE`` repeat without limit, but additionally pin the
    trigger behaviour -- see :meth:`Macro.effective_trigger`.  They exist as
    playback modes because that is how users describe the behaviour ("repeat
    forever until I let go"), even though mechanically the bound is enforced by
    the trigger rather than by a counter.
    """

    ONCE = "once"
    REPEAT_COUNT = "repeat_count"
    REPEAT_FOREVER = "repeat_forever"
    WHILE_HELD = "while_held"
    TOGGLE = "toggle"


class TriggerMode(Enum):
    """When activating the bound input starts or stops a run."""

    ON_PRESS = "on_press"
    ON_RELEASE = "on_release"
    WHILE_HELD = "while_held"
    TOGGLE = "toggle"
    ONE_SHOT = "one_shot"


#: Display labels, shared by the configuration page and the binding editor.
PLAYBACK_LABELS: dict[PlaybackMode, str] = {
    PlaybackMode.ONCE: "Run once",
    PlaybackMode.REPEAT_COUNT: "Repeat a set number of times",
    PlaybackMode.REPEAT_FOREVER: "Repeat forever",
    PlaybackMode.WHILE_HELD: "Repeat while held",
    PlaybackMode.TOGGLE: "Toggle on and off",
}

TRIGGER_LABELS: dict[TriggerMode, str] = {
    TriggerMode.ON_PRESS: "On press",
    TriggerMode.ON_RELEASE: "On release",
    TriggerMode.WHILE_HELD: "While held",
    TriggerMode.TOGGLE: "Toggle on and off",
    TriggerMode.ONE_SHOT: "One-shot (ignore until finished)",
}


@dataclass(slots=True)
class PlaybackConfig:
    """Repetition settings for a macro."""

    mode: PlaybackMode = PlaybackMode.ONCE
    repeat_count: int = 5
    gap_ms: int = 10
    """Delay inserted *between* repetitions.

    Distinct from any Wait action inside the macro: those run as part of a
    single pass, this one only applies when a pass is followed by another.
    """

    def clamp(self) -> None:
        """Force values into their valid ranges after loading or editing."""
        self.repeat_count = max(1, min(MAX_REPEAT_COUNT, int(self.repeat_count)))
        self.gap_ms = max(0, min(MAX_GAP_MS, int(self.gap_ms)))

    def iterations(self) -> int | None:
        """Number of passes to run, or ``None`` for unbounded."""
        if self.mode is PlaybackMode.ONCE:
            return 1
        if self.mode is PlaybackMode.REPEAT_COUNT:
            return self.repeat_count
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "repeat_count": self.repeat_count,
            "gap_ms": self.gap_ms,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "PlaybackConfig":
        config = cls()
        if isinstance(data, dict):
            try:
                config.mode = PlaybackMode(data.get("mode", PlaybackMode.ONCE.value))
            except ValueError:
                config.mode = PlaybackMode.ONCE
            config.repeat_count = _as_int(data.get("repeat_count"), config.repeat_count)
            config.gap_ms = _as_int(data.get("gap_ms"), config.gap_ms)
        config.clamp()
        return config


@dataclass(slots=True)
class TriggerConfig:
    """How the bound input starts and stops the macro."""

    mode: TriggerMode = TriggerMode.ON_PRESS

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode.value}

    @classmethod
    def from_dict(cls, data: Any) -> "TriggerConfig":
        if isinstance(data, dict):
            try:
                return cls(mode=TriggerMode(data.get("mode", TriggerMode.ON_PRESS.value)))
            except ValueError:
                pass
        return cls()


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


@dataclass(slots=True)
class Macro:
    """A named, portable macro definition."""

    name: str = "New Macro"
    description: str = ""
    actions: list[MacroAction] = field(default_factory=list)
    playback: PlaybackConfig = field(default_factory=PlaybackConfig)
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    # --- Behaviour ------------------------------------------------------

    def effective_trigger(self) -> TriggerMode:
        """The trigger mode actually used, after playback mode is applied.

        ``Repeat while held`` and ``Toggle on and off`` are unbounded playback
        modes whose bound comes from the trigger, so they override whatever the
        trigger field says.  Everything else leaves the trigger untouched.
        """
        if self.playback.mode is PlaybackMode.WHILE_HELD:
            return TriggerMode.WHILE_HELD
        if self.playback.mode is PlaybackMode.TOGGLE:
            return TriggerMode.TOGGLE
        return self.trigger.mode

    def is_unbounded(self) -> bool:
        """Whether this macro can run indefinitely without user intervention.

        ``Repeat forever`` combined with a trigger that never stops it is the
        one genuinely runaway combination, and the UI warns about it.
        """
        return (
            self.playback.mode is PlaybackMode.REPEAT_FOREVER
            and self.effective_trigger()
            not in (TriggerMode.WHILE_HELD, TriggerMode.TOGGLE)
        )

    def describe_behaviour(self, trigger_label: str = "the trigger") -> str:
        """Plain-English summary of trigger + playback, shown under the settings.

        The two settings interact, so rather than making users work out the
        matrix, the interface states the resulting behaviour outright.
        """
        mode = self.effective_trigger()
        gap = f" with {self.playback.gap_ms} ms between repeats" if self.playback.gap_ms else ""

        if mode is TriggerMode.WHILE_HELD:
            return f"Holding {trigger_label} repeats the macro until it is released{gap}."
        if mode is TriggerMode.TOGGLE:
            return (
                f"Pressing {trigger_label} starts the macro; pressing it again stops it{gap}."
            )

        start = {
            TriggerMode.ON_PRESS: f"Pressing {trigger_label}",
            TriggerMode.ON_RELEASE: f"Releasing {trigger_label}",
            TriggerMode.ONE_SHOT: f"Pressing {trigger_label}",
        }[mode]
        ignored = (
            " Further presses are ignored until it finishes."
            if mode is TriggerMode.ONE_SHOT
            else ""
        )

        if self.playback.mode is PlaybackMode.ONCE:
            return f"{start} runs the macro once.{ignored}"
        if self.playback.mode is PlaybackMode.REPEAT_COUNT:
            return f"{start} runs the macro {self.playback.repeat_count} times{gap}.{ignored}"
        return (
            f"{start} repeats the macro until you stop it from the application{gap}.{ignored}"
        )

    def total_duration_ms(self) -> int:
        """Estimated duration of a single pass, for display in the macro list.

        Counts explicit waits and hold durations.  Actual input delivery is
        effectively instantaneous by comparison, so this is a close estimate
        rather than a guess.
        """
        total = 0
        for action in self.actions:
            if not action.enabled:
                continue
            total += _as_int(action.params.get("duration_ms"), 0)
            total += _as_int(action.params.get("hold_ms"), 0)
        return total

    def duplicate(self, name: str | None = None) -> "Macro":
        """A full copy with a new identity."""
        return Macro(
            name=name or f"{self.name} (Copy)",
            description=self.description,
            actions=[a.duplicate() for a in self.actions],
            playback=PlaybackConfig(
                mode=self.playback.mode,
                repeat_count=self.playback.repeat_count,
                gap_ms=self.playback.gap_ms,
            ),
            trigger=TriggerConfig(mode=self.trigger.mode),
        )

    # --- Serialisation --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the on-disk preset format."""
        return {
            "version": FILE_VERSION,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "playback": self.playback.to_dict(),
            "trigger": self.trigger.to_dict(),
            "actions": [action.to_dict() for action in self.actions],
        }

    @classmethod
    def from_dict(cls, data: Any, *, new_id: bool = False) -> "Macro":
        """Rebuild from the on-disk preset format.

        Malformed individual actions are skipped rather than failing the whole
        load, so one bad step in a hand-edited preset does not cost the user
        the rest of the macro.  Use :func:`clickyclicker.macros.validate.inspect`
        first when you need to report those problems.

        :param new_id: assign a fresh id, used when importing so an imported
            preset cannot collide with an existing one.
        :raises ValueError: if *data* is not a macro object at all.
        """
        if not isinstance(data, dict):
            raise ValueError("macro data must be a JSON object")

        actions: list[MacroAction] = []
        raw_actions = data.get("actions")
        if isinstance(raw_actions, list):
            for entry in raw_actions:
                if not isinstance(entry, dict):
                    continue
                try:
                    actions.append(MacroAction.from_dict(entry))
                except ValueError:
                    continue

        name = data.get("name")
        macro = cls(
            name=str(name) if isinstance(name, str) and name.strip() else "Untitled Macro",
            description=str(data.get("description") or ""),
            actions=actions,
            playback=PlaybackConfig.from_dict(data.get("playback")),
            trigger=TriggerConfig.from_dict(data.get("trigger")),
        )
        stored_id = data.get("id")
        if not new_id and isinstance(stored_id, str) and stored_id.strip():
            macro.id = stored_id
        return macro
