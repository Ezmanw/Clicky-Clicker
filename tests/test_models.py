"""Tests for the data model and its on-disk representation."""

from __future__ import annotations

import json

import pytest

from clickyclicker.models import (
    Binding,
    BindingKind,
    BindingSet,
    Macro,
    MacroAction,
    PlaybackMode,
    Settings,
    TriggerMode,
)
from clickyclicker.models.action import ActionType
from clickyclicker.models.keys import code_for, format_combo, label_for, name_for_code


def build_macro() -> Macro:
    macro = Macro(name="Rapid Action")
    macro.actions = [
        MacroAction.create(ActionType.KEY_PRESS, key="KEY_E"),
        MacroAction.create(ActionType.WAIT, duration_ms=1),
        MacroAction.create(ActionType.KEY_RELEASE, key="KEY_E"),
        MacroAction.create(ActionType.BUTTON_CLICK, button="BTN_LEFT", hold_ms=20),
        MacroAction.create(ActionType.MOUSE_MOVE, x=500, y=300),
    ]
    return macro


class TestKeyTable:
    def test_round_trips_between_name_and_code(self):
        assert name_for_code(code_for("KEY_E")) == "KEY_E"
        assert name_for_code(code_for("BTN_SIDE")) == "BTN_SIDE"

    def test_labels_are_human_readable(self):
        assert label_for("KEY_E") == "E"
        assert label_for("BTN_SIDE") == "Button 4 (Side)"
        assert label_for("KEY_CAPSLOCK") == "Caps Lock"

    def test_unknown_name_falls_back_to_itself(self):
        assert label_for("KEY_NOT_A_REAL_KEY") == "KEY_NOT_A_REAL_KEY"

    def test_combo_puts_modifiers_first(self):
        assert format_combo(["KEY_A", "KEY_LEFTCTRL"]) == "Left Ctrl + A"
        assert format_combo([]) == "Nothing"


class TestActionSummaries:
    @pytest.mark.parametrize(
        ("action", "expected"),
        [
            (MacroAction.create(ActionType.KEY_PRESS, key="KEY_E"), "Press and hold E"),
            (MacroAction.create(ActionType.WAIT, duration_ms=1), "Wait 1 ms"),
            (MacroAction.create(ActionType.WAIT, duration_ms=1500), "Wait 1.5 s"),
            (
                MacroAction.create(ActionType.MOUSE_MOVE, x=500, y=300),
                "Move pointer to X 500, Y 300",
            ),
            (MacroAction.create(ActionType.SCROLL, amount=-3), "Scroll down 3"),
        ],
    )
    def test_reads_as_a_sentence(self, action, expected):
        assert action.summary() == expected

    def test_summary_survives_malformed_parameters(self):
        action = MacroAction(type=ActionType.KEY_TAP, params={})
        assert action.summary()  # falls back to the type label rather than raising


class TestMacroSerialisation:
    def test_round_trip_preserves_everything(self):
        original = build_macro()
        original.playback.mode = PlaybackMode.WHILE_HELD
        original.playback.gap_ms = 5

        restored = Macro.from_dict(json.loads(json.dumps(original.to_dict())))

        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.playback.mode is PlaybackMode.WHILE_HELD
        assert restored.playback.gap_ms == 5
        assert [a.summary() for a in restored.actions] == [
            a.summary() for a in original.actions
        ]

    def test_import_can_assign_a_new_identity(self):
        original = build_macro()
        imported = Macro.from_dict(original.to_dict(), new_id=True)
        assert imported.id != original.id

    def test_unknown_action_type_is_skipped_not_fatal(self):
        data = build_macro().to_dict()
        data["actions"].insert(1, {"type": "teleport", "params": {}})
        restored = Macro.from_dict(data)
        assert len(restored.actions) == 5

    def test_missing_parameters_fall_back_to_defaults(self):
        restored = Macro.from_dict(
            {"name": "Partial", "actions": [{"type": "key_tap", "params": {}}]}
        )
        assert restored.actions[0].params["key"] == "KEY_E"

    def test_rejects_non_object(self):
        with pytest.raises(ValueError):
            Macro.from_dict([1, 2, 3])


