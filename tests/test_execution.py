"""Tests for macro playback, validation and recording.

The safety properties -- nothing left held, always stoppable -- get the most
attention here, because they are what stands between a bug and a desktop the
user cannot regain control of.
"""

from __future__ import annotations

import time

from clickyclicker.input.backend import InputEvent, KeyState
from clickyclicker.macros import MacroExecutor, MacroRecorder, Severity, inspect
from clickyclicker.models import Macro, MacroAction, PlaybackMode, TriggerMode
from clickyclicker.models.action import ActionType


def macro_with(*actions: MacroAction, **playback) -> Macro:
    macro = Macro(name="Test")
    macro.actions = list(actions)
    for key, value in playback.items():
        setattr(macro.playback, key, value)
    return macro


class TestPlayback:
    def test_runs_actions_in_order(self, sink):
        macro = macro_with(
            MacroAction.create(ActionType.KEY_PRESS, key="KEY_E"),
            MacroAction.create(ActionType.WAIT, duration_ms=1),
            MacroAction.create(ActionType.KEY_RELEASE, key="KEY_E"),
        )
        MacroExecutor(sink).start(macro).join(5)
        assert sink.events == [("press", "KEY_E"), ("release", "KEY_E")]

    def test_repeat_count_is_honoured(self, sink):
        macro = macro_with(
            MacroAction.create(ActionType.KEY_TAP, key="KEY_A", hold_ms=0),
            mode=PlaybackMode.REPEAT_COUNT,
            repeat_count=4,
            gap_ms=0,
        )
        handle = MacroExecutor(sink).start(macro)
        handle.join(5)
        assert handle.completed_iterations == 4
        assert sink.events.count(("press", "KEY_A")) == 4

    def test_combo_releases_in_reverse_order(self, sink):
        macro = macro_with(
            MacroAction.create(
                ActionType.KEY_COMBO, keys=["KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_A"], hold_ms=0
            )
        )
        MacroExecutor(sink).start(macro).join(5)
        assert sink.events == [
            ("press", "KEY_LEFTCTRL"),
            ("press", "KEY_LEFTSHIFT"),
            ("press", "KEY_A"),
            ("release", "KEY_A"),
            ("release", "KEY_LEFTSHIFT"),
            ("release", "KEY_LEFTCTRL"),
        ]

    def test_click_at_position_moves_before_clicking(self, sink):
        macro = macro_with(
            MacroAction.create(
                ActionType.MOUSE_CLICK_AT, button="BTN_LEFT", x=500, y=300, hold_ms=0
            )
        )
        MacroExecutor(sink).start(macro).join(5)
        assert sink.events[0] == ("move_absolute", 500, 300)
        assert ("press", "BTN_LEFT") in sink.events

    def test_disabled_actions_are_skipped(self, sink):
        skipped = MacroAction.create(ActionType.KEY_TAP, key="KEY_B", hold_ms=0)
        skipped.enabled = False
        macro = macro_with(
            MacroAction.create(ActionType.KEY_TAP, key="KEY_A", hold_ms=0), skipped
        )
        MacroExecutor(sink).start(macro).join(5)
        assert ("press", "KEY_B") not in sink.events

    def test_macro_with_no_enabled_actions_does_not_start(self, sink):
        action = MacroAction.create(ActionType.KEY_TAP, key="KEY_A")
        action.enabled = False
        assert MacroExecutor(sink).start(macro_with(action)) is None

    def test_test_run_overrides_playback_count(self, sink):
        macro = macro_with(
            MacroAction.create(ActionType.KEY_TAP, key="KEY_A", hold_ms=0),
            mode=PlaybackMode.REPEAT_FOREVER,
        )
        handle = MacroExecutor(sink).start(macro, iterations=1)
        handle.join(5)
        assert handle.completed_iterations == 1


