"""Human-facing catalogue of keyboard keys and mouse buttons.

The kernel's :mod:`~clickyclicker.models.keycodes` table is exhaustive but not
presentable: it contains reserved slots, vendor-specific scancodes and joystick
axes that have no place in a key picker.  This module curates that table into
labelled, grouped entries suitable for the UI, while keeping the kernel symbol
(``KEY_E``, ``BTN_SIDE``) as the canonical identifier used everywhere else in
the application -- in the data model, on the wire and in saved presets.

Storing the kernel symbol rather than the numeric code means saved macros stay
readable and stay valid across kernel versions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .keycodes import CODES, NAMES

__all__ = [
    "KeyKind",
    "KeyInfo",
    "CATEGORY_ORDER",
    "lookup",
    "label_for",
    "code_for",
    "name_for_code",
    "is_mouse_button",
    "is_modifier",
    "keyboard_keys",
    "mouse_buttons",
    "all_inputs",
    "by_category",
    "MODIFIERS",
]


class KeyKind(Enum):
    """Whether an input lives on the keyboard or the mouse."""

    KEYBOARD = "keyboard"
    MOUSE = "mouse"


@dataclass(frozen=True, slots=True)
class KeyInfo:
    """A single selectable input.

    :param name: Kernel symbol, e.g. ``KEY_E``.  The canonical identifier.
    :param label: Human-readable name shown in the interface, e.g. ``E``.
    :param category: Grouping used by the key picker's section list.
    :param kind: Keyboard or mouse.
    """

    name: str
    label: str
    category: str
    kind: KeyKind

    @property
    def code(self) -> int:
        """The numeric Linux event code."""
        return CODES[self.name]


# --- Curated tables -------------------------------------------------------
#
# Each entry is (kernel symbol, label).  Anything not listed here is still
# usable -- `lookup()` falls back to a derived label -- but only these appear
# in the picker, which keeps it navigable.

_LETTERS = [(f"KEY_{c}", c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]

_DIGITS = [(f"KEY_{d}", d) for d in "1234567890"]

_FUNCTION = [(f"KEY_F{i}", f"F{i}") for i in range(1, 25)]

_NAVIGATION = [
    ("KEY_UP", "Up"),
    ("KEY_DOWN", "Down"),
    ("KEY_LEFT", "Left"),
    ("KEY_RIGHT", "Right"),
    ("KEY_HOME", "Home"),
    ("KEY_END", "End"),
    ("KEY_PAGEUP", "Page Up"),
    ("KEY_PAGEDOWN", "Page Down"),
    ("KEY_INSERT", "Insert"),
    ("KEY_DELETE", "Delete"),
]

_EDITING = [
    ("KEY_ESC", "Escape"),
    ("KEY_TAB", "Tab"),
    ("KEY_ENTER", "Enter"),
    ("KEY_SPACE", "Space"),
    ("KEY_BACKSPACE", "Backspace"),
    ("KEY_CAPSLOCK", "Caps Lock"),
    ("KEY_NUMLOCK", "Num Lock"),
    ("KEY_SCROLLLOCK", "Scroll Lock"),
    ("KEY_SYSRQ", "Print Screen"),
    ("KEY_PAUSE", "Pause"),
    ("KEY_MENU", "Menu"),
    ("KEY_COMPOSE", "Compose"),
]

_MODIFIERS = [
    ("KEY_LEFTCTRL", "Left Ctrl"),
    ("KEY_RIGHTCTRL", "Right Ctrl"),
    ("KEY_LEFTSHIFT", "Left Shift"),
    ("KEY_RIGHTSHIFT", "Right Shift"),
    ("KEY_LEFTALT", "Left Alt"),
    ("KEY_RIGHTALT", "Right Alt (AltGr)"),
    ("KEY_LEFTMETA", "Left Super"),
    ("KEY_RIGHTMETA", "Right Super"),
]

_PUNCTUATION = [
    ("KEY_MINUS", "- Minus"),
    ("KEY_EQUAL", "= Equals"),
    ("KEY_LEFTBRACE", "[ Left Bracket"),
    ("KEY_RIGHTBRACE", "] Right Bracket"),
    ("KEY_BACKSLASH", "\\ Backslash"),
    ("KEY_SEMICOLON", "; Semicolon"),
    ("KEY_APOSTROPHE", "' Apostrophe"),
    ("KEY_GRAVE", "` Grave"),
    ("KEY_COMMA", ", Comma"),
    ("KEY_DOT", ". Period"),
    ("KEY_SLASH", "/ Slash"),
    ("KEY_102ND", "Extra Key (ISO layouts)"),
]

_NUMPAD = [
    ("KEY_KP0", "Numpad 0"),
    ("KEY_KP1", "Numpad 1"),
    ("KEY_KP2", "Numpad 2"),
    ("KEY_KP3", "Numpad 3"),
    ("KEY_KP4", "Numpad 4"),
    ("KEY_KP5", "Numpad 5"),
    ("KEY_KP6", "Numpad 6"),
    ("KEY_KP7", "Numpad 7"),
    ("KEY_KP8", "Numpad 8"),
    ("KEY_KP9", "Numpad 9"),
    ("KEY_KPDOT", "Numpad ."),
    ("KEY_KPPLUS", "Numpad +"),
    ("KEY_KPMINUS", "Numpad -"),
    ("KEY_KPASTERISK", "Numpad *"),
    ("KEY_KPSLASH", "Numpad /"),
    ("KEY_KPENTER", "Numpad Enter"),
    ("KEY_KPEQUAL", "Numpad ="),
]

_MEDIA = [
    ("KEY_MUTE", "Mute"),
    ("KEY_VOLUMEDOWN", "Volume Down"),
    ("KEY_VOLUMEUP", "Volume Up"),
    ("KEY_PLAYPAUSE", "Play/Pause"),
    ("KEY_STOPCD", "Stop"),
    ("KEY_PREVIOUSSONG", "Previous Track"),
    ("KEY_NEXTSONG", "Next Track"),
    ("KEY_BRIGHTNESSDOWN", "Brightness Down"),
    ("KEY_BRIGHTNESSUP", "Brightness Up"),
    ("KEY_SEARCH", "Search"),
    ("KEY_HOMEPAGE", "Home Page"),
    ("KEY_CALC", "Calculator"),
]

# Mouse buttons.  BTN_SIDE/BTN_EXTRA are what most mice report for the two
# thumb buttons; BTN_FORWARD/BTN_BACK/BTN_TASK show up on mice with more.
# Gaming mice with many buttons expose the surplus as BTN_TRIGGER_HAPPY*.
_MOUSE = [
    ("BTN_LEFT", "Left Button"),
    ("BTN_RIGHT", "Right Button"),
    ("BTN_MIDDLE", "Middle Button"),
    ("BTN_SIDE", "Button 4 (Side)"),
    ("BTN_EXTRA", "Button 5 (Extra)"),
    ("BTN_FORWARD", "Button 6 (Forward)"),
    ("BTN_BACK", "Button 7 (Back)"),
    ("BTN_TASK", "Button 8 (Task)"),
]
_MOUSE += [(f"BTN_TRIGGER_HAPPY{i}", f"Button {8 + i} (Extended)") for i in range(1, 21)]


CATEGORY_ORDER: tuple[str, ...] = (
    "Mouse",
    "Letters",
    "Numbers",
    "Function Keys",
    "Modifiers",
    "Editing",
    "Navigation",
    "Punctuation",
    "Numeric Keypad",
    "Media",
    "Other",
)

_TABLE: tuple[tuple[str, str, str, KeyKind], ...] = tuple(
    (name, label, category, kind)
    for category, kind, entries in (
        ("Mouse", KeyKind.MOUSE, _MOUSE),
        ("Letters", KeyKind.KEYBOARD, _LETTERS),
        ("Numbers", KeyKind.KEYBOARD, _DIGITS),
        ("Function Keys", KeyKind.KEYBOARD, _FUNCTION),
        ("Modifiers", KeyKind.KEYBOARD, _MODIFIERS),
        ("Editing", KeyKind.KEYBOARD, _EDITING),
        ("Navigation", KeyKind.KEYBOARD, _NAVIGATION),
        ("Punctuation", KeyKind.KEYBOARD, _PUNCTUATION),
        ("Numeric Keypad", KeyKind.KEYBOARD, _NUMPAD),
        ("Media", KeyKind.KEYBOARD, _MEDIA),
    )
    for name, label in entries
    # Guard against kernel headers older than an entry we list.
    if name in CODES
)

_BY_NAME: dict[str, KeyInfo] = {
    name: KeyInfo(name=name, label=label, category=category, kind=kind)
    for name, label, category, kind in _TABLE
}

#: Modifier keys, used to render combos in a conventional order.
MODIFIERS: tuple[str, ...] = tuple(name for name, _ in _MODIFIERS if name in CODES)

_MODIFIER_SET = frozenset(MODIFIERS)


def _derive_label(name: str) -> str:
    """Best-effort label for a kernel symbol that is not in the curated table."""
    stem = name.split("_", 1)[1] if "_" in name else name
    return stem.replace("_", " ").title()


def lookup(name: str) -> KeyInfo:
    """Return the :class:`KeyInfo` for a kernel symbol.

    Unknown-but-valid symbols get a derived label rather than raising, so a
    preset authored on a newer kernel still loads and displays sensibly.

    :raises KeyError: if *name* is not a known Linux input code at all.
    """
    info = _BY_NAME.get(name)
    if info is not None:
        return info
    if name not in CODES:
        raise KeyError(name)
    kind = KeyKind.MOUSE if name.startswith("BTN_") else KeyKind.KEYBOARD
    return KeyInfo(name=name, label=_derive_label(name), category="Other", kind=kind)


def label_for(name: str) -> str:
    """Human-readable label for a kernel symbol, or the symbol itself if unknown."""
    try:
        return lookup(name).label
    except KeyError:
        return name


def code_for(name: str) -> int:
    """Numeric event code for a kernel symbol.

    :raises KeyError: if the symbol is unknown.
    """
    return CODES[name]


def name_for_code(code: int) -> str | None:
    """Kernel symbol for a numeric event code, or ``None`` if unmapped."""
    return NAMES.get(code)


def is_mouse_button(name: str) -> bool:
    """Whether *name* refers to a mouse button rather than a keyboard key."""
    return name.startswith("BTN_")


def is_modifier(name: str) -> bool:
    """Whether *name* is a modifier key (Ctrl/Shift/Alt/Super)."""
    return name in _MODIFIER_SET


def keyboard_keys() -> list[KeyInfo]:
    """Every curated keyboard key, in catalogue order."""
    return [info for info in _BY_NAME.values() if info.kind is KeyKind.KEYBOARD]


def mouse_buttons() -> list[KeyInfo]:
    """Every curated mouse button, in catalogue order."""
    return [info for info in _BY_NAME.values() if info.kind is KeyKind.MOUSE]


def all_inputs() -> list[KeyInfo]:
    """Every curated input, in catalogue order."""
    return list(_BY_NAME.values())


def by_category() -> dict[str, list[KeyInfo]]:
    """Curated inputs grouped by category, in :data:`CATEGORY_ORDER` order."""
    grouped: dict[str, list[KeyInfo]] = {name: [] for name in CATEGORY_ORDER}
    for info in _BY_NAME.values():
        grouped.setdefault(info.category, []).append(info)
    return {name: entries for name, entries in grouped.items() if entries}


def format_combo(names: list[str]) -> str:
    """Render a key combination the way a user would write it: ``Ctrl + Shift + A``."""
    if not names:
        return "Nothing"
    modifiers = [n for n in names if is_modifier(n)]
    regular = [n for n in names if not is_modifier(n)]
    ordered = sorted(modifiers, key=MODIFIERS.index) + regular
    return " + ".join(label_for(n) for n in ordered)
