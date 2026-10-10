"""Tests for mock runtime behavioral correctness.

These tests verify that the mock matches REAL Q-SYS runtime behavior.
Many are marked xfail because the mock currently diverges from Q-SYS.

Key Q-SYS behaviors the mock must replicate:
  1. Setting control .Value / .String / .Boolean from Lua does NOT fire EventHandler
  2. Trigger() from Lua does NOT fire EventHandler
  3. Only external stimuli (simulate_external_write / simulate_trigger) fire EventHandler
  4. Timer.New() is an alias for Timer.Create()
  5. Log.Warning() and Log.Debug() are not valid Q-SYS API calls
  6. Controls["name"] and Controls.name flat access work

Each xfail test documents a discrepancy.  Removing the xfail marker after
implementing the fix is the only change needed to verify the fix.
"""

import pytest

from qpdk.mock_runtime.controls import MockControl, MockControls
from qpdk.mock_runtime.runtime import QSYSRuntime
from qpdk.mock_runtime.timer import MockTimer, TimerAPI, TimerScheduler


@pytest.fixture
def ctrl():
    """A fresh MockControl for each test."""
    return MockControl(name="test_ctrl")


@pytest.fixture
def runtime():
    """A fresh QSYSRuntime for each test."""
    rt = QSYSRuntime()
    yield rt
    rt.reset()


# ---------------------------------------------------------------------------
# Control write / EventHandler firing semantics
# ---------------------------------------------------------------------------

class TestControlWriteSemantics:
    """In real Q-SYS, Lua code writing to a control (setting .Value etc.) does
    NOT fire the control's EventHandler.  The handler fires only when the
    control is changed by something external to the plugin (user interaction,
    an upstream schematic wire, etc.).

    This distinction is critical: a plugin that updates its own output controls
    inside an EventHandler would cause an infinite loop if writes also fired
    the handler.
    """

    def test_control_value_set_no_handler_fire(self, ctrl):
        """P0-D FIXED: Setting .Value does NOT fire EventHandler.

        Real Q-SYS behavior: the handler is only called by external stimuli.
        """
        calls = []
        ctrl.EventHandler = lambda c: calls.append(c.value)

        ctrl.Value = 99.0

        assert len(calls) == 0, f"EventHandler fired {len(calls)} time(s) on .Value write"

    def test_control_string_set_no_handler_fire(self, ctrl):
        """P0-D FIXED: Setting .String does NOT fire EventHandler."""
        calls = []
        ctrl.EventHandler = lambda c: calls.append(c.string)

        ctrl.String = "hello"

        assert len(calls) == 0, f"EventHandler fired {len(calls)} time(s) on .String write"

    def test_control_boolean_set_no_handler_fire(self, ctrl):
        """P0-D FIXED: Setting .Boolean does NOT fire EventHandler."""
        calls = []
        ctrl.EventHandler = lambda c: calls.append(c.Boolean)

        ctrl.Boolean = True

        assert len(calls) == 0, f"EventHandler fired {len(calls)} time(s) on .Boolean write"

    def test_simulate_external_write_fires_handler(self, ctrl):
        """P0-D FIXED: simulate_external_write() changes the value AND fires EventHandler.

        This is the correct way to simulate a user or external component
        changing a control during a test.
        """
        calls = []
        ctrl.EventHandler = lambda c: calls.append(c.value)

        ctrl.simulate_external_write(42.0)  # type: ignore[attr-defined]

        assert ctrl.Value == 42.0
        assert len(calls) == 1

    def test_value_stored_after_write(self, ctrl):
        """Value must be stored correctly even if handler does not fire.

        This must always pass regardless of the handler-firing fix.
        """
        ctrl.Value = 7.5
        assert ctrl.Value == 7.5
        assert ctrl.value == 7.5

    def test_string_stored_after_write(self, ctrl):
        """String must be stored correctly after write."""
        ctrl.String = "connected"
        assert ctrl.String == "connected"
        assert ctrl.string == "connected"

    def test_boolean_stored_after_write(self, ctrl):
        """Boolean must update both .Boolean and underlying .value."""
        ctrl.Boolean = True
        assert ctrl.Boolean is True
        assert ctrl.value == 1.0


