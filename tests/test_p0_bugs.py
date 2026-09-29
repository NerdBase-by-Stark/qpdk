"""Tests for P0 bugs identified in the deep analysis report.

Tests marked @pytest.mark.xfail document CURRENT BROKEN behavior — the test
encodes the DESIRED behavior (what should happen after the fix is applied).
When a P0 fix is implemented, remove its xfail marker; the test should then
pass, confirming the fix is correct.

Bug reference:
  P0-A  Dead validator stubs (QPDK006/007/008/009 registered but never fire)
  P0-B  Log.Warning/Debug silently accepted by mock (not valid Q-SYS API)
  P0-C  Timer.New() missing from mock (only Timer.Create() exists)
  P0-D  EventHandler fires when Lua sets .Value/.String/.Boolean (wrong)
  P0-E  Trigger() fires EventHandler (wrong — only external stimuli should)
  P0-F  Controls flat access (Controls.name) missing from mock
"""

import pytest

from qpdk.validator import PluginValidator, ValidationResult, IssueSeverity
from qpdk.mock_runtime.controls import MockControl, MockControls
from qpdk.mock_runtime.runtime import QSYSRuntime
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def validator():
    return PluginValidator(qsys_version="9")


@pytest.fixture
def runtime():
    rt = QSYSRuntime()
    yield rt
    rt.reset()


# ---------------------------------------------------------------------------
# P0-A: Dead validator stubs
# ---------------------------------------------------------------------------

class TestP0ADeadStubs:
    """QPDK006-009 are registered as error codes but the check methods are
    never called from validate_file() / validate_string().  These tests
    confirm the stubs are dead today (xfail) and will flip to real
    assertions once the implementations land.
    """

    def test_qpdk006_retired(self, validator):
        """QPDK006 was retired — globals checking is owned by luacheck
        (LUACHECK-W111/W112/W113) directly with no duplicate re-emission.
        Confirm QPDK006 is never emitted.
        """
        code = """
PluginInfo = { Name = "Test~Undef", Version = "1.0.0", Id = "test.undef" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  local x = SomeUndefinedGlobal.doThing()
end
"""
        result = validator.validate_string(code)
        assert not any(i.code == "QPDK006" for i in result.issues)

    def test_qpdk008_first_occurrence_clean(self, validator):
        """First occurrence of a plugin Id should not flag QPDK008."""
        code_a = """
PluginInfo = { Name = "Test~A", Version = "1.0.0", Id = "test.same-id" }
function GetProperties() return {} end
function GetControls() return {} end
"""
        result_a = validator.validate_string(code_a)
        assert not any(i.code == "QPDK008" for i in result_a.issues)

    def test_qpdk008_unique_ids_clean(self, validator):
        """Different plugin Ids should not flag QPDK008."""
        code_a = """
PluginInfo = { Name = "Test~A", Version = "1.0.0", Id = "test.plugin-a" }
function GetProperties() return {} end
function GetControls() return {} end
"""
        code_b = """
PluginInfo = { Name = "Test~B", Version = "1.0.0", Id = "test.plugin-b" }
function GetProperties() return {} end
function GetControls() return {} end
"""
        result_a = validator.validate_string(code_a)
        result_b = validator.validate_string(code_b)
        assert not any(i.code == "QPDK008" for i in result_a.issues)
        assert not any(i.code == "QPDK008" for i in result_b.issues)

    def test_qpdk008_detects_duplicate_id(self, validator):
        """Same plugin Id in two plugins should flag QPDK008 on the second."""
        code_a = """
PluginInfo = { Name = "Test~A", Version = "1.0.0", Id = "test.same-id" }
function GetProperties() return {} end
function GetControls() return {} end
"""
        code_b = """
PluginInfo = { Name = "Test~B", Version = "1.0.0", Id = "test.same-id" }
function GetProperties() return {} end
function GetControls() return {} end
"""
        result_a = validator.validate_string(code_a)
        result_b = validator.validate_string(code_b)
        assert not any(i.code == "QPDK008" for i in result_a.issues)
        assert any(i.code == "QPDK008" for i in result_b.issues)

    def test_qpdk008_message_includes_first_seen(self, validator):
        """QPDK008 message should reference where the Id was first seen."""
        code_a = """
PluginInfo = { Name = "Test~A", Version = "1.0.0", Id = "test.dup" }
function GetProperties() return {} end
function GetControls() return {} end
"""
        code_b = """
PluginInfo = { Name = "Test~B", Version = "1.0.0", Id = "test.dup" }
function GetProperties() return {} end
function GetControls() return {} end
"""
        validator.validate_string(code_a, name="plugin-a.qplug")
        result_b = validator.validate_string(code_b, name="plugin-b.qplug")
        dup_issues = [i for i in result_b.issues if i.code == "QPDK008"]
        assert len(dup_issues) == 1
        assert "plugin-a.qplug" in dup_issues[0].message

    def test_qpdk009_v9_target_no_warning(self, validator):
        """v9 target using v9 globals should NOT flag QPDK009."""
        code = """
PluginInfo = { Name = "Test~VerAPI", Version = "1.0.0", Id = "test.ver-api" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  -- IsManaged is v9.10+ only; fine when targeting v9
  local managed = IsManaged
end
"""
        result = validator.validate_string(code)
        assert not any(i.code == "QPDK009" for i in result.issues)

    def test_qpdk009_detects_version_api_mismatch(self):
        """Using v9-only API against a v7 validator should produce QPDK009."""
        v7_validator = PluginValidator(qsys_version="7")
        code = """
PluginInfo = { Name = "Test~VerAPI", Version = "1.0.0", Id = "test.ver-api" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  local managed = IsManaged
end
"""
        result = v7_validator.validate_string(code)
        assert any(i.code == "QPDK009" for i in result.issues)

    def test_qpdk009_message_includes_global_name(self):
        """QPDK009 message should reference the unavailable global."""
        v7_validator = PluginValidator(qsys_version="7")
        code = """
PluginInfo = { Name = "Test~VerAPI", Version = "1.0.0", Id = "test.ver-api2" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  local managed = IsManaged
  Notifications.Publish("test")
end
"""
        result = v7_validator.validate_string(code)
        ver_issues = [i for i in result.issues if i.code == "QPDK009"]
        names = [i.message for i in ver_issues]
        assert any("IsManaged" in m for m in names)
        assert any("Notifications" in m for m in names)