class TestPlaybackAndTrigger:
    def test_iteration_counts(self):
        macro = Macro()
        macro.playback.mode = PlaybackMode.ONCE
        assert macro.playback.iterations() == 1
        macro.playback.mode = PlaybackMode.REPEAT_COUNT
        macro.playback.repeat_count = 20
        assert macro.playback.iterations() == 20
        macro.playback.mode = PlaybackMode.REPEAT_FOREVER
        assert macro.playback.iterations() is None

    def test_while_held_playback_pins_the_trigger(self):
        macro = Macro()
        macro.trigger.mode = TriggerMode.ON_PRESS
        macro.playback.mode = PlaybackMode.WHILE_HELD
        assert macro.effective_trigger() is TriggerMode.WHILE_HELD

    def test_toggle_playback_pins_the_trigger(self):
        macro = Macro()
        macro.playback.mode = PlaybackMode.TOGGLE
        assert macro.effective_trigger() is TriggerMode.TOGGLE

    def test_only_unstoppable_combination_is_flagged_unbounded(self):
        macro = Macro()
        macro.playback.mode = PlaybackMode.REPEAT_FOREVER
        macro.trigger.mode = TriggerMode.ON_PRESS
        assert macro.is_unbounded()

        macro.playback.mode = PlaybackMode.WHILE_HELD
        assert not macro.is_unbounded()

    def test_behaviour_is_described_in_plain_english(self):
        macro = Macro()
        macro.playback.mode = PlaybackMode.WHILE_HELD
        macro.playback.gap_ms = 5
        described = macro.describe_behaviour("Button 4")
        assert "Holding Button 4" in described
        assert "until it is released" in described
        assert "5 ms" in described

    def test_repeat_count_is_clamped_on_load(self):
        macro = Macro.from_dict(
            {"name": "x", "playback": {"mode": "repeat_count", "repeat_count": -5}}
        )
        assert macro.playback.repeat_count == 1

    def test_duration_estimate_ignores_disabled_actions(self):
        macro = build_macro()
        assert macro.total_duration_ms() == 21
        macro.actions[1].enabled = False
        assert macro.total_duration_ms() == 20


class TestBindings:
    def test_remap_always_suppresses(self):
        binding = Binding(kind=BindingKind.REMAP, output=["KEY_ESC"], suppress_original=False)
        assert binding.effective_suppress()

    def test_macro_trigger_respects_the_switch(self):
        binding = Binding(kind=BindingKind.RUN_MACRO, macro_id="m", suppress_original=False)
        assert not binding.effective_suppress()

    def test_describes_itself(self):
        assert "Escape" in Binding(
            kind=BindingKind.REMAP, output=["KEY_ESC"]
        ).describe()
        assert Binding(kind=BindingKind.DISABLE).describe() == "Disabled — does nothing"

    def test_validity_requires_a_known_macro(self):
        binding = Binding(input="KEY_F6", kind=BindingKind.RUN_MACRO, macro_id="abc")
        assert binding.is_valid({"abc"})
        assert not binding.is_valid({"xyz"})

    def test_validity_rejects_unknown_input(self):
        assert not Binding(input="KEY_NONSENSE", kind=BindingKind.DISABLE).is_valid(set())

    def test_conflicts_are_detected(self):
        bindings = BindingSet([
            Binding(input="KEY_F6", kind=BindingKind.DISABLE),
            Binding(input="KEY_F6", kind=BindingKind.DISABLE),
            Binding(input="KEY_F7", kind=BindingKind.DISABLE),
        ])
        conflicts = bindings.conflicts()
        assert list(conflicts) == ["F6"]

    def test_disabled_bindings_do_not_conflict(self):
        bindings = BindingSet([
            Binding(input="KEY_F6", kind=BindingKind.DISABLE),
            Binding(input="KEY_F6", kind=BindingKind.DISABLE, enabled=False),
        ])
        assert bindings.conflicts() == {}


class TestSettings:
    def test_round_trip(self):
        settings = Settings()
        settings.emergency_stop = ["KEY_LEFTSHIFT", "KEY_F12"]
        settings.enabled = False
        restored = Settings.from_dict(json.loads(json.dumps(settings.to_dict())))
        assert restored.emergency_stop == ["KEY_LEFTSHIFT", "KEY_F12"]
        assert restored.enabled is False

    def test_garbage_falls_back_to_defaults(self):
        restored = Settings.from_dict({"enabled": "yes please", "default_gap_ms": "soon"})
        assert restored.enabled is True
        assert restored.default_gap_ms == 10

    def test_empty_emergency_stop_keeps_the_default(self):
        assert Settings.from_dict({"emergency_stop": []}).emergency_stop
