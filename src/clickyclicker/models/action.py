"""Macro actions: the individual steps that make up a macro.

An action is a ``(type, params)`` pair rather than one dataclass per action
type.  Every action type is described once, declaratively, by an
:class:`ActionSpec` in :data:`ACTION_SPECS`; the macro editor builds its
parameter controls from that description, the executor dispatches on the type,
and serialisation is a plain dict either way.

Adding a new action type therefore means adding one :class:`ActionSpec` and one
branch in :mod:`clickyclicker.macros.executor` -- no UI changes required.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from . import keys

__all__ = [
    "ActionType",
    "ParamKind",
    "ParamSpec",
    "ActionSpec",
    "MacroAction",
    "ACTION_SPECS",
    "spec_for",
    "action_types_in_order",
]


class ActionType(Enum):
    """The kinds of step a macro can contain.

    Values are the strings written to disk, so they must stay stable.
    """

    KEY_PRESS = "key_press"
    KEY_RELEASE = "key_release"
    KEY_TAP = "key_tap"
    KEY_COMBO = "key_combo"
    BUTTON_PRESS = "button_press"
    BUTTON_RELEASE = "button_release"
    BUTTON_CLICK = "button_click"
    MOUSE_MOVE = "mouse_move"
    MOUSE_MOVE_RELATIVE = "mouse_move_relative"
    MOUSE_CLICK_AT = "mouse_click_at"
    SCROLL = "scroll"
    WAIT = "wait"


class ParamKind(Enum):
    """How a parameter should be edited and validated.

    The macro editor maps each kind onto a native control: :data:`KEY` and
    :data:`KEY_LIST` open the key chooser dialog, :data:`DURATION` and
    :data:`COORDINATE` use ``AdwSpinRow``, :data:`CHOICE` uses ``AdwComboRow``
    and :data:`BOOLEAN` uses ``AdwSwitchRow``.
    """

    KEY = "key"
    KEY_LIST = "key_list"
    DURATION = "duration"
    COORDINATE = "coordinate"
    INTEGER = "integer"
    CHOICE = "choice"
    BOOLEAN = "boolean"


#: Upper bound for any single delay, in milliseconds (one hour).  Generous, but
#: finite, so a typo cannot wedge a macro run for days.
MAX_DURATION_MS = 3_600_000

#: Upper bound for absolute pointer coordinates.  The virtual absolute pointer
#: is created with this logical range and the compositor scales it onto the
#: desktop; see :mod:`clickyclicker.input.uinput_sink`.
MAX_COORDINATE = 32_767


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """Description of one parameter of an action type."""

    key: str
    label: str
    kind: ParamKind
    default: Any
    minimum: int = 0
    maximum: int = 0
    step: int = 1
    unit: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    """For :data:`ParamKind.CHOICE`: ``(stored value, display label)`` pairs."""


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """Everything the UI and executor need to know about an action type."""

    type: ActionType
    label: str
    icon_name: str
    """A standard Adwaita icon name; no custom artwork is shipped."""
    category: str
    params: tuple[ParamSpec, ...]
    summarise: Callable[[dict[str, Any]], str]
    """Renders the one-line description shown in the macro editor row."""

    def defaults(self) -> dict[str, Any]:
        """A fresh parameter dict populated with this type's defaults."""
        return {
            p.key: list(p.default) if isinstance(p.default, list) else p.default
            for p in self.params
        }


# --- Summary formatters ---------------------------------------------------


def _fmt_duration(ms: int) -> str:
    """Render a millisecond duration compactly but without losing precision."""
    if ms < 1000:
        return f"{ms} ms"
    seconds = ms / 1000
    text = f"{seconds:.3f}".rstrip("0").rstrip(".")
    return f"{text} s"


def _key_label(params: dict[str, Any], param: str = "key") -> str:
    return keys.label_for(params.get(param) or "")


def _sum_key_press(p: dict[str, Any]) -> str:
    return f"Press and hold {_key_label(p)}"


def _sum_key_release(p: dict[str, Any]) -> str:
    return f"Release {_key_label(p)}"


def _sum_key_tap(p: dict[str, Any]) -> str:
    return f"Tap {_key_label(p)} for {_fmt_duration(p.get('hold_ms', 0))}"


def _sum_key_combo(p: dict[str, Any]) -> str:
    combo = keys.format_combo(list(p.get("keys") or []))
    return f"Press {combo} for {_fmt_duration(p.get('hold_ms', 0))}"


def _sum_button_press(p: dict[str, Any]) -> str:
    return f"Hold {_key_label(p, 'button')}"


def _sum_button_release(p: dict[str, Any]) -> str:
    return f"Release {_key_label(p, 'button')}"


