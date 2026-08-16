"""Macro validation.

Reports problems the user should know about *before* a macro runs, so they show
up as inline banners in the editor rather than as surprising behaviour with the
pointer moving on its own.

Severity is meaningful: an :data:`Severity.ERROR` means the macro cannot play
correctly as written, while an :data:`Severity.WARNING` means it will do exactly
what it says and the user may not want that.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..models import Macro
from ..models.action import ActionType
from ..models.keys import CODES, label_for
from ..models.macro import PlaybackMode

__all__ = ["Severity", "Issue", "inspect"]


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Issue:
    """One problem found in a macro."""

    severity: Severity
    message: str
    action_index: int | None = None
    """Which action is at fault, or ``None`` for a whole-macro problem."""


_PRESS_TYPES = {ActionType.KEY_PRESS: "key", ActionType.BUTTON_PRESS: "button"}
_RELEASE_TYPES = {ActionType.KEY_RELEASE: "key", ActionType.BUTTON_RELEASE: "button"}


def inspect(macro: Macro) -> list[Issue]:
    """Every problem found in *macro*, most severe first."""
    issues: list[Issue] = []
    issues.extend(_check_empty(macro))
    issues.extend(_check_codes(macro))
    issues.extend(_check_balance(macro))
    issues.extend(_check_runaway(macro))
    issues.extend(_check_busy_loop(macro))
    issues.sort(key=lambda issue: 0 if issue.severity is Severity.ERROR else 1)
    return issues


def _check_empty(macro: Macro) -> list[Issue]:
    if not macro.actions:
        return [Issue(Severity.WARNING, "This macro has no actions yet.")]
    if not any(action.enabled for action in macro.actions):
        return [Issue(Severity.WARNING, "Every action in this macro is disabled.")]
    return []


def _check_codes(macro: Macro) -> list[Issue]:
    """Catch keys that this kernel does not know, e.g. from an imported preset."""
    issues: list[Issue] = []
    for index, action in enumerate(macro.actions):
        names: list[str] = []
        for key in ("key", "button"):
            value = action.params.get(key)
            if isinstance(value, str) and value:
                names.append(value)
        combo = action.params.get("keys")
        if isinstance(combo, list):
            names.extend(str(k) for k in combo)

        for name in names:
            if name not in CODES:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        f"“{name}” is not an input this system recognises.",
                        action_index=index,
                    )
                )
        if action.type is ActionType.KEY_COMBO and not combo:
            issues.append(
                Issue(Severity.ERROR, "This combination has no keys.", action_index=index)
            )
    return issues


def _check_balance(macro: Macro) -> list[Issue]:
    """Find keys pressed but never released.

    The executor releases them anyway when the run ends, but a macro that
    repeats will press an already-held key on every pass, which is almost never
    what the author meant.
    """
    held: dict[str, int] = {}
    for index, action in enumerate(macro.actions):
        if not action.enabled:
            continue
        if action.type in _PRESS_TYPES:
            code = str(action.params.get(_PRESS_TYPES[action.type], ""))
            if code:
                held.setdefault(code, index)
        elif action.type in _RELEASE_TYPES:
            held.pop(str(action.params.get(_RELEASE_TYPES[action.type], "")), None)

    return [
        Issue(
            Severity.WARNING,
            f"{label_for(code)} is pressed but never released.",
            action_index=index,
        )
        for code, index in held.items()
    ]


def _check_runaway(macro: Macro) -> list[Issue]:
    if macro.is_unbounded():
        return [
            Issue(
                Severity.WARNING,
                "This macro repeats forever. Stop it from the Macros list or with "
                "the emergency stop shortcut.",
            )
        ]
    return []


def _check_busy_loop(macro: Macro) -> list[Issue]:
    """Warn about a repeating macro with no delay anywhere in it.

    This floods the compositor with events as fast as the thread can emit them,
    which in practice makes the desktop unusable until the macro is stopped.
    """
    if macro.playback.mode is PlaybackMode.ONCE or macro.playback.gap_ms:
        return []
    has_delay = any(
        action.enabled
        and (action.params.get("duration_ms") or action.params.get("hold_ms"))
        for action in macro.actions
    )
    if has_delay:
        return []
    return [
        Issue(
            Severity.WARNING,
            "This macro repeats with no delays at all, which will flood the "
            "desktop with events. Add a Wait action or a repeat gap.",
        )
    ]