# ---------------------------------------------------------------------------
# Trigger() semantics
# ---------------------------------------------------------------------------

class TestTriggerSemantics:
    """ctrl:Trigger() in Lua notifies the Q-SYS framework that a button was
    activated, but it does NOT call the plugin's own EventHandler.  The
    EventHandler only fires in response to external (non-Lua) stimuli.

    The mock currently calls the handler immediately when Trigger() is invoked,
    which is wrong and can cause feedback loops in plugins that call Trigger()
    inside an EventHandler.
    """

    def test_trigger_no_handler_fire(self, ctrl):
        """P0-E FIXED: Trigger() does NOT call EventHandler."""
        calls = []
        ctrl.EventHandler = lambda c: calls.append(True)

        ctrl.Trigger()

        assert len(calls) == 0, f"EventHandler fired {len(calls)} time(s) from Trigger()"

    def test_simulate_trigger_fires_handler(self, ctrl):
        """P0-E FIXED: simulate_trigger() fires EventHandler -- this is the
        test-only method for simulating an external button press."""
        calls = []
        ctrl.EventHandler = lambda c: calls.append(True)

        ctrl.simulate_trigger()  # type: ignore[attr-defined]

        assert len(calls) == 1

    def test_no_handler_trigger_does_not_crash(self, ctrl):
        """Trigger() with no EventHandler assigned must not raise.

        This must always pass.
        """
        ctrl.Trigger()  # Must not raise


# ---------------------------------------------------------------------------
# Timer API
# ---------------------------------------------------------------------------

class TestTimerAPI:
    """Timer.New() is the Q-SYS OOP constructor pattern.  The mock only
    exposes Timer.Create() which is the factory pattern.  Both must work.
    """

    def test_timer_create_still_works(self):
        """Timer.Create() must continue to work — regression check."""
        scheduler = TimerScheduler()
        api = TimerAPI(scheduler)

        t = api.Create()

        assert isinstance(t, MockTimer)

    def test_timer_new_exists(self):
        """P0-C FIXED: Timer.New() is an alias for Timer.Create()."""
        scheduler = TimerScheduler()
        api = TimerAPI(scheduler)

        t = api.New()  # type: ignore[attr-defined]

        assert isinstance(t, MockTimer)

    def test_timer_new_in_lua_context(self, runtime):
        """P0-C FIXED: Timer.New() works when called from within a loaded plugin."""
        code = """
PluginInfo = { Name = "Test~TN", Version = "1.0.0", Id = "test.tn" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  local t = Timer.New()
  t.EventHandler = function() end
  t:Start(1.0)
end
"""
        runtime.load_string(code)  # Must not raise after fix

    def test_timer_create_in_lua_context(self, runtime):
        """Timer.Create() does not exist on a Core (KB: Sys/Timer.htm lists
        Timer.New/CallAfter/Now). The mock must fail the call, or a plugin
        written against it passes here and nil-calls at load on hardware.
        Changed in 0.15; this test previously asserted Create() worked.
        """
        code = """
PluginInfo = { Name = "Test~TC", Version = "1.0.0", Id = "test.tc" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  local t = Timer.Create()
end
"""
        with pytest.raises(Exception, match="does not exist"):
            runtime.load_string(code)


# ---------------------------------------------------------------------------
# Log API correctness
# ---------------------------------------------------------------------------