def _sum_button_click(p: dict[str, Any]) -> str:
    return f"Click {_key_label(p, 'button')} for {_fmt_duration(p.get('hold_ms', 0))}"


def _sum_mouse_move(p: dict[str, Any]) -> str:
    return f"Move pointer to X {p.get('x', 0)}, Y {p.get('y', 0)}"


def _sum_mouse_move_relative(p: dict[str, Any]) -> str:
    dx, dy = p.get("dx", 0), p.get("dy", 0)
    return f"Move pointer by {dx:+d}, {dy:+d}"


def _sum_mouse_click_at(p: dict[str, Any]) -> str:
    button = _key_label(p, "button")
    return f"Click {button} at X {p.get('x', 0)}, Y {p.get('y', 0)}"


def _sum_scroll(p: dict[str, Any]) -> str:
    amount = p.get("amount", 0)
    if p.get("horizontal"):
        direction = "right" if amount >= 0 else "left"
    else:
        direction = "up" if amount >= 0 else "down"
    return f"Scroll {direction} {abs(amount)}"


def _sum_wait(p: dict[str, Any]) -> str:
    return f"Wait {_fmt_duration(p.get('duration_ms', 0))}"


# --- Shared parameter specs ----------------------------------------------

_HOLD_MS = ParamSpec(
    key="hold_ms",
    label="Hold Duration",
    kind=ParamKind.DURATION,
    default=20,
    minimum=0,
    maximum=MAX_DURATION_MS,
    unit="ms",
)

_BUTTON = ParamSpec(
    key="button",
    label="Mouse Button",
    kind=ParamKind.KEY,
    default="BTN_LEFT",
)

_KEY = ParamSpec(key="key", label="Key", kind=ParamKind.KEY, default="KEY_E")


def _coordinate(key: str, label: str) -> ParamSpec:
    return ParamSpec(
        key=key,
        label=label,
        kind=ParamKind.COORDINATE,
        default=0,
        minimum=0,
        maximum=MAX_COORDINATE,
        step=1,
        unit="px",
    )


ACTION_SPECS: dict[ActionType, ActionSpec] = {
    ActionType.KEY_TAP: ActionSpec(
        type=ActionType.KEY_TAP,
        label="Tap Key",
        icon_name="input-keyboard-symbolic",
        category="Keyboard",
        params=(_KEY, _HOLD_MS),
        summarise=_sum_key_tap,
    ),
    ActionType.KEY_PRESS: ActionSpec(
        type=ActionType.KEY_PRESS,
        label="Press Key",
        icon_name="input-keyboard-symbolic",
        category="Keyboard",
        params=(_KEY,),
        summarise=_sum_key_press,
    ),
    ActionType.KEY_RELEASE: ActionSpec(
        type=ActionType.KEY_RELEASE,
        label="Release Key",
        icon_name="input-keyboard-symbolic",
        category="Keyboard",
        params=(_KEY,),
        summarise=_sum_key_release,
    ),
    ActionType.KEY_COMBO: ActionSpec(
        type=ActionType.KEY_COMBO,
        label="Key Combination",
        icon_name="input-keyboard-symbolic",
        category="Keyboard",
        params=(
            ParamSpec(
                key="keys",
                label="Keys",
                kind=ParamKind.KEY_LIST,
                default=["KEY_LEFTCTRL", "KEY_C"],
            ),
            _HOLD_MS,
        ),
        summarise=_sum_key_combo,
    ),
    ActionType.BUTTON_CLICK: ActionSpec(
        type=ActionType.BUTTON_CLICK,
        label="Click Mouse Button",
        icon_name="input-mouse-symbolic",
        category="Mouse",
        params=(_BUTTON, _HOLD_MS),
        summarise=_sum_button_click,
    ),
    ActionType.BUTTON_PRESS: ActionSpec(
        type=ActionType.BUTTON_PRESS,
        label="Press Mouse Button",
        icon_name="input-mouse-symbolic",
        category="Mouse",
        params=(_BUTTON,),
        summarise=_sum_button_press,
    ),
    ActionType.BUTTON_RELEASE: ActionSpec(
        type=ActionType.BUTTON_RELEASE,
        label="Release Mouse Button",
        icon_name="input-mouse-symbolic",
        category="Mouse",
        params=(_BUTTON,),
        summarise=_sum_button_release,
    ),
    ActionType.MOUSE_CLICK_AT: ActionSpec(
        type=ActionType.MOUSE_CLICK_AT,
        label="Click at Position",
        icon_name="find-location-symbolic",
        category="Pointer",
        params=(_BUTTON, _coordinate("x", "X Position"), _coordinate("y", "Y Position"), _HOLD_MS),
        summarise=_sum_mouse_click_at,
    ),
    ActionType.MOUSE_MOVE: ActionSpec(
        type=ActionType.MOUSE_MOVE,
        label="Move Pointer To",
        icon_name="find-location-symbolic",
        category="Pointer",
        params=(_coordinate("x", "X Position"), _coordinate("y", "Y Position")),
        summarise=_sum_mouse_move,
    ),
    ActionType.MOUSE_MOVE_RELATIVE: ActionSpec(
        type=ActionType.MOUSE_MOVE_RELATIVE,
        label="Move Pointer By",
        icon_name="find-location-symbolic",
        category="Pointer",
        params=(
            ParamSpec(
                key="dx",
                label="Horizontal",
                kind=ParamKind.INTEGER,
                default=0,
                minimum=-MAX_COORDINATE,
                maximum=MAX_COORDINATE,
                unit="px",
            ),
            ParamSpec(
                key="dy",
                label="Vertical",
                kind=ParamKind.INTEGER,
                default=0,
                minimum=-MAX_COORDINATE,
                maximum=MAX_COORDINATE,
                unit="px",
            ),
        ),
        summarise=_sum_mouse_move_relative,
    ),
    ActionType.SCROLL: ActionSpec(
        type=ActionType.SCROLL,
        label="Scroll Wheel",
        icon_name="input-mouse-symbolic",
        category="Mouse",
        params=(
            ParamSpec(
                key="amount",
                label="Amount",
                kind=ParamKind.INTEGER,
                default=1,
                minimum=-100,
                maximum=100,
                unit="steps",
            ),
            ParamSpec(
                key="horizontal",
                label="Scroll Horizontally",
                kind=ParamKind.BOOLEAN,
                default=False,
            ),
        ),
        summarise=_sum_scroll,
    ),
    ActionType.WAIT: ActionSpec(
        type=ActionType.WAIT,
        label="Wait",
        icon_name="preferences-system-time-symbolic",
        category="Timing",
        params=(
            ParamSpec(
                key="duration_ms",
                label="Duration",
                kind=ParamKind.DURATION,
                default=50,
                minimum=0,
                maximum=MAX_DURATION_MS,
                unit="ms",
            ),
        ),
        summarise=_sum_wait,
    ),
}


