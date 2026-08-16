"""Pure data model for Clicky Clicker.

Nothing in this package imports GTK, evdev, or touches the filesystem.  That is
deliberate: the same objects are used by the user interface, the daemon, the
persistence layer and the test suite, and keeping them dependency-free is what
lets the daemon run without a display and the tests run without hardware.
"""

from .action import ACTION_SPECS, ActionSpec, ActionType, MacroAction, ParamKind, ParamSpec, spec_for
from .binding import Binding, BindingKind, BindingSet
from .macro import (
    PLAYBACK_LABELS,
    TRIGGER_LABELS,
    Macro,
    PlaybackConfig,
    PlaybackMode,
    TriggerConfig,
    TriggerMode,
)
from .settings import Settings

__all__ = [
    "ACTION_SPECS",
    "ActionSpec",
    "ActionType",
    "Binding",
    "BindingKind",
    "BindingSet",
    "Macro",
    "MacroAction",
    "PLAYBACK_LABELS",
    "ParamKind",
    "ParamSpec",
    "PlaybackConfig",
    "PlaybackMode",
    "Settings",
    "TRIGGER_LABELS",
    "TriggerConfig",
    "TriggerMode",
    "spec_for",
]
