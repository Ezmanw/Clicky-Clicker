"""Tests for opening applications: .desktop parsing, lookup, and the model."""

from __future__ import annotations

import pytest

from clickyclicker.macros import MacroExecutor, Severity, inspect
from clickyclicker.models import AppRef, Binding, BindingKind, Macro, MacroAction
from clickyclicker.models.action import ActionType
from clickyclicker.services.launcher import LaunchError, exec_argv, find_desktop_file


def desktop(tmp_path, body: str, name: str = "test.desktop"):
    path = tmp_path / name
    path.write_text("[Desktop Entry]\nType=Application\nName=Test\n" + body)
    return path


class TestExecParsing:
    def test_plain_command(self, tmp_path):
        argv, _ = exec_argv(desktop(tmp_path, "Exec=firefox\n"))
        assert argv == ["firefox"]

    @pytest.mark.parametrize("code", ["%u", "%U", "%f", "%F", "%i", "%c", "%k"])
    def test_field_codes_are_dropped(self, tmp_path, code):
        argv, _ = exec_argv(desktop(tmp_path, f"Exec=app --new-window {code}\n"))
        assert argv == ["app", "--new-window"]

    def test_literal_percent_survives(self, tmp_path):
        argv, _ = exec_argv(desktop(tmp_path, "Exec=app --level=100%%\n"))
        assert argv == ["app", "--level=100%"]

    def test_quoted_arguments_stay_together(self, tmp_path):
        argv, _ = exec_argv(desktop(tmp_path, 'Exec="/opt/My App/run" --profile "Two Words"\n'))
        assert argv == ["/opt/My App/run", "--profile", "Two Words"]

    def test_flatpak_style_command(self, tmp_path):
        body = "Exec=/usr/bin/flatpak run --branch=stable --command=foo org.example.Foo @@u %U @@\n"
        argv, _ = exec_argv(desktop(tmp_path, body))
        assert argv[:4] == ["/usr/bin/flatpak", "run", "--branch=stable", "--command=foo"]
        assert "%U" not in argv

    def test_missing_exec_is_an_error(self, tmp_path):
        with pytest.raises(LaunchError, match="does not say how"):
            exec_argv(desktop(tmp_path, ""))

    def test_not_a_desktop_entry_is_an_error(self, tmp_path):
        path = tmp_path / "broken.desktop"
        path.write_text("[Something Else]\nExec=x\n")
        with pytest.raises(LaunchError, match="Desktop Entry"):
            exec_argv(path)


class TestLookup:
    def test_path_hint_is_used_when_present(self, tmp_path):
        path = desktop(tmp_path, "Exec=x\n", "hinted.desktop")
        assert find_desktop_file(AppRef(id="other.desktop", path=str(path))) == path

    def test_found_by_id_in_the_user_data_dir(self, tmp_path, monkeypatch):
        apps = tmp_path / "applications"
        apps.mkdir()
        target = desktop(apps, "Exec=x\n", "org.example.App.desktop")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert find_desktop_file(AppRef(id="org.example.App.desktop")) == target

    def test_dash_in_id_maps_to_a_subdirectory(self, tmp_path, monkeypatch):
        sub = tmp_path / "applications" / "kde"
        sub.mkdir(parents=True)
        target = desktop(sub, "Exec=x\n", "org.foo.desktop")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert find_desktop_file(AppRef(id="kde-org.foo.desktop")) == target

    def test_missing_app_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert find_desktop_file(AppRef(id="definitely-not-installed-xyz.desktop")) is None


class TestModel:
    def test_app_ref_round_trip(self):
        app = AppRef(id="firefox.desktop", name="Firefox", path="/usr/share/applications/f.desktop")
        assert AppRef.from_dict(app.to_dict()) == app

    def test_app_ref_label_falls_back_to_id(self):
        assert AppRef(id="org.example.Foo.desktop").label == "org.example.Foo"

    @pytest.mark.parametrize("bad", [None, {}, {"id": ""}, "firefox", 3])
    def test_app_ref_rejects_garbage(self, bad):
        assert AppRef.from_dict(bad) is None

    def test_launch_binding_round_trip(self):
        binding = Binding(
            input="KEY_F13", kind=BindingKind.LAUNCH_APP, app=AppRef("firefox.desktop", "Firefox")
        )
        restored = Binding.from_dict(binding.to_dict())
        assert restored.kind is BindingKind.LAUNCH_APP
        assert restored.app == binding.app
        assert restored.describe() == "Opens Firefox"
        assert restored.is_valid(set())

    def test_launch_binding_without_app_is_invalid(self):
        assert not Binding(input="KEY_F13", kind=BindingKind.LAUNCH_APP).is_valid(set())

    def test_action_summary(self):
        action = MacroAction.create(ActionType.LAUNCH_APP, app=AppRef("a.desktop", "Calc").to_dict())
        assert action.summary() == "Open Calc"
        assert MacroAction.create(ActionType.LAUNCH_APP).summary() == "Open an application"

    def test_validation_flags_a_step_with_no_app(self):
        macro = Macro(actions=[MacroAction.create(ActionType.LAUNCH_APP)])
        errors = [i for i in inspect(macro) if i.severity is Severity.ERROR]
        assert errors and errors[0].action_index == 0


class TestExecution:
    def test_executor_hands_the_app_to_the_launcher(self, sink):
        opened: list[AppRef] = []
        app = AppRef("calc.desktop", "Calculator")
        macro = Macro(actions=[MacroAction.create(ActionType.LAUNCH_APP, app=app.to_dict())])
        MacroExecutor(sink, launcher=opened.append).start(macro).join(5)
        assert opened == [app]

    def test_step_without_app_is_skipped_not_fatal(self, sink):
        opened: list[AppRef] = []
        macro = Macro(actions=[
            MacroAction.create(ActionType.LAUNCH_APP),
            MacroAction.create(ActionType.KEY_TAP, key="KEY_A", hold_ms=0),
        ])
        handle = MacroExecutor(sink, launcher=opened.append).start(macro)
        handle.join(5)
        assert handle.error is None
        assert opened == []
        assert ("press", "KEY_A") in sink.events