class TestSafety:
    def test_stopping_releases_everything_held(self, sink):
        macro = macro_with(
            MacroAction.create(ActionType.KEY_PRESS, key="KEY_W"),
            MacroAction.create(ActionType.WAIT, duration_ms=5000),
            MacroAction.create(ActionType.KEY_RELEASE, key="KEY_W"),
        )
        executor = MacroExecutor(sink)
        executor.start(macro)
        time.sleep(0.15)
        assert sink.held == {"KEY_W"}

        executor.stop_all()
        assert sink.held == set(), "a stopped macro must not leave a key down"

    def test_stop_is_prompt_even_during_a_long_wait(self, sink):
        macro = macro_with(
            MacroAction.create(ActionType.WAIT, duration_ms=30_000),
            mode=PlaybackMode.REPEAT_FOREVER,
        )
        executor = MacroExecutor(sink)
        executor.start(macro)
        time.sleep(0.1)

        started = time.monotonic()
        executor.stop_all()
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"stop took {elapsed:.2f}s; it must not wait out the delay"
        assert executor.active_count == 0

    def test_repeat_forever_keeps_going_until_stopped(self, sink):
        macro = macro_with(
            MacroAction.create(ActionType.KEY_TAP, key="KEY_A", hold_ms=0),
            mode=PlaybackMode.REPEAT_FOREVER,
            gap_ms=1,
        )
        executor = MacroExecutor(sink)
        executor.start(macro)
        time.sleep(0.2)
        assert executor.active_count == 1
        executor.stop_all()
        assert executor.active_count == 0

    def test_same_key_does_not_start_two_runs(self, sink):
        macro = macro_with(
            MacroAction.create(ActionType.WAIT, duration_ms=2000),
            mode=PlaybackMode.REPEAT_FOREVER,
        )
        executor = MacroExecutor(sink)
        assert executor.start(macro) is not None
        assert executor.start(macro) is None, "a second run under the same key must be refused"
        executor.stop_all()

    def test_toggle_starts_then_stops(self, sink):
        macro = macro_with(
            MacroAction.create(ActionType.WAIT, duration_ms=2000),
            mode=PlaybackMode.REPEAT_FOREVER,
        )
        executor = MacroExecutor(sink)
        assert executor.toggle(macro) is True
        assert executor.is_running(macro.id)
        assert executor.toggle(macro) is False
        executor.stop_all()

    def test_unknown_key_does_not_abort_the_macro(self, sink):
        macro = macro_with(
            MacroAction.create(ActionType.KEY_TAP, key="", hold_ms=0),
            MacroAction.create(ActionType.KEY_TAP, key="KEY_A", hold_ms=0),
        )
        handle = MacroExecutor(sink).start(macro)
        handle.join(5)
        assert handle.error is None
        assert ("press", "KEY_A") in sink.events


class TestValidation:
    def test_unbalanced_press_is_warned_about(self):
        macro = macro_with(MacroAction.create(ActionType.KEY_PRESS, key="KEY_W"))
        messages = [issue.message for issue in inspect(macro)]
        assert any("never released" in message for message in messages)

    def test_balanced_press_is_not_warned_about(self):
        macro = macro_with(
            MacroAction.create(ActionType.KEY_PRESS, key="KEY_W"),
            MacroAction.create(ActionType.KEY_RELEASE, key="KEY_W"),
        )
        assert not any("never released" in i.message for i in inspect(macro))

    def test_unknown_key_is_an_error(self):
        macro = macro_with(MacroAction.create(ActionType.KEY_TAP, key="KEY_FAKE"))
        errors = [i for i in inspect(macro) if i.severity is Severity.ERROR]
        assert errors and errors[0].action_index == 0

    def test_busy_loop_is_warned_about(self):
        macro = macro_with(
            MacroAction.create(ActionType.KEY_TAP, key="KEY_A", hold_ms=0),
            mode=PlaybackMode.REPEAT_FOREVER,
            gap_ms=0,
        )
        assert any("flood" in i.message for i in inspect(macro))

    def test_delays_prevent_the_busy_loop_warning(self):
        macro = macro_with(
            MacroAction.create(ActionType.KEY_TAP, key="KEY_A", hold_ms=10),
            mode=PlaybackMode.REPEAT_FOREVER,
            gap_ms=0,
        )
        assert not any("flood" in i.message for i in inspect(macro))

    def test_errors_sort_before_warnings(self):
        macro = macro_with(
            MacroAction.create(ActionType.KEY_PRESS, key="KEY_W"),
            MacroAction.create(ActionType.KEY_TAP, key="KEY_FAKE"),
        )
        assert inspect(macro)[0].severity is Severity.ERROR

    def test_empty_macro_is_flagged(self):
        assert inspect(Macro())


