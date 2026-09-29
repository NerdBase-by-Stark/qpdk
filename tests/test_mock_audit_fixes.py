"""Regression tests for the 2026-06-02 mock-runtime fidelity fixes
(see internal notes)."""

import pytest

from qpdk.mock_runtime.runtime import QSYSRuntime
from qpdk.mock_runtime.controls import MockControl


@pytest.fixture
def runtime():
    return QSYSRuntime()


class TestMockFidelityFixes:

    def test_m1_runs_lua_53_not_54(self, runtime):
        """M1: Q-SYS Core runs Lua 5.3; the mock must too, so 5.4-only syntax
        doesn't pass the mock yet fail on a Core."""
        runtime.load_string("V = _VERSION")
        assert runtime._lua.globals().V == "Lua 5.3"

    def test_m2_rapidjson_decodes_real_data(self, runtime):
        """M2: decode must return the actual parsed data, not a fake empty table."""
        runtime.load_string(
            'local rj = require("rapidjson")\n'
            "D = rj.decode('{\"key\":\"value\",\"n\":3}')\n"
            "K = D.key\n"
            "N = D.n\n"
        )
        g = runtime._lua.globals()
        assert g.K == "value"
        assert g.N == 3

    def test_m2_json_global_present_and_works(self, runtime):
        """M2: the built-in `json` global was missing entirely (nil)."""
        runtime.load_string("J = type(json)\nX = json.decode('{\"x\":42}').x")
        g = runtime._lua.globals()
        assert g.J == "table"
        assert g.X == 42

    def test_m2_encode_empty_table_contract(self, runtime):
        """encode({}) must still be '{}' (preserves the original contract)."""
        runtime.load_string('E = require("rapidjson").encode({})')
        assert runtime._lua.globals().E == "{}"

    def test_m2_encode_roundtrip(self, runtime):
        runtime.load_string('R = require("rapidjson").encode({a = 1})')
        assert runtime._lua.globals().R == '{"a":1}'

    def test_m3_eventhandler_crash_is_surfaced(self, runtime):
        """M3: an EventHandler crash must be detectable, not silently printed."""
        runtime.load_string(
            "if Controls then\n"
            '  Controls.Inputs["Btn"].EventHandler = function(c) error("boom") end\n'
            "end\n"
        )
        runtime.controls.Inputs["Btn"].simulate_external_write(1)
        assert runtime.had_errors()
        assert any("boom" in m for m in runtime.error_messages)

    def test_m3_timer_crash_is_surfaced(self, runtime):
        runtime.load_string(
            "if Controls then\n"
            "  t = Timer.New()\n"
            '  t.EventHandler = function() error("tickboom") end\n'
            "  t:Start(1)\n"
            "end\n"
        )
        runtime.tick(1.5)
        assert runtime.had_errors()
        assert any("tickboom" in m for m in runtime.error_messages)

    def test_m3_clean_run_has_no_errors(self, runtime):
        runtime.load_string(
            "if Controls then\n"
            '  Controls.Inputs["Btn"].EventHandler = function(c) end\n'
            "end\n"
        )
        runtime.controls.Inputs["Btn"].simulate_external_write(1)
        assert not runtime.had_errors()

    def test_m4_component_chain_does_not_crash(self, runtime):
        """M4: Component.New(n)[ctrl].Value = x previously crashed (empty table)."""
        runtime.load_string(
            'if Controls then Component.New("Amp")["gain"].Value = 7 end'
        )  # must not raise
        assert not runtime.had_errors()

    def test_m4_namedcontrol_roundtrip(self, runtime):
        runtime.load_string(
            "if Controls then\n"
            '  NamedControl.Set("room", 2)\n'
            '  V = NamedControl.Get("room").Value\n'
            "end\n"
        )
        assert runtime._lua.globals().V == 2.0

    def test_m5_pin_schema_fields_exist(self):
        """M5: MockControl carries control-schema fields (no behavior asserted)."""
        c = MockControl(name="x", pin_style="Both", button_type="Trigger", user_pin=True)
        assert c.pin_style == "Both"
        assert c.button_type == "Trigger"
        assert c.user_pin is True

    def test_f7_reset_preserves_error_sink(self, runtime):
        """F7: after reset(), a crashing EventHandler must still be surfaced via
        had_errors() — the recreated MockControls must keep an error_sink wired
        to runtime.errors (the prior reset() dropped it, re-breaking M3)."""
        runtime.reset()
        runtime.load_string(
            "if Controls then\n"
            '  Controls.Inputs["Btn"].EventHandler = function(c) error("postreset") end\n'
            "end\n"
        )
        runtime.controls.Inputs["Btn"].simulate_external_write(1)
        assert runtime.had_errors()
        assert any("postreset" in m for m in runtime.error_messages)

    def test_f7_reset_clears_stale_errors(self, runtime):
        """reset() must also CLEAR errors recorded before the reset."""
        runtime.load_string(
            "if Controls then\n"
            '  Controls.Inputs["Btn"].EventHandler = function(c) error("before") end\n'
            "end\n"
        )
        runtime.controls.Inputs["Btn"].simulate_external_write(1)
        assert runtime.had_errors()
        runtime.reset()
        assert not runtime.had_errors()