# ---------------------------------------------------------------------------
# P0-B: Log.Warning / Log.Debug silently accepted by mock
# ---------------------------------------------------------------------------

class TestP0BLogAPIMock:
    """In real Q-SYS, Log.Warning() and Log.Debug() do not exist.
    The mock currently wires them up (so plugins calling them don't crash
    during testing), but this masks a real runtime error that will surface
    on device.  After the fix the mock should raise RuntimeError for both.
    """

    def test_mock_log_warning_raises(self, runtime):
        """P0-B FIXED: Log.Warning() raises RuntimeError in mock because
        Log.Warning() does not exist in the real Q-SYS API."""
        code = """
PluginInfo = { Name = "Test~LogW", Version = "1.0.0", Id = "test.logw" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  Log.Warning("invalid call")
end
"""
        with pytest.raises(RuntimeError):
            runtime.load_string(code)

    def test_mock_log_debug_raises(self, runtime):
        """P0-B FIXED: Log.Debug() raises RuntimeError in mock because
        Log.Debug() does not exist in the real Q-SYS API."""
        code = """
PluginInfo = { Name = "Test~LogD", Version = "1.0.0", Id = "test.logd" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  Log.Debug("invalid call")
end
"""
        with pytest.raises(RuntimeError):
            runtime.load_string(code)

    def test_mock_log_message_still_works(self, runtime):
        """Log.Message() is valid and must always work regardless of P0-B fix."""
        code = """
PluginInfo = { Name = "Test~LogM", Version = "1.0.0", Id = "test.logm" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  Log.Message("this is valid")
end
"""
        runtime.load_string(code)  # Must not raise

    def test_mock_log_error_still_works(self, runtime):
        """Log.Error() is valid and must always work regardless of P0-B fix."""
        code = """
PluginInfo = { Name = "Test~LogE", Version = "1.0.0", Id = "test.loge" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
  Log.Error("this is valid")
end
"""
        runtime.load_string(code)  # Must not raise


# ---------------------------------------------------------------------------
# P0-C: Timer.New() missing from mock
# ---------------------------------------------------------------------------

class TestP0CTimerNew:
    """Real Q-SYS uses Timer.New() (OOP style) while the mock only exposes
    Timer.Create() (factory style).  Plugins that use Timer.New() will fail
    to load in the mock even though they are valid Q-SYS plugins.
    """

    def test_mock_timer_create_exists(self, runtime):
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

    def test_mock_timer_new_works(self, runtime):
        """P0-C FIXED: Timer.New() is now an alias for Timer.Create() so
        plugins using the standard OOP constructor pattern load without error."""
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
        runtime.load_string(code)  # Must not raise


