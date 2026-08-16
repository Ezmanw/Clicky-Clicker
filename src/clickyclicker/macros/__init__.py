"""Macro playback, recording and validation.

Depends on :mod:`clickyclicker.models` and :mod:`clickyclicker.input` only.  No
GTK, so the daemon can run macros headless and the tests can run against a fake
sink.
"""

from .executor import MacroExecutor, RunHandle
from .recorder import MacroRecorder
from .validate import Issue, Severity, inspect

__all__ = ["Issue", "MacroExecutor", "MacroRecorder", "RunHandle", "Severity", "inspect"]
