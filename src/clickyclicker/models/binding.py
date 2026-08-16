"""Input bindings: what a physical key or button does when you press it.

Bindings are deliberately separate from :class:`~clickyclicker.models.macro.Macro`.
A macro describes *what to do*; a binding describes *what makes it happen*.
Keeping them apart is what allows the same macro to be attached to several
inputs, and allows a macro to be edited without touching any binding.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from . import keys
from .macro import TriggerMode

__all__ = ["BindingKind", "Binding", "BindingSet"]


class BindingKind(Enum):
    """What kind of behaviour a binding installs."""

    RUN_MACRO = "run_macro"
    """Activating the input plays a macro."""

    REMAP = "remap"
    """The input is replaced by one or more other keys or buttons."""

    DISABLE = "disable"
    """The input is swallowed and does nothing."""


@dataclass(slots=True)
class Binding:
    """A single physical input and the behaviour attached to it."""

    input: str = "KEY_F6"
    """Kernel symbol of the triggering key or button, e.g. ``BTN_SIDE``."""

    kind: BindingKind = BindingKind.RUN_MACRO
    macro_id: str | None = None
    """Target macro, for :data:`BindingKind.RUN_MACRO`."""

    output: list[str] = field(default_factory=list)
    """Replacement keys, for :data:`BindingKind.REMAP`.

    More than one entry produces a combination: ``["KEY_LEFTCTRL", "KEY_C"]``
    makes the bound input behave as Ctrl+C.
    """

    trigger_override: TriggerMode | None = None
    """Overrides the macro's own trigger mode when set.

    Lets the same macro behave differently on different inputs -- held on one,
    toggled on another -- without duplicating the macro.
    """

    device_id: str | None = None
    """Restrict to one physical device, or ``None`` to match any device.

    Useful when two devices report the same code, e.g. a keyboard's F6 and a
    gaming mouse that also emits F6.
    """

    suppress_original: bool = True
    """Whether the original event is withheld from the rest of the session.

    Suppression requires the daemon to take an exclusive grab on the device and
    re-emit everything else through its virtual device; see
    :mod:`clickyclicker.input.evdev_backend`.  Remaps and disables always
    suppress -- there is no meaning to a remap that also delivers the original.
    """

    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    # --- Behaviour ------------------------------------------------------

    def effective_suppress(self) -> bool:
        """Whether this binding actually withholds the original event."""
        if self.kind in (BindingKind.REMAP, BindingKind.DISABLE):
            return True
        return self.suppress_original

    def input_label(self) -> str:
        """Human-readable name of the triggering input."""
        return keys.label_for(self.input)

    def describe(self, macro_name: str | None = None) -> str:
        """One-line description of the binding's effect, for the mappings list."""
        if self.kind is BindingKind.DISABLE:
            return "Disabled — does nothing"
        if self.kind is BindingKind.REMAP:
            if not self.output:
                return "No replacement chosen"
            return f"Acts as {keys.format_combo(self.output)}"
        if macro_name:
            return f"Runs “{macro_name}”"
        return "No macro chosen"

    def duplicate(self) -> Binding:
        """A copy with a new identity."""
        return Binding(
            input=self.input,
            kind=self.kind,
            macro_id=self.macro_id,
            output=list(self.output),
            trigger_override=self.trigger_override,
            device_id=self.device_id,
            suppress_original=self.suppress_original,
            enabled=self.enabled,
        )

    def is_valid(self, known_macro_ids: set[str]) -> bool:
        """Whether this binding can actually be installed by the daemon."""
        try:
            keys.code_for(self.input)
        except KeyError:
            return False
        if self.kind is BindingKind.RUN_MACRO:
            return self.macro_id in known_macro_ids
        if self.kind is BindingKind.REMAP:
            return bool(self.output)
        return True

    # --- Serialisation --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "input": self.input,
            "kind": self.kind.value,
            "enabled": self.enabled,
            "suppress_original": self.suppress_original,
        }
        if self.kind is BindingKind.RUN_MACRO and self.macro_id:
            data["macro_id"] = self.macro_id
        if self.kind is BindingKind.REMAP:
            data["output"] = list(self.output)
        if self.trigger_override is not None:
            data["trigger_override"] = self.trigger_override.value
        if self.device_id:
            data["device_id"] = self.device_id
        return data

    @classmethod
    def from_dict(cls, data: Any) -> Binding:
        """Rebuild from disk.

        :raises ValueError: if *data* is not a binding object.
        """
        if not isinstance(data, dict):
            raise ValueError("binding data must be a JSON object")

        try:
            kind = BindingKind(data.get("kind", BindingKind.RUN_MACRO.value))
        except ValueError:
            kind = BindingKind.RUN_MACRO

        trigger_override: TriggerMode | None = None
        raw_override = data.get("trigger_override")
        if isinstance(raw_override, str):
            try:
                trigger_override = TriggerMode(raw_override)
            except ValueError:
                trigger_override = None

        raw_output = data.get("output")
        output = [str(k) for k in raw_output] if isinstance(raw_output, list) else []

        binding = cls(
            input=str(data.get("input") or "KEY_F6"),
            kind=kind,
            macro_id=data.get("macro_id") or None,
            output=output,
            trigger_override=trigger_override,
            device_id=data.get("device_id") or None,
            suppress_original=bool(data.get("suppress_original", True)),
            enabled=bool(data.get("enabled", True)),
        )
        stored_id = data.get("id")
        if isinstance(stored_id, str) and stored_id.strip():
            binding.id = stored_id
        return binding


@dataclass(slots=True)
class BindingSet:
    """The full collection of bindings, as stored in ``bindings.json``."""

    bindings: list[Binding] = field(default_factory=list)

    def for_macro(self, macro_id: str) -> list[Binding]:
        """Every binding that runs the given macro."""
        return [
            b
            for b in self.bindings
            if b.kind is BindingKind.RUN_MACRO and b.macro_id == macro_id
        ]

    def conflicts(self) -> dict[str, list[Binding]]:
        """Enabled bindings that claim the same input on the same device.

        Two bindings on one input are ambiguous, so the mappings page surfaces
        them instead of silently letting the first one win.
        """
        seen: dict[tuple[str, str | None], list[Binding]] = {}
        for binding in self.bindings:
            if not binding.enabled:
                continue
            seen.setdefault((binding.input, binding.device_id), []).append(binding)
        return {
            keys.label_for(input_name): group
            for (input_name, _device), group in seen.items()
            if len(group) > 1
        }

    def to_dict(self) -> dict[str, Any]:
        return {"version": 1, "bindings": [b.to_dict() for b in self.bindings]}

    @classmethod
    def from_dict(cls, data: Any) -> BindingSet:
        bindings: list[Binding] = []
        if isinstance(data, dict):
            raw = data.get("bindings")
            if isinstance(raw, list):
                for entry in raw:
                    try:
                        bindings.append(Binding.from_dict(entry))
                    except ValueError:
                        continue
        return cls(bindings=bindings)