# ---------------------------------------------------------------------------
# P0-D: EventHandler fires on Lua .Value / .String / .Boolean write
# ---------------------------------------------------------------------------

class TestP0DEventHandlerOnWrite:
    """In real Q-SYS, setting a control's Value/String/Boolean from Lua does
    NOT fire the EventHandler — only external stimuli (user interaction, pin
    writes from other components) do.  The mock currently fires the handler
    on every write, which causes false positive test results and infinite
    feedback loops in plugins that update controls inside their own handlers.
    """

    def test_event_handler_does_not_fire_on_value_set(self):
        """P0-D FIXED: setting .Value does NOT fire EventHandler (real Q-SYS behavior)."""
        ctrl = MockControl(name="test")
        calls = []
        ctrl.EventHandler = lambda c: calls.append(c.value)

        ctrl.Value = 42.0

        assert len(calls) == 0, "EventHandler should NOT fire on Lua .Value write"

    def test_simulate_external_write_fires_handler(self):
        """P0-D FIXED: simulate_external_write() DOES fire EventHandler."""
        ctrl = MockControl(name="test")
        calls = []
        ctrl.EventHandler = lambda c: calls.append(c.value)

        ctrl.simulate_external_write(42.0)

        assert len(calls) == 1
        assert calls[0] == 42.0

    def test_event_handler_does_not_fire_on_string_set(self):
        """P0-D FIXED: setting .String does NOT fire EventHandler."""
        ctrl = MockControl(name="test")
        calls = []
        ctrl.EventHandler = lambda c: calls.append(c.string)

        ctrl.String = "hello"

        assert len(calls) == 0, "EventHandler should NOT fire on Lua .String write"

    def test_event_handler_does_not_fire_on_boolean_set(self):
        """P0-D FIXED: setting .Boolean does NOT fire EventHandler."""
        ctrl = MockControl(name="test")
        calls = []
        ctrl.EventHandler = lambda c: calls.append(c.Boolean)

        ctrl.Boolean = True

        assert len(calls) == 0, "EventHandler should NOT fire on Lua .Boolean write"


# ---------------------------------------------------------------------------
# P0-E: Trigger() fires EventHandler
# ---------------------------------------------------------------------------

class TestP0ETrigger:
    """In real Q-SYS, calling ctrl:Trigger() from Lua does NOT fire the
    EventHandler — Trigger() signals to the framework that the control was
    activated but the handler callback only fires in response to external
    (non-Lua) stimuli.  The mock currently fires the handler immediately.
    """

    def test_trigger_does_not_fire_handler(self):
        """P0-E FIXED: Trigger() from Lua does NOT fire EventHandler."""
        ctrl = MockControl(name="test")
        calls = []
        ctrl.EventHandler = lambda c: calls.append(True)

        ctrl.Trigger()

        assert len(calls) == 0, "EventHandler should NOT fire on Lua Trigger()"

    def test_simulate_trigger_fires_handler(self):
        """P0-E FIXED: simulate_trigger() DOES fire EventHandler."""
        ctrl = MockControl(name="test")
        calls = []
        ctrl.EventHandler = lambda c: calls.append(True)

        ctrl.simulate_trigger()

        assert len(calls) == 1


# ---------------------------------------------------------------------------
# P0-F: Controls flat access missing
# ---------------------------------------------------------------------------

class TestP0FControlsFlatAccess:
    """Real Q-SYS plugins access controls via Controls["name"] or Controls.name
    (flat namespace), not only via Controls.Inputs["name"].
    The mock only supports the Inputs/Outputs sub-table pattern.
    """

    def test_controls_inputs_access_works(self):
        """Controls.Inputs["name"] works — existing behavior to preserve."""
        controls = MockControls()
        controls.Inputs["Button"].Value = 1.0
        assert controls.Inputs["Button"].Value == 1.0

    def test_controls_flat_bracket_access_works(self):
        """P0-F FIXED: Controls['name'] finds controls from Inputs or Outputs."""
        controls = MockControls()
        controls.Inputs["Button"].Value = 1.0

        ctrl = controls["Button"]
        assert ctrl.Value == 1.0

    def test_controls_flat_attribute_access_works(self):
        """P0-F FIXED: Controls.name (attribute style) finds controls."""
        controls = MockControls()
        controls.Inputs["Button"].Value = 1.0

        ctrl = controls.Button
        assert ctrl.Value == 1.0

    def test_controls_flat_access_outputs(self):
        """P0-F FIXED: Flat access also finds controls in Outputs."""
        controls = MockControls()
        controls.Outputs["Status"].String = "OK"

        ctrl = controls["Status"]
        assert ctrl.String == "OK"
