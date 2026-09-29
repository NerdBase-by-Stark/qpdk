"""Both-handler mode: Lua writes fire EventHandlers on PinStyle="Both" controls.

Field truth (cascade-matrix probe, 2026-07-07, Designer 10.3 + Core Nano):
Lua writes to a Both-pin control DO fire its EventHandler on a real Core.
The mock historically never fired on Lua writes, so mock-green tests could
not catch echo loops a real Core executes. This mode is OPT-IN:

    QSYSRuntime(writes_fire_both_handlers=True) + apply_control_schema(rows)
    PluginHarness(..., writes_fire_both_handlers=True)

Default OFF keeps the historical behavior — locked here too.
"""

import textwrap

import pytest

from qpdk.mock_runtime.controls import MockControl
from qpdk.mock_runtime.runtime import QSYSRuntime


# ---------------------------------------------------------------- MockControl


class TestMockControlFiring:
    def test_default_off_lua_writes_stay_silent(self):
        ctrl = MockControl(name="x")
        fired = []
        ctrl.EventHandler = lambda c: fired.append(c.value)
        ctrl.Value = 42
        ctrl.String = "hi"
        ctrl.Boolean = True
        ctrl.Position = 0.5
        assert fired == []

    @pytest.mark.parametrize("write", [
        lambda c: setattr(c, "Value", 42),
        lambda c: setattr(c, "String", "hi"),
        lambda c: setattr(c, "Boolean", True),
        lambda c: setattr(c, "Position", 0.5),
    ])
    def test_armed_control_fires_on_each_setter(self, write):
        ctrl = MockControl(name="x", writes_fire_handlers=True)
        fired = []
        ctrl.EventHandler = lambda c: fired.append(True)
        write(ctrl)
        assert fired == [True]

    def test_guarded_write_pattern_holds(self):
        """The muteWriteGuard idiom: guard-set / write / guard-clear, with the
        handler early-returning while the guard is up. Under the mode the
        handler DOES run on the guarded write — and must see the guard."""
        ctrl = MockControl(name="volume", writes_fire_handlers=True)
        state = {"guard": False, "echoes": 0}

        def handler(c):
            if state["guard"]:
                return
            state["echoes"] += 1

        ctrl.EventHandler = handler
        state["guard"] = True
        ctrl.Value = 50
        state["guard"] = False
        assert state["echoes"] == 0, "guarded feedback write must not echo"
        ctrl.Value = 60  # external-style unguarded Lua write DOES reach handler
        assert state["echoes"] == 1

    def test_unguarded_cascade_hits_depth_cap_not_stack(self):
        """Handler re-writes its own control (missing guard = echo loop):
        recorded to the error sink, never a RecursionError."""
        sink: list = []
        ctrl = MockControl(name="x", writes_fire_handlers=True, error_sink=sink)
        ctrl.EventHandler = lambda c: setattr(c, "Value", c.value + 1)
        ctrl.Value = 1
        assert any("write-cascade depth cap" in m for m in sink)
        assert ctrl._write_fire_depth == 0  # unwound cleanly

    def test_simulate_external_write_unchanged(self):
        ctrl = MockControl(name="x")  # mode OFF
        fired = []
        ctrl.EventHandler = lambda c: fired.append(c.value)
        ctrl.simulate_external_write(7)
        assert fired == [7.0]


# ------------------------------------------------------- apply_control_schema


SCHEMA = [
    {"Name": "volume", "Count": 1, "PinStyle": "Both",
     "ButtonType": None, "UserPin": True},
    {"Name": "status_led", "Count": 1, "PinStyle": "Output",
     "ButtonType": None, "UserPin": True},
    {"Name": "step", "Count": 3, "PinStyle": "Both",
     "ButtonType": "Momentary", "UserPin": True},
    {"Name": "internal", "Count": 1, "PinStyle": None,
     "ButtonType": None, "UserPin": False},
]


class TestApplyControlSchema:
    def test_arms_only_both_controls_when_mode_on(self):
        rt = QSYSRuntime(writes_fire_both_handlers=True)
        rt.apply_control_schema(SCHEMA)
        assert rt.controls["volume"].writes_fire_handlers is True
        assert rt.controls["status_led"].writes_fire_handlers is False
        assert rt.controls["internal"].writes_fire_handlers is False

    def test_count_arrays_stamp_flat_elements(self):
        rt = QSYSRuntime(writes_fire_both_handlers=True)
        rt.apply_control_schema(SCHEMA)
        for i in (1, 2, 3):
            el = rt.controls[f"step {i}"]
            assert el.writes_fire_handlers is True
            assert el.pin_style == "Both"
            assert el.button_type == "Momentary"

    def test_mode_off_stamps_metadata_but_never_arms(self):
        rt = QSYSRuntime()
        rt.apply_control_schema(SCHEMA)
        assert rt.controls["volume"].pin_style == "Both"
        assert rt.controls["volume"].writes_fire_handlers is False


# ------------------------------------------------------------ in-Lua behavior


PLUGIN = textwrap.dedent("""
    PluginInfo = { Name = "BothModeFixture", Version = "0.0.1",
                   Id = "test-both-mode" }

    function GetControls(props)
      return {
        { Name = "both_knob", ControlType = "Knob", ControlUnit = "Integer",
          Min = 0, Max = 100, Count = 1, UserPin = true, PinStyle = "Both" },
        { Name = "input_knob", ControlType = "Knob", ControlUnit = "Integer",
          Min = 0, Max = 100, Count = 1, UserPin = true, PinStyle = "Input" },
        { Name = "fires", ControlType = "Knob", ControlUnit = "Integer",
          Min = 0, Max = 1000, Count = 1 },
      }
    end

    function GetControlLayout(props)
      return {}, {}
    end

    if Controls then
      Controls.fires.Value = 0
      Controls.both_knob.EventHandler = function()
        Controls.fires.Value = Controls.fires.Value + 1
      end
      Controls.input_knob.EventHandler = function()
        Controls.fires.Value = Controls.fires.Value + 100
      end
      function poke_both()  Controls.both_knob.Value = 42 end
      function poke_input() Controls.input_knob.Value = 42 end
    end
""")


@pytest.fixture()
def plugin_file(tmp_path):
    p = tmp_path / "both_mode_fixture.qplug"
    p.write_text(PLUGIN, encoding="utf-8")
    return p


class TestLuaWritesThroughHarness:
    def test_mode_on_lua_write_fires_both_only(self, plugin_file):
        from qpdk.testing.harness import PluginHarness
        h = PluginHarness(plugin_file, writes_fire_both_handlers=True)
        rt = h.make_runtime()
        rt._lua.eval("poke_both()")
        assert rt.controls["fires"].value == 1, \
            "Lua write to Both-pin control must fire its handler"
        rt._lua.eval("poke_input()")
        assert rt.controls["fires"].value == 1, \
            "non-Both controls must NOT fire (behavior unverified on Core)"

    def test_mode_off_default_stays_silent(self, plugin_file):
        from qpdk.testing.harness import PluginHarness
        h = PluginHarness(plugin_file)
        rt = h.make_runtime()
        rt._lua.eval("poke_both()")
        assert rt.controls["fires"].value == 0, \
            "default-OFF harness must keep the historical silent behavior"

    def test_schema_property_exposes_getcontrols_truth(self, plugin_file):
        from qpdk.testing.harness import PluginHarness
        h = PluginHarness(plugin_file)
        rows = {r["Name"]: r for r in h.schema}
        assert rows["both_knob"]["PinStyle"] == "Both"
        assert rows["input_knob"]["PinStyle"] == "Input"
        assert rows["fires"]["PinStyle"] is None