class TestRecorder:
    def event(self, code, pressed, at):
        return InputEvent(
            code=code,
            state=KeyState.PRESSED if pressed else KeyState.RELEASED,
            timestamp=at,
        )

    def test_captures_presses_releases_and_timing(self):
        recorder = MacroRecorder(minimum_delay_ms=1)
        recorder.start()
        recorder.feed(self.event("KEY_E", True, 0.0))
        recorder.feed(self.event("KEY_E", False, 0.037))
        recorder.feed(self.event("BTN_LEFT", True, 0.119))
        recorder.feed(self.event("BTN_LEFT", False, 0.140))
        recorder.stop()

        assert [a.summary() for a in recorder.actions()] == [
            "Press and hold E",
            "Wait 37 ms",
            "Release E",
            "Wait 82 ms",
            "Hold Left Button",
            "Wait 21 ms",
            "Release Left Button",
        ]

    def test_auto_repeat_is_ignored(self):
        recorder = MacroRecorder()
        recorder.start()
        recorder.feed(InputEvent(code="KEY_A", state=KeyState.HELD, timestamp=0.0))
        assert recorder.count == 0

    def test_ignored_codes_are_left_out(self):
        recorder = MacroRecorder(ignored_codes={"KEY_ESC"})
        recorder.start()
        recorder.feed(self.event("KEY_ESC", True, 0.0))
        assert recorder.count == 0

    def test_trailing_wait_is_trimmed(self):
        recorder = MacroRecorder(minimum_delay_ms=1)
        recorder.start()
        recorder.feed(self.event("KEY_A", True, 0.0))
        recorder.feed(self.event("KEY_A", False, 0.05))
        recorder.stop()
        assert recorder.actions()[-1].type is ActionType.KEY_RELEASE

    def test_timing_can_be_switched_off(self):
        recorder = MacroRecorder(capture_delays=False)
        recorder.start()
        recorder.feed(self.event("KEY_A", True, 0.0))
        recorder.feed(self.event("KEY_A", False, 0.5))
        assert not any(a.type is ActionType.WAIT for a in recorder.actions())

    def test_discard_clears_everything(self):
        recorder = MacroRecorder()
        recorder.start()
        recorder.feed(self.event("KEY_A", True, 0.0))
        recorder.discard()
        assert recorder.count == 0
        assert not recorder.is_recording

    def test_not_recording_means_nothing_is_captured(self):
        recorder = MacroRecorder()
        recorder.feed(self.event("KEY_A", True, 0.0))
        assert recorder.count == 0


class TestTriggerModes:
    """The trigger/playback interaction, which the engine relies on."""

    def test_while_held_is_unbounded(self):
        macro = Macro()
        macro.playback.mode = PlaybackMode.WHILE_HELD
        assert macro.playback.iterations() is None
        assert macro.effective_trigger() is TriggerMode.WHILE_HELD

    def test_one_shot_leaves_playback_alone(self):
        macro = Macro()
        macro.trigger.mode = TriggerMode.ONE_SHOT
        macro.playback.mode = PlaybackMode.REPEAT_COUNT
        macro.playback.repeat_count = 3
        assert macro.effective_trigger() is TriggerMode.ONE_SHOT
        assert macro.playback.iterations() == 3
