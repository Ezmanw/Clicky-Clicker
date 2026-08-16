"""Tests for saving, loading, importing and exporting, and for the IPC framing."""

from __future__ import annotations

import json

import pytest

from clickyclicker.models import Binding, BindingKind, BindingSet, Macro, MacroAction
from clickyclicker.models.action import ActionType
from clickyclicker.persistence.store import read_json, write_json
from clickyclicker.services.ipc import Command, decode, encode, error, ok


def sample() -> Macro:
    macro = Macro(name="Sample")
    macro.actions = [MacroAction.create(ActionType.KEY_TAP, key="KEY_A", hold_ms=5)]
    return macro


class TestAtomicWrites:
    def test_write_then_read_round_trips(self, tmp_path):
        target = tmp_path / "data.json"
        write_json(target, {"hello": "world"})
        assert read_json(target) == {"hello": "world"}

    def test_leaves_no_temporary_files_behind(self, tmp_path):
        target = tmp_path / "data.json"
        write_json(target, {"a": 1})
        assert [p.name for p in tmp_path.iterdir()] == ["data.json"]

    def test_a_failed_write_leaves_the_original_intact(self, tmp_path):
        target = tmp_path / "data.json"
        write_json(target, {"good": True})

        class Unserialisable:
            pass

        with pytest.raises(TypeError):
            write_json(target, {"bad": Unserialisable()})

        assert read_json(target) == {"good": True}, "the previous file must survive"
        assert [p.name for p in tmp_path.iterdir()] == ["data.json"]

    def test_invalid_json_reports_the_line(self, tmp_path):
        target = tmp_path / "broken.json"
        target.write_text('{\n  "a": \n}')
        with pytest.raises(ValueError, match="line 3"):
            read_json(target)


class TestMacroStore:
    def test_save_and_load(self, store):
        macro = sample()
        store.save_macro(macro)
        loaded = store.load_macros()
        assert len(loaded) == 1
        assert loaded[0].id == macro.id

    def test_macros_load_sorted_by_name(self, store):
        for name in ("Zebra", "apple", "Mango"):
            macro = sample()
            macro.name = name
            store.save_macro(macro)
        assert [m.name for m in store.load_macros()] == ["apple", "Mango", "Zebra"]

    def test_one_broken_file_does_not_stop_the_rest(self, store):
        store.save_macro(sample())
        (store.macros_path / "broken.json").write_text("{not json")

        loaded = store.load_macros()
        assert len(loaded) == 1
        assert len(store.problems) == 1
        assert "broken.json" in store.problems[0].summary

    def test_delete_is_idempotent(self, store):
        macro = sample()
        store.save_macro(macro)
        store.delete_macro(macro.id)
        store.delete_macro(macro.id)
        assert store.load_macros() == []

    @pytest.mark.parametrize(
        "hostile_id",
        ["../../etc/passwd", "/etc/passwd", "..", "a/b/c", "~/secrets", ""],
    )
    def test_macro_id_cannot_escape_the_directory(self, store, hostile_id):
        """A hand-edited or imported id must not direct a write outside the folder.

        The invariant is about *where* the file lands, not what it is called:
        the resolved path must stay directly inside the macros directory.
        """
        path = store.macro_path(hostile_id)
        assert path.parent == store.macros_path
        assert path.resolve().parent == store.macros_path.resolve()
        assert "/" not in path.name

    def test_export_then_import_gives_a_new_identity(self, store, tmp_path):
        macro = sample()
        store.save_macro(macro)

        exported = tmp_path / "shared.json"
        store.export_macro(macro, exported)
        imported = store.import_macro(exported)

        assert imported.id != macro.id
        assert imported.name == macro.name
        assert len(imported.actions) == len(macro.actions)

    def test_importing_an_empty_preset_is_rejected(self, store, tmp_path):
        empty = tmp_path / "empty.json"
        empty.write_text(json.dumps({"name": "Nothing", "actions": []}))
        with pytest.raises(ValueError, match="no actions"):
            store.import_macro(empty)

    def test_importing_rubbish_is_rejected(self, store, tmp_path):
        rubbish = tmp_path / "rubbish.json"
        rubbish.write_text("this is not json at all")
        with pytest.raises(ValueError):
            store.import_macro(rubbish)


class TestBindingPersistence:
    def test_round_trip(self, store):
        bindings = BindingSet([
            Binding(input="BTN_SIDE", kind=BindingKind.RUN_MACRO, macro_id="abc"),
            Binding(input="KEY_CAPSLOCK", kind=BindingKind.REMAP, output=["KEY_ESC"]),
        ])
        store.save_bindings(bindings)

        loaded = store.load_bindings()
        assert len(loaded.bindings) == 2
        assert loaded.bindings[1].output == ["KEY_ESC"]

    def test_missing_file_gives_an_empty_set(self, store):
        assert store.load_bindings().bindings == []

    def test_corrupt_file_gives_an_empty_set_and_reports(self, store):
        store.bindings_path.parent.mkdir(parents=True, exist_ok=True)
        store.bindings_path.write_text("{{{")
        assert store.load_bindings().bindings == []
        assert store.problems

    def test_settings_round_trip(self, store):
        settings = store.load_settings()
        settings.default_gap_ms = 42
        store.save_settings(settings)
        assert store.load_settings().default_gap_ms == 42


class TestExamplePresets:
    def test_bundled_presets_all_parse(self):
        """Every shipped preset must load, or a new user's first run is broken."""
        from clickyclicker.persistence import paths

        directories = [d for d in paths.system_preset_dirs() if d.is_dir()]
        assert directories, "the bundled presets should be findable from a checkout"

        files = sorted(directories[0].glob("*.json"))
        assert len(files) >= 5

        for path in files:
            macro = Macro.from_dict(read_json(path))
            assert macro.name
            assert macro.actions, f"{path.name} has no actions"

    def test_seeding_only_happens_when_empty(self, store):
        assert store.seed_examples_if_empty()
        count = len(store.load_macros())
        assert count > 0
        assert store.seed_examples_if_empty() == []
        assert len(store.load_macros()) == count


class TestIpcFraming:
    def test_round_trip(self):
        payload = {"command": Command.STATUS.value, "extra": 1}
        assert decode(encode(payload).rstrip(b"\n")) == payload

    def test_encoding_is_newline_terminated(self):
        assert encode({"a": 1}).endswith(b"\n")

    def test_replies_carry_a_success_flag(self):
        assert ok(started=True) == {"ok": True, "started": True}
        assert error("nope") == {"ok": False, "error": "nope"}

    @pytest.mark.parametrize(
        "raw", [b"not json", b'"a string"', b"[1,2]", b"\xff\xfe"]
    )
    def test_malformed_messages_raise_value_error(self, raw):
        with pytest.raises(ValueError):
            decode(raw)

    def test_oversized_messages_are_refused(self):
        with pytest.raises(ValueError, match="too large"):
            decode(b"x" * (1 << 21))