def action_types_in_order() -> list[ActionType]:
    """Action types grouped by category, for the 'Add Action' menu."""
    order = ("Keyboard", "Mouse", "Pointer", "Timing")
    return sorted(
        ACTION_SPECS,
        key=lambda t: (order.index(ACTION_SPECS[t].category), list(ACTION_SPECS).index(t)),
    )


def spec_for(action_type: ActionType) -> ActionSpec:
    """The :class:`ActionSpec` describing *action_type*."""
    return ACTION_SPECS[action_type]


@dataclass(slots=True)
class MacroAction:
    """One step of a macro.

    The ``uid`` is transient -- it exists so the editor can track rows across
    reorders -- and is regenerated on load rather than persisted.
    """

    type: ActionType
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    uid: str = field(default_factory=lambda: uuid.uuid4().hex, compare=False)

    @classmethod
    def create(cls, action_type: ActionType, **overrides: Any) -> MacroAction:
        """Build an action of *action_type* with default parameters."""
        spec = spec_for(action_type)
        params = spec.defaults()
        params.update(overrides)
        return cls(type=action_type, params=params)

    @property
    def spec(self) -> ActionSpec:
        """The spec describing this action's type."""
        return spec_for(self.type)

    def summary(self) -> str:
        """One-line human description, e.g. ``Tap E for 1 ms``."""
        try:
            return self.spec.summarise(self.params)
        except Exception:  # noqa: BLE001 - a malformed preset must not break the list
            return self.spec.label

    def duplicate(self) -> MacroAction:
        """A copy with fresh identity, for the editor's Duplicate command."""
        return MacroAction(
            type=self.type,
            params=dict(self.params),
            enabled=self.enabled,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the on-disk representation."""
        data: dict[str, Any] = {"type": self.type.value, "params": dict(self.params)}
        if not self.enabled:
            data["enabled"] = False
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MacroAction:
        """Rebuild from the on-disk representation.

        Unknown parameters are dropped and missing ones filled with defaults, so
        a preset written by a different version still loads.

        :raises ValueError: if the action type is not recognised.
        """
        raw_type = data.get("type")
        try:
            action_type = ActionType(raw_type)
        except ValueError as exc:
            raise ValueError(f"unknown action type {raw_type!r}") from exc

        spec = spec_for(action_type)
        params = spec.defaults()
        supplied = data.get("params")
        if isinstance(supplied, dict):
            for param in spec.params:
                if param.key in supplied:
                    params[param.key] = supplied[param.key]
        return cls(type=action_type, params=params, enabled=bool(data.get("enabled", True)))