class TestLogAPICorrectness:
    """Log.Message() and Log.Error() are valid Q-SYS APIs.
    Log.Warning() and Log.Debug() are NOT — they will fail on device.
    The mock should raise RuntimeError for invalid methods so plugin authors
    discover the problem during testing rather than in production.
    """

    def test_log_warning_raises(self, runtime):
        """P0-B FIXED: Log.Warning() from Lua raises RuntimeError in mock."""
        code = """
PluginInfo = { Name = "Test~LW", Version = "1.0.0", Id = "test.lw" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  Log.Warning("invalid")
end
"""
        with pytest.raises(RuntimeError):
            runtime.load_string(code)

    def test_log_debug_raises(self, runtime):
        """P0-B FIXED: Log.Debug() from Lua raises RuntimeError in mock."""
        code = """
PluginInfo = { Name = "Test~LD", Version = "1.0.0", Id = "test.ld" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  Log.Debug("invalid")
end
"""
        with pytest.raises(RuntimeError):
            runtime.load_string(code)

    def test_log_message_works(self, runtime):
        """Log.Message() must always work — must not raise."""
        code = """
PluginInfo = { Name = "Test~LM", Version = "1.0.0", Id = "test.lm" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  Log.Message("valid message")
end
"""
        runtime.load_string(code)
        messages = [m for m in runtime.log.messages if m["level"] == "message"]
        assert len(messages) == 1
        assert messages[0]["text"] == "valid message"

    def test_log_error_works(self, runtime):
        """Log.Error() must always work — must not raise."""
        code = """
PluginInfo = { Name = "Test~LE", Version = "1.0.0", Id = "test.le" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  Log.Error("valid error")
end
"""
        runtime.load_string(code)
        errors = [m for m in runtime.log.messages if m["level"] == "error"]
        assert len(errors) == 1
        assert errors[0]["text"] == "valid error"

    def test_log_message_captured(self, runtime):
        """Log.Message() text is captured in runtime.log.messages."""
        code = """
PluginInfo = { Name = "Test~Cap", Version = "1.0.0", Id = "test.cap" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  Log.Message("captured text")
end
"""
        runtime.load_string(code)
        assert any(m["text"] == "captured text" for m in runtime.log.messages)


# ---------------------------------------------------------------------------
# Controls flat access
# ---------------------------------------------------------------------------

class TestControlsFlatAccess:
    """Real Q-SYS plugins use Controls["name"] or Controls.name directly
    (flat namespace) rather than Controls.Inputs["name"].  The mock only
    supports the Inputs/Outputs sub-table pattern, which means many real
    plugins fail to load correctly.
    """

    def test_controls_bracket_access_inputs(self):
        """Controls.Inputs['name'] works — existing behavior that must be preserved."""
        controls = MockControls()
        controls.Inputs["LED"].Value = 1.0
        assert controls.Inputs["LED"].Value == 1.0

    def test_controls_bracket_access_outputs(self):
        """Controls.Outputs['name'] works — existing behavior that must be preserved."""
        controls = MockControls()
        controls.Outputs["Status"].String = "OK"
        assert controls.Outputs["Status"].String == "OK"

    def test_controls_bracket_access(self):
        """P0-F FIXED: Controls['name'] (flat) returns the control from Inputs or Outputs."""
        controls = MockControls()
        controls.Inputs["Button"].Value = 1.0

        ctrl = controls["Button"]  # type: ignore[index]
        assert ctrl.Value == 1.0

    def test_controls_attribute_access(self):
        """P0-F FIXED: Controls.name returns the control from Inputs or Outputs."""
        controls = MockControls()
        controls.Inputs["Button"].Value = 1.0

        ctrl = controls.Button  # type: ignore[attr-defined]
        assert ctrl.Value == 1.0

    def test_controls_flat_in_lua_context(self, runtime):
        """P0-F FIXED: Controls['name'] pattern in a loaded plugin works without error."""
        code = """
PluginInfo = { Name = "Test~Flat", Version = "1.0.0", Id = "test.flat" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  local val = Controls["MyButton"].Value
end
"""
        runtime.load_string(code)  # Must not raise after fix

    def test_controls_inputs_bracket_in_lua_context(self, runtime):
        """Controls.Inputs['name'] works in loaded plugin.

        Fixed as a side-effect of the P0-F Lua-side metatable overhaul --
        the Inputs proxy metatable now correctly delegates to Python.
        """
        code = """
PluginInfo = { Name = "Test~Inp", Version = "1.0.0", Id = "test.inp" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  local val = Controls.Inputs["MyButton"].Value
end
"""
        runtime.load_string(code)  # Must not raise after fix
