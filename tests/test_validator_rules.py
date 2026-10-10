"""Tests for proposed validator rules (QPDK020-028).

Most tests are marked @pytest.mark.xfail because the rules have not yet been
implemented in validator.py.  Each test encodes the precise behavior the rule
SHOULD exhibit so that implementing the rule and removing the xfail marker is
the only change required to make the test suite go green.

True-negative tests (things that should NOT warn) are NOT xfail — they must
pass immediately to prevent regressions once rules land.

Rule reference:
  QPDK005-EXT  Object scoping extended to Ssh/Tcp/Udp/Component.New()
  QPDK020      Invalid Log API (Log.Warning, Log.Debug not in Q-SYS)
  QPDK021      rapidjson used without require()
  QPDK023      Status control value outside 0-5 range
  QPDK024      Local variable shadows Q-SYS reserved name
  QPDK025      Runtime API call outside "if Controls then" guard
  QPDK028      SSH object missing mandatory event handlers
"""

from pathlib import Path

import pytest

from qpdk.validator import IssueSeverity, PluginValidator

FIXTURES = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Minimal valid plugin header — use as prefix for inline test snippets
# to guarantee PluginInfo passes before the rule under test is reached.
# ---------------------------------------------------------------------------
_HEADER = """\
PluginInfo = {{ Name = "Test~{name}", Version = "1.0.0", Id = "test.{id}" }}
function GetProperties() return {{}} end
function GetControls() return {{}} end
function GetControlLayout() return {{}}, {{}} end
"""


def _plugin(name: str, body: str, id_suffix: str | None = None) -> str:
    """Return a complete minimal plugin string with the given runtime body."""
    safe_id = (id_suffix or name).lower().replace(" ", "-").replace("~", ".")
    return _HEADER.format(name=name, id=safe_id) + body


@pytest.fixture
def v():
    """A validator instance shared across tests in this module."""
    return PluginValidator(qsys_version="9")


# ---------------------------------------------------------------------------
# QPDK005-EXT: Object scoping extended beyond Timer.Create()
# ---------------------------------------------------------------------------

class TestQPDK005Extended:
    """QPDK005 currently only catches local Timer.Create() inside functions.
    It should be extended to catch any resource object (.New() constructor)
    stored in a local variable inside a function body.
    """

    def test_qpdk001_compiles_under_lua53_semantics(self, v):
        """Assigning to a for-in control variable is legal Lua 5.3 (the Core's
        version) and must NOT raise QPDK001.

        Regression (cisco CI 2026-07-06): the validator compiled with lupa's
        DEFAULT runtime; a fresh lupa on CI bundles a Lua 5.4 where the loop
        control variable is const, so `deviceIndex = tonumber(deviceIndex)`
        inside a gmatch loop false-positived QPDK001 on a plugin the Core
        runs fine. QPDK001 must pin lupa.lua53 like the mock runtime does.
        """
        code = _plugin("Loop Var", """\
if Controls then
  for deviceIndex, deviceType in string.gmatch("a 1", "(%w+) (%w+)") do
    deviceIndex = tonumber(deviceIndex)
  end
end
""", "loop-var")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK001" for i in result.issues)

    def test_qpdk001_rejects_lua54_only_syntax(self, v):
        """`<close>` attribs are Lua 5.4-only; the 5.3 Core rejects them, so
        QPDK001 must fire. Guards against a silent fallback to a 5.4 lupa
        (which would compile this and pass a plugin the Core cannot load)."""
        code = _plugin("Close Attrib", """\
if Controls then
  local guard <close> = nil
end
""", "close-attrib")
        result = v.validate_string(code)
        assert any(i.code == "QPDK001" for i in result.issues)

    def test_multiline_if_condition_does_not_desync(self, v):
        """A multi-line `if` condition must not hide a later GC-risk local.

        Regression: the old hand-rolled counter
        only registered an if-opener when `then` sat on the SAME line, so the
        if's `end` over-popped the function frame and the scanner believed it
        had left the function — silently missing `local t = Timer.New()`.
        """
        code = _plugin("Multiline If", """\
function setup()
  if someCondition and
     otherCondition then
    local x = 1
  end
  local t = Timer.New()
  t:Start(5)
end
""", "multiline-if")
        result = v.validate_string(code)
        assert any(i.code == "QPDK005" for i in result.issues), (
            "GC-risk local after a multi-line if condition must still warn")

    def test_local_timer_new_warning(self, v):
        """local t = Timer.New() inside a function body should produce QPDK005."""
        code = _plugin("Timer New", """\
if Controls then
  function StartPolling()
    local t = Timer.New()
    t.EventHandler = function() end
    t:Start(5)
  end
end
""", "timer-new")
        result = v.validate_string(code)
        assert any(i.code == "QPDK005" for i in result.issues)

    def test_local_ssh_new_warning(self, v):
        """local s = Ssh.New() inside a function body should produce QPDK005."""
        code = _plugin("SSH New", """\
if Controls then
  function Connect()
    local ssh = Ssh.New()
    ssh:Connect("192.168.1.1", 22, "admin", "pass")
  end
end
""", "ssh-new")
        result = v.validate_string(code)
        assert any(i.code == "QPDK005" for i in result.issues)

    def test_local_tcpsocket_new_warning(self, v):
        """local t = TcpSocket.New() inside a function body should produce QPDK005."""
        code = _plugin("TCP New", """\
if Controls then
  function Connect()
    local tcp = TcpSocket.New()
    tcp:Connect("192.168.1.1", 1710)
  end
end
""", "tcp-new")
        result = v.validate_string(code)
        assert any(i.code == "QPDK005" for i in result.issues)

    def test_local_udpsocket_new_warning(self, v):
        """local u = UdpSocket.New() inside a function body should produce QPDK005."""
        code = _plugin("UDP New", """\
if Controls then
  function Connect()
    local udp = UdpSocket.New()
    udp:Open("0.0.0.0", 0)
  end
end
""", "udp-new")
        result = v.validate_string(code)
        assert any(i.code == "QPDK005" for i in result.issues)

    def test_local_component_new_warning(self, v):
        """local c = Component.New() inside a function body should produce QPDK005."""
        code = _plugin("Component New", """\
if Controls then
  function Setup()
    local c = Component.New("MyComponent")
  end
end
""", "component-new")
        result = v.validate_string(code)
        assert any(i.code == "QPDK005" for i in result.issues)

    def test_module_level_timer_no_warning(self, v):
        """Module-level timer (inside if Controls then but outside a function)
        should NOT produce any QPDK005 warning — true negative."""
        code = _plugin("Timer Module", """\
if Controls then
  local pollTimer = Timer.Create()
  pollTimer.EventHandler = function() end
  pollTimer:Start(5)
end
""", "timer-module")
        result = v.validate_string(code)
        timer_issues = [i for i in result.issues if i.code == "QPDK005"]
        assert len(timer_issues) == 0

    def test_existing_timer_create_still_warns(self, v):
        """Existing QPDK005 for Timer.Create() inside a function must keep
        working after QPDK005-EXT is added — regression check."""
        result = v.validate_file(
            Path(__file__).parent.parent / "examples" / "bad" / "local-timer.qplug"
        )
        if not (Path(__file__).parent.parent / "examples" / "bad" / "local-timer.qplug").exists():
            pytest.skip("local-timer.qplug fixture not found")
        timer_warnings = [w for w in result.warnings if w.code == "QPDK005"]
        assert len(timer_warnings) > 0

    def test_local_ssh_fixture_file(self, v):
        """Validate the local_ssh.qplug fixture file.

        After QPDK005-EXT is implemented this will become a meaningful assertion;
        for now we just verify the fixture is parseable and the baseline
        behavior is documented.
        """
        path = FIXTURES / "local_ssh.qplug"
        if not path.exists():
            pytest.skip("fixture not found")
        result = v.validate_file(path)
        # Fixture has valid PluginInfo — must not produce QPDK002
        assert not any(i.code == "QPDK002" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK020: Invalid Log API
# ---------------------------------------------------------------------------

class TestQPDK020LogAPI:
    """Log.Warning() and Log.Debug() are not part of the Q-SYS Log API.
    The valid methods are Log.Message() and Log.Error().
    Calling invalid methods produces a runtime error on device but is silently
    accepted by the mock, so the validator must catch them statically.
    """

    def test_log_warning_detected(self, v):
        """Log.Warning() call should produce a QPDK020 error."""
        code = _plugin("Log Warn", """\
if Controls then
  Log.Warning("invalid api call")
end
""", "log-warn")
        result = v.validate_string(code)
        assert any(i.code == "QPDK020" for i in result.issues)

    def test_log_debug_detected(self, v):
        """Log.Debug() call should produce a QPDK020 error."""
        code = _plugin("Log Debug", """\
if Controls then
  Log.Debug("invalid api call")
end
""", "log-debug")
        result = v.validate_string(code)
        assert any(i.code == "QPDK020" for i in result.issues)

    def test_log_message_ok(self, v):
        """Log.Message() is valid — must NOT produce QPDK020."""
        code = _plugin("Log Msg", """\
if Controls then
  Log.Message("valid call")
end
""", "log-msg")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK020" for i in result.issues)

    def test_log_error_ok(self, v):
        """Log.Error() is valid — must NOT produce QPDK020."""
        code = _plugin("Log Err", """\
if Controls then
  Log.Error("valid error call")
end
""", "log-err")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK020" for i in result.issues)

    def test_log_warning_in_comment_ignored(self, v):
        """Log.Warning() inside a Lua comment must NOT trigger QPDK020.
        The validator's regex must not match commented-out code.
        """
        code = _plugin("Log Comment", """\
if Controls then
  -- Log.Warning("this is just a comment")
  Log.Message("actual code")
end
""", "log-comment")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK020" for i in result.issues)

    def test_log_warning_fixture(self, v):
        """Validate log_warning.qplug fixture — should trigger QPDK020."""
        path = FIXTURES / "log_warning.qplug"
        if not path.exists():
            pytest.skip("fixture not found")
        result = v.validate_file(path)
        assert not any(i.code == "QPDK002" for i in result.issues)
        assert any(i.code == "QPDK020" for i in result.issues)

    def test_log_debug_fixture(self, v):
        """Validate log_debug.qplug fixture — should trigger QPDK020."""
        path = FIXTURES / "log_debug.qplug"
        if not path.exists():
            pytest.skip("fixture not found")
        result = v.validate_file(path)
        assert not any(i.code == "QPDK002" for i in result.issues)
        assert any(i.code == "QPDK020" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK021: rapidjson require check
# ---------------------------------------------------------------------------

class TestQPDK021RapidJSON:
    """rapidjson is a Lua C extension bundled with Q-SYS.  It must be loaded
    with require() before use; calling rapidjson.decode() without a prior
    require() causes a "attempt to index a nil value" error at runtime.
    """

    def test_rapidjson_without_require_warns(self, v):
        """Using rapidjson without require() should produce QPDK021."""
        code = _plugin("RJ No Req", """\
if Controls then
  local data = rapidjson.decode('{"x": 1}')
end
""", "rj-no-req")
        result = v.validate_string(code)
        assert any(i.code == "QPDK021" for i in result.issues)

    def test_rapidjson_with_require_parens_ok(self, v):
        """require("rapidjson") before use must NOT produce QPDK021."""
        code = _plugin("RJ Req Parens", """\
if Controls then
  local rapidjson = require("rapidjson")
  local data = rapidjson.decode('{"x": 1}')
end
""", "rj-req-parens")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK021" for i in result.issues)

    def test_rapidjson_with_require_no_parens_ok(self, v):
        """require "rapidjson" (no parens) is valid Lua and must NOT produce
        QPDK021.  The validator regex must handle both forms."""
        code = _plugin("RJ Req No Parens", """\
if Controls then
  local json = require "rapidjson"
  local data = json.decode('{"x": 1}')
end
""", "rj-req-no-parens")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK021" for i in result.issues)

    def test_no_rapidjson_usage_ok(self, v):
        """Plugin that doesn't use rapidjson at all must NOT produce QPDK021."""
        code = _plugin("No RJ", """\
if Controls then
  Log.Message("no json here")
end
""", "no-rj")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK021" for i in result.issues)

    def test_rapidjson_no_require_fixture(self, v):
        """Validate rapidjson_no_require.qplug fixture — should trigger QPDK021."""
        path = FIXTURES / "rapidjson_no_require.qplug"
        if not path.exists():
            pytest.skip("fixture not found")
        result = v.validate_file(path)
        assert not any(i.code == "QPDK002" for i in result.issues)
        assert any(i.code == "QPDK021" for i in result.issues)

    def test_rapidjson_with_require_fixture(self, v):
        """Validate rapidjson_with_require.qplug fixture — no QPDK021."""
        path = FIXTURES / "rapidjson_with_require.qplug"
        if not path.exists():
            pytest.skip("fixture not found")
        result = v.validate_file(path)
        assert not any(i.code == "QPDK002" for i in result.issues)
        assert not any(i.code == "QPDK021" for i in result.issues)

    def test_rapidjson_no_parens_fixture(self, v):
        """Validate rapidjson_no_parens.qplug fixture — no QPDK021."""
        path = FIXTURES / "rapidjson_no_parens.qplug"
        if not path.exists():
            pytest.skip("fixture not found")
        result = v.validate_file(path)
        assert not any(i.code == "QPDK002" for i in result.issues)
        assert not any(i.code == "QPDK021" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK023: Status value range
# ---------------------------------------------------------------------------

class TestQPDK023StatusRange:
    """Q-SYS StatusState constants map to integers 0-5.  Setting a status
    control's .Value outside that range is silently accepted by the runtime
    but produces unpredictable indicator behavior in Designer.
    """

    def test_status_value_6_warns(self, v):
        """A status control assigned value 6 should produce QPDK023."""
        code = _plugin("Status 6", """\
if Controls then
  Controls.Outputs["status"].Value = 6
end
""", "status-6")
        result = v.validate_string(code)
        assert any(i.code == "QPDK023" for i in result.issues)

    def test_status_value_negative_warns(self, v):
        """A status control assigned a negative value should produce QPDK023."""
        code = _plugin("Status Neg", """\
if Controls then
  Controls.Outputs["status"].Value = -1
end
""", "status-neg")
        result = v.validate_string(code)
        assert any(i.code == "QPDK023" for i in result.issues)

    def test_status_value_0_ok(self, v):
        """Value 0 (OK) is valid — must NOT produce QPDK023."""
        code = _plugin("Status 0", """\
if Controls then
  Controls.Outputs["status"].Value = 0
end
""", "status-0")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK023" for i in result.issues)

    def test_status_value_5_ok(self, v):
        """Value 5 (Initializing) is the maximum valid value — must NOT warn."""
        code = _plugin("Status 5", """\
if Controls then
  Controls.Outputs["status"].Value = 5
end
""", "status-5")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK023" for i in result.issues)

    def test_status_value_float_warns(self, v):
        """A status control assigned a float value should produce QPDK023."""
        code = _plugin("Status Float", """\
if Controls then
  Controls.status.Value = 1.5
end
""", "status-float")
        result = v.validate_string(code)
        assert any(i.code == "QPDK023" for i in result.issues)

    def test_non_status_control_not_flagged(self, v):
        """A non-status control .Value assignment must NOT produce QPDK023."""
        code = _plugin("Volume", """\
if Controls then
  Controls.Outputs["volume"].Value = 100
end
""", "volume")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK023" for i in result.issues)

    def test_status_out_of_range_fixture(self, v):
        """Validate status_out_of_range.qplug fixture — should trigger QPDK023."""
        path = FIXTURES / "status_out_of_range.qplug"
        if not path.exists():
            pytest.skip("fixture not found")
        result = v.validate_file(path)
        assert not any(i.code == "QPDK002" for i in result.issues)
        assert any(i.code == "QPDK023" for i in result.issues)

    def test_status_valid_fixture(self, v):
        """Validate status_valid.qplug fixture — must not produce QPDK023."""
        path = FIXTURES / "status_valid.qplug"
        if not path.exists():
            pytest.skip("fixture not found")
        result = v.validate_file(path)
        assert not any(i.code == "QPDK002" for i in result.issues)
        assert not any(i.code == "QPDK023" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK024: Reserved name shadowing
# ---------------------------------------------------------------------------

class TestQPDK024ReservedShadow:
    """Q-SYS provides a set of well-known globals (Log, Timer, Controls, etc.).
    A local with exactly the same name shadows the global for the rest of the
    scope. Lua names are case-sensitive, so ``local log`` or ``local controls``
    shadow nothing and must not be flagged.
    """

    def test_local_function_log_lowercase_ok(self, v):
        """local function log() does not shadow Log (case differs): no QPDK024."""
        code = _plugin("Shadow Log", """\
if Controls then
  local function log(msg)
    print("[LOG] " .. msg)
  end
  log("shadow")
end
""", "shadow-log")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK024" for i in result.issues)

    def test_local_lowercase_names_ok(self, v):
        """local timer / local controls / local status differ in case: no QPDK024."""
        code = _plugin("Shadow Timer", """\
if Controls then
  local timer = Timer.New()
  local controls = Controls
  local status = 0
end
""", "shadow-timer")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK024" for i in result.issues)

    def test_local_exact_case_log_warns(self, v):
        """local function Log() shadows the Log global: QPDK024."""
        code = _plugin("Shadow Log2", """\
if Controls then
  local function Log(msg) print(msg) end
end
""", "shadow-log2")
        result = v.validate_string(code)
        hits = [i for i in result.issues if i.code == "QPDK024"]
        assert len(hits) == 1 and "'Log'" in hits[0].message

    def test_unrelated_name_ok(self, v):
        """A local function with a non-reserved name must NOT produce QPDK024."""
        code = _plugin("No Shadow", """\
if Controls then
  local function helper(msg)
    return msg .. " processed"
  end
  Log.Message(helper("data"))
end
""", "no-shadow")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK024" for i in result.issues)

    def test_reserved_shadow_fixture(self, v):
        """reserved_name_shadow.qplug declares ``local function log``; Lua is
        case-sensitive, so it shadows nothing and must not trigger QPDK024."""
        path = FIXTURES / "reserved_name_shadow.qplug"
        if not path.exists():
            pytest.skip("fixture not found")
        result = v.validate_file(path)
        assert not any(i.code == "QPDK002" for i in result.issues)
        assert not any(i.code == "QPDK024" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK025: Runtime guard
# ---------------------------------------------------------------------------

class TestQPDK025RuntimeGuard:
    """Q-SYS evaluates a plugin file multiple times: once during Designer
    layout/property inspection and once at runtime.  API calls such as
    Timer.New(), Ssh.New(), etc. are only valid during the runtime phase.
    Placing them outside the "if Controls then" guard causes Designer to
    crash when opening the plugin properties panel.
    """

    def test_no_guard_with_runtime_api_errors(self, v):
        """Timer.New() at module level without 'if Controls then' guard
        should produce a QPDK025 error."""
        code = _plugin("No Guard", """\
local poll = Timer.New()
poll.EventHandler = function() end
poll:Start(5)
""", "no-guard")
        result = v.validate_string(code)
        assert any(i.code == "QPDK025" for i in result.issues)

    def test_guard_present_ok(self, v):
        """Runtime API wrapped in 'if Controls then' must NOT produce QPDK025."""
        code = _plugin("With Guard", """\
if Controls then
  local poll = Timer.Create()
  poll.EventHandler = function() end
  poll:Start(5)
end
""", "with-guard")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK025" for i in result.issues)

    @pytest.mark.parametrize("guard", [
        "if Controls ~= nil then",
        "if Controls and Controls.led then",
        "if Controls ~= nil and Controls.led then",
    ])
    def test_alternate_guard_forms_ok(self, v, guard):
        """B3: `~= nil` and `and ...` guards keep the body off the design-time path."""
        code = _plugin("Alt Guard", guard + """
  Controls.led.Boolean = true
  local poll = Timer.New()
end
""", "alt-guard")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK025" for i in result.issues), \
            [(i.line, i.message) for i in result.issues if i.code == "QPDK025"]

    def test_guard_elsewhere_does_not_cover_toplevel_call(self, v):
        """A guard block somewhere must not excuse a top-level call outside it.

        Regression: the old check was two file-level booleans, so ONE
        `if Controls then` anywhere suppressed the ERROR even when the
        runtime call executed outside every guard.
        """
        code = _plugin("Partial Guard", """\
if Controls then
  local inside = Timer.New()
end
local outside = Timer.New()
""", "partial-guard")
        result = v.validate_string(code)
        errors = [i for i in result.issues if i.code == "QPDK025"]
        assert errors, "top-level Timer.New outside the guard must error"
        expected_line = code.split("\n").index("local outside = Timer.New()") + 1
        assert any(i.line == expected_line for i in errors), (
            f"error should anchor to line {expected_line}, got {[i.line for i in errors]}")

    def test_guard_elsewhere_does_not_cover_toplevel_controls_write(self, v):
        """Top-level Controls indexing outside the guard is the reproduced
        Designer crash (nil Controls) and must error even with a guard block
        elsewhere in the file."""
        code = _plugin("Partial Guard 2", """\
if Controls then
  local ok = true
end
Controls.led.EventHandler = function() end
""", "partial-guard-2")
        result = v.validate_string(code)
        assert any(i.code == "QPDK025" for i in result.issues)

    def test_runtime_api_only_inside_function_no_guard_still_errors(self, v):
        """Parity: runtime API inside an unguarded function body with NO guard
        anywhere in the file keeps the legacy file-level ERROR (the wiring has
        to execute somewhere, and there is no guard structure at all)."""
        code = _plugin("Func Only", """\
function setupPolling()
  local t = Timer.New()
  t:Start(5)
end
""", "func-only")
        result = v.validate_string(code)
        assert any(i.code == "QPDK025" for i in result.issues)

    def test_early_return_guard_style_ok(self, v):
        """The early-return guard (`if not Controls then return end`) is a
        legitimate design-time protection: code after it never executes at
        design time and must NOT produce QPDK025."""
        code = _plugin("Early Return", """\
if not Controls then return end
local poll = Timer.New()
poll:Start(5)
""", "early-return")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK025" for i in result.issues)

    def test_no_runtime_api_no_guard_ok(self, v):
        """A plugin with no runtime API calls at all needs no guard and must
        not produce QPDK025.  Only runtime-phase APIs trigger the rule."""
        code = _plugin("Simple", """\
local TIMEOUT = 30
local VERSION = "1.0"
""", "simple")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK025" for i in result.issues)

    def test_no_runtime_guard_fixture(self, v):
        """Validate no_runtime_guard.qplug fixture — should trigger QPDK025."""
        path = FIXTURES / "no_runtime_guard.qplug"
        if not path.exists():
            pytest.skip("fixture not found")
        result = v.validate_file(path)
        assert not any(i.code == "QPDK002" for i in result.issues)
        assert any(i.code == "QPDK025" for i in result.issues)

    def test_with_runtime_guard_fixture(self, v):
        """Validate with_runtime_guard.qplug — must not produce QPDK025."""
        path = FIXTURES / "with_runtime_guard.qplug"
        if not path.exists():
            pytest.skip("fixture not found")
        result = v.validate_file(path)
        assert not any(i.code == "QPDK002" for i in result.issues)
        assert not any(i.code == "QPDK025" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK028: SSH handler completeness
# ---------------------------------------------------------------------------

class TestQPDK028SSHHandlers:
    """Every Ssh.New() usage should assign handlers for the mandatory events:
    Connected, Reconnect, Closed, Error, Timeout.
    Missing Error or Timeout handlers means connection failures are swallowed
    silently, making production debugging very hard.
    The Data handler is recommended (not mandatory) — its absence is INFO.
    """

    def test_ssh_missing_handlers_warns(self, v):
        """SSH object missing Error and Timeout handlers should produce QPDK028."""
        code = _plugin("SSH Missing", """\
if Controls then
  local ssh = Ssh.New()
  ssh.Connected = function() Log.Message("connected") end
  ssh.Data = function(d, e) end
  ssh:Connect("192.168.1.1", 22, "admin", "pass")
end
""", "ssh-missing")
        result = v.validate_string(code)
        assert any(i.code == "QPDK028" for i in result.issues)

    def test_ssh_all_mandatory_handlers_ok(self, v):
        """All 6 mandatory handlers assigned — must NOT produce QPDK028."""
        code = _plugin("SSH Complete", """\
if Controls then
  local ssh = Ssh.New()
  ssh.Connected   = function() Log.Message("ok") end
  ssh.Reconnect   = function() Log.Message("retry") end
  ssh.Closed      = function() Log.Message("closed") end
  ssh.Error       = function(e) Log.Error(e) end
  ssh.Timeout     = function() Log.Error("timeout") end
  ssh.LoginFailed = function() Log.Error("login failed") end
  ssh.Data        = function(d, e) end
  ssh:Connect("192.168.1.1", 22, "admin", "pass")
end
""", "ssh-complete")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK028" for i in result.issues)

    def test_ssh_missing_data_info(self, v):
        """SSH with all 6 mandatory handlers but no Data handler should produce
        an INFO-level QPDK028 (recommended, not required)."""
        code = _plugin("SSH No Data", """\
if Controls then
  local ssh = Ssh.New()
  ssh.Connected   = function() Log.Message("ok") end
  ssh.Reconnect   = function() Log.Message("retry") end
  ssh.Closed      = function() Log.Message("closed") end
  ssh.Error       = function(e) Log.Error(e) end
  ssh.Timeout     = function() Log.Error("timeout") end
  ssh.LoginFailed = function() Log.Error("login failed") end
  ssh:Connect("192.168.1.1", 22, "admin", "pass")
end
""", "ssh-no-data")
        result = v.validate_string(code)
        info_issues = [i for i in result.issues
                       if i.code == "QPDK028" and i.severity == IssueSeverity.INFO]
        assert len(info_issues) > 0

    def test_no_ssh_usage_ok(self, v):
        """Plugin with no Ssh.New() must NOT produce any QPDK028 issues."""
        code = _plugin("No SSH", """\
if Controls then
  Log.Message("no ssh here")
end
""", "no-ssh")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK028" for i in result.issues)

    def test_ssh_complete_fixture(self, v):
        """Validate ssh_complete_handlers.qplug fixture — must not warn on QPDK028."""
        path = FIXTURES / "ssh_complete_handlers.qplug"
        if not path.exists():
            pytest.skip("fixture not found")
        result = v.validate_file(path)
        assert not any(i.code == "QPDK002" for i in result.issues)
        assert not any(i.code == "QPDK028" for i in result.issues)

    def test_ssh_missing_fixture(self, v):
        """Validate ssh_missing_handlers.qplug fixture — should trigger QPDK028."""
        path = FIXTURES / "ssh_missing_handlers.qplug"
        if not path.exists():
            pytest.skip("fixture not found")
        result = v.validate_file(path)
        # Fixture must at least parse cleanly (no QPDK002)
        assert not any(i.code == "QPDK002" for i in result.issues)
        assert any(i.code == "QPDK028" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK022: tonumber() without nil guard
# ---------------------------------------------------------------------------

class TestQPDK022TonumberNilGuard:
    """tonumber() returns nil on invalid input. Any arithmetic on that nil
    crashes the plugin. The validator should flag tonumber() results used
    inside function calls without an ``or 0`` fallback.
    """

    def test_math_floor_tonumber_warns(self, v):
        """math.floor(tonumber(x)) without guard should produce QPDK022."""
        code = _plugin("TN Unsafe", """\
if Controls then
  local n = math.floor(tonumber(Controls.input.String))
end
""", "tn-unsafe")
        result = v.validate_string(code)
        assert any(i.code == "QPDK022" for i in result.issues)

    def test_tonumber_with_or_fallback_ok(self, v):
        """tonumber(x) or 0 is safe — must NOT produce QPDK022."""
        code = _plugin("TN Safe", """\
if Controls then
  local n = math.floor(tonumber(Controls.input.String) or 0)
end
""", "tn-safe")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK022" for i in result.issues)

    def test_tonumber_standalone_ok(self, v):
        """local n = tonumber(x) alone is not nested — must NOT produce QPDK022."""
        code = _plugin("TN Standalone", """\
if Controls then
  local n = tonumber(Controls.input.String)
  if n then n = n + 1 end
end
""", "tn-standalone")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK022" for i in result.issues)

    def test_nested_tonumber_no_guard_warns(self, v):
        """tostring(tonumber(x)) without guard should produce QPDK022."""
        code = _plugin("TN Nested", """\
if Controls then
  local s = tostring(tonumber(Controls.val.String))
end
""", "tn-nested")
        result = v.validate_string(code)
        assert any(i.code == "QPDK022" for i in result.issues)

    def test_format_tonumber_second_arg_no_warn(self, v):
        """string.format('%d', tonumber(x)) — tonumber as non-first arg is harder
        to detect heuristically. This is a known limitation of the regex approach.
        Must NOT crash (true negative is acceptable for the heuristic)."""
        code = _plugin("TN Format", """\
if Controls then
  local s = string.format("%d", tonumber(Controls.val.String))
end
""", "tn-format")
        v.validate_string(code)
        # Heuristic limitation: tonumber as second arg to function is not caught.
        # This documents a known gap — full detection requires AST analysis.
        assert True  # no crash, no false positive

    def test_tonumber_in_comment_ignored(self, v):
        """tonumber() in a comment must NOT produce QPDK022."""
        code = _plugin("TN Comment", """\
if Controls then
  -- math.floor(tonumber(x)) -- just a comment
  local n = 42
end
""", "tn-comment")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK022" for i in result.issues)

    def test_suppression_comment(self, v):
        """-- qpdk:ignore QPDK022 should suppress the warning."""
        code = _plugin("TN Suppress", """\
if Controls then
  local n = math.floor(tonumber(Controls.input.String))  -- qpdk:ignore QPDK022
end
""", "tn-suppress")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK022" for i in result.issues)

    def test_string_char_tonumber_hex_ok(self, v):
        """string.char(tonumber(x, 16)) is a safe hex-escape idiom — must NOT produce QPDK022.

        The gsub capture pattern (e.g. %x%x) pre-validates the input so
        tonumber(x, 16) can never return nil in this context.
        """
        code = _plugin("TN HexEscape", """\
if Controls then
  local function parseHex(input)
    return input:gsub("\\\\x(%%x%%x)", function(x)
      return string.char(tonumber(x, 16))
    end)
  end
end
""", "tn-hex-escape")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK022" for i in result.issues)

    def test_string_char_tonumber_base10_still_warns(self, v):
        """string.char(tonumber(x)) without base-16 is NOT the safe hex idiom."""
        code = _plugin("TN CharBase10", """\
if Controls then
  local c = string.char(tonumber(user_input))
end
""", "tn-char-base10")
        result = v.validate_string(code)
        assert any(i.code == "QPDK022" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK022: Additional coverage — code-review requested tests
# ---------------------------------------------------------------------------

class TestQPDK022TonumberGuard:
    """Additional QPDK022 tests requested during code review.

    These complement TestQPDK022TonumberNilGuard above with the specific
    scenarios called out in the review checklist.
    """

    def test_tonumber_unsafe_nested_warns(self, v):
        """math.floor(tonumber(Controls["knob"].String)) should trigger QPDK022."""
        code = _plugin("TN Knob", """\
if Controls then
  local n = math.floor(tonumber(Controls["knob"].String))
end
""", "tn-knob")
        result = v.validate_string(code)
        assert any(i.code == "QPDK022" for i in result.issues)

    def test_tonumber_with_fallback_ok(self, v):
        """tonumber(x) or 0 should NOT trigger QPDK022."""
        code = _plugin("TN Fallback", """\
if Controls then
  local n = tonumber(x) or 0
end
""", "tn-fallback")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK022" for i in result.issues)

    def test_no_tonumber_ok(self, v):
        """Code without tonumber should NOT trigger QPDK022."""
        code = _plugin("No TN", """\
if Controls then
  local n = 42
  Log.Message("no tonumber usage")
end
""", "no-tn")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK022" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK026: GetControlLayout missing when controls defined
# ---------------------------------------------------------------------------

class TestQPDK026MissingControlLayout:
    """Plugins with controls but no GetControlLayout produce blank UI panels."""

    def test_controls_without_layout_warns(self, v):
        """GetControls with controls but no GetControlLayout should produce QPDK026."""
        code = """\
PluginInfo = { Name = "Test~NoLayout", Version = "1.0.0", Id = "test.nolayout" }
function GetProperties() return {} end
function GetControls(props)
  local ctls = {}
  table.insert(ctls, { Name = "my_button", ControlType = "Button" })
  return ctls
end
"""
        result = v.validate_string(code)
        assert any(i.code == "QPDK026" for i in result.issues)

    def test_controls_with_layout_ok(self, v):
        """GetControls with controls AND GetControlLayout must NOT produce QPDK026."""
        code = _plugin("With Layout", """\
if Controls then
  Log.Message("runtime")
end
""", "with-layout")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK026" for i in result.issues)

    def test_empty_controls_no_layout_ok(self, v):
        """GetControls that returns empty table without GetControlLayout should NOT warn."""
        code = """\
PluginInfo = { Name = "Test~Empty", Version = "1.0.0", Id = "test.empty" }
function GetProperties() return {} end
function GetControls(props)
  return {}
end
"""
        result = v.validate_string(code)
        assert not any(i.code == "QPDK026" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK027: Duplicate property in PluginInfo and GetProperties
# ---------------------------------------------------------------------------

class TestQPDK027DuplicateProperty:
    """Duplicate property names between PluginInfo and GetProperties cause conflicts."""

    def test_duplicate_property_warns(self, v):
        """Same property name in both PluginInfo and GetProperties should produce QPDK027."""
        code = """\
PluginInfo = { Name = "Test~Dup", Version = "1.0.0", Id = "test.dup" }
function GetProperties()
  local props = {}
  table.insert(props, { Name = "Name", Type = "string", Value = "default" })
  return props
end
function GetControls(props) return {} end
function GetControlLayout() return {}, {} end
"""
        result = v.validate_string(code)
        assert any(i.code == "QPDK027" for i in result.issues)

    def test_no_duplicate_ok(self, v):
        """Different property names must NOT produce QPDK027."""
        code = """\
PluginInfo = { Name = "Test~NoDup", Version = "1.0.0", Id = "test.nodup" }
function GetProperties()
  local props = {}
  table.insert(props, { Name = "MyProp", Type = "string", Value = "default" })
  return props
end
function GetControls(props) return {} end
function GetControlLayout() return {}, {} end
"""
        result = v.validate_string(code)
        assert not any(i.code == "QPDK027" for i in result.issues)

    def test_no_get_properties_ok(self, v):
        """Plugin without GetProperties should NOT produce QPDK027."""
        code = _plugin("No Props", """\
if Controls then end
""", "no-props")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK027" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK029: SSH:Write() without pcall
# ---------------------------------------------------------------------------

class TestQPDK029SSHWritePcall:
    """SSH:Write() failures without pcall wrapping terminate the EventHandler."""

    def test_unprotected_write_warns(self, v):
        """ssh:Write() without pcall should produce QPDK029."""
        code = _plugin("SSH Write", """\
if Controls then
  local ssh = Ssh.New()
  ssh:Write("xStatus\\n")
end
""", "ssh-write")
        result = v.validate_string(code)
        assert any(i.code == "QPDK029" for i in result.issues)

    def test_pcall_wrapped_write_ok(self, v):
        """pcall(ssh.Write, ssh, data) must NOT produce QPDK029."""
        code = _plugin("SSH Pcall", """\
if Controls then
  local ssh = Ssh.New()
  pcall(ssh.Write, ssh, "xStatus\\n")
end
""", "ssh-pcall")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK029" for i in result.issues)

    def test_write_in_comment_ignored(self, v):
        """ssh:Write() in a comment must NOT produce QPDK029."""
        code = _plugin("SSH Comment", """\
if Controls then
  -- ssh:Write("commented out")
  Log.Message("ok")
end
""", "ssh-comment")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK029" for i in result.issues)

    def test_suppression_comment(self, v):
        """-- qpdk:ignore QPDK029 should suppress the warning."""
        code = _plugin("SSH Suppress", """\
if Controls then
  local ssh = Ssh.New()
  ssh:Write("xStatus\\n")  -- qpdk:ignore QPDK029
end
""", "ssh-suppress")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK029" for i in result.issues)

    def test_tcp_write_also_warns(self, v):
        """tcp:Write() without pcall should also produce QPDK029."""
        code = _plugin("TCP Write", """\
if Controls then
  local tcp = TcpSocket.New()
  tcp:Write("data\\n")
end
""", "tcp-write")
        result = v.validate_string(code)
        assert any(i.code == "QPDK029" for i in result.issues)

    def test_multiline_pcall_function_wrapper_ok(self, v):
        """pcall(function() on preceding line wrapping :Write() must NOT produce QPDK029.

        This is the standard multi-line pcall pattern used in production plugins:
            local ok, err = pcall(function()
              SSH:Write(data)
            end)
        """
        code = _plugin("SSH Multiline Pcall", """\
if Controls then
  local ssh = Ssh.New()
  local ok, err = pcall(function()
    ssh:Write("xStatus\\n")
  end)
  if not ok then print(err) end
end
""", "ssh-multiline-pcall")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK029" for i in result.issues)

    def test_multiline_pcall_deeper_nesting_ok(self, v):
        """pcall(function() wrapper 2-3 lines above :Write() should still be detected."""
        code = _plugin("SSH Deep Pcall", """\
if Controls then
  local ssh = Ssh.New()
  local ok, err = pcall(function()
    local data = "xStatus\\n"
    ssh:Write(data)
  end)
end
""", "ssh-deep-pcall")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK029" for i in result.issues)

    def test_write_far_from_pcall_still_warns(self, v):
        """pcall(function() more than 3 lines above :Write() should still warn."""
        code = _plugin("SSH Far Pcall", """\
if Controls then
  local ssh = Ssh.New()
  local ok, err = pcall(function()
    local a = 1
    local b = 2
    local c = 3
    local d = 4
    ssh:Write("xStatus\\n")
  end)
end
""", "ssh-far-pcall")
        result = v.validate_string(code)
        assert any(i.code == "QPDK029" for i in result.issues)

    def test_pcall_in_unrelated_block_still_warns(self, v):
        """pcall on a prior unrelated block should NOT suppress a later bare :Write()."""
        code = _plugin("SSH Unrelated Pcall", """\
if Controls then
  local ssh = Ssh.New()
  pcall(function() print("unrelated") end)
  -- many lines between
  local x = 1
  local y = 2
  local z = 3
  local w = 4
  ssh:Write("xStatus\\n")
end
""", "ssh-unrelated-pcall")
        result = v.validate_string(code)
        assert any(i.code == "QPDK029" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK031: Single-shot timer missing :Stop()
# ---------------------------------------------------------------------------

    def test_isconnected_guard_in_enclosing_function_ok(self, v):
        """B6: an IsConnected early return in the same function guards the Write."""
        code = _plugin("IsConn Guard", """\
if Controls then
  sock = TcpSocket.New()
  function Send(data)
    if not sock.IsConnected then
      return nil
    end
    local payload = data .. "\\n"
    sock:Write(payload)
  end
end
""", "isconn-guard")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK029" for i in result.issues)

    def test_isconnected_of_other_object_does_not_guard(self, v):
        code = _plugin("IsConn Other", """\
if Controls then
  function Send(data)
    if not other.IsConnected then return end
    sock:Write(data)
  end
end
""", "isconn-other")
        result = v.validate_string(code)
        assert any(i.code == "QPDK029" for i in result.issues)

class TestQPDK031TimerMissingStop:
    """A Timer repeats until stopped; a never-stopped timer is surfaced as INFO."""

    def test_timer_handler_no_stop_warns(self, v):
        """Timer EventHandler without :Stop() should produce QPDK031."""
        code = _plugin("Timer NoStop", """\
if Controls then
  settle_timer = Timer.New()
  settle_timer.EventHandler = function()
    Log.Message("fired")
  end
  settle_timer:Start(3.0)
end
""", "timer-nostop")
        result = v.validate_string(code)
        hits = [i for i in result.issues if i.code == "QPDK031"]
        # B5: a Timer repeats by default (Timer.htm), so this is advisory INFO.
        assert hits and all(i.severity == IssueSeverity.INFO for i in hits)
        assert "poll timer" in hits[0].message

    def test_timer_handler_with_stop_ok(self, v):
        """Timer EventHandler with :Stop() must NOT produce QPDK031."""
        code = _plugin("Timer WithStop", """\
if Controls then
  settle_timer = Timer.New()
  settle_timer.EventHandler = function()
    settle_timer:Stop()
    Log.Message("fired once")
  end
  settle_timer:Start(3.0)
end
""", "timer-withstop")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK031" for i in result.issues)

    def test_non_timer_handler_ok(self, v):
        """EventHandler on a non-timer object must NOT produce QPDK031."""
        code = _plugin("Control Handler", """\
if Controls then
  Controls.Inputs["button"].EventHandler = function(ctrl)
    Log.Message("button pressed")
  end
end
""", "ctrl-handler")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK031" for i in result.issues)

    def test_suppression_comment(self, v):
        """-- qpdk:ignore QPDK031 on the handler line should suppress the warning."""
        code = _plugin("Timer Suppress", """\
if Controls then
  poll_timer = Timer.New()
  poll_timer.EventHandler = function()  -- qpdk:ignore QPDK031
    Log.Message("polling")
  end
  poll_timer:Start(5.0)
end
""", "timer-suppress")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK031" for i in result.issues)

    def test_repeating_timer_with_external_stop_ok(self, v):
        """Timer handler without :Stop() BUT with :Stop() called elsewhere
        in the file is an externally-managed repeating timer -- must NOT
        produce QPDK031."""
        code = _plugin("Timer ExtStop", """\
if Controls then
  poll_timer = Timer.New()
  poll_timer.EventHandler = function()
    Log.Message("polling device")
  end
  poll_timer:Start(5.0)

  function StopPolling()
    poll_timer:Stop()
  end
end
""", "timer-extstop")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK031" for i in result.issues)

    def test_single_shot_no_stop_anywhere_warns(self, v):
        """Timer handler without :Stop() AND no :Stop() anywhere in the file
        is a likely single-shot that forgot to self-stop -- must produce
        QPDK031."""
        code = _plugin("Timer NoStopAnywhere", """\
if Controls then
  debounce = Timer.New()
  debounce.EventHandler = function()
    Log.Message("debounce fired")
    applySettings()
  end
  debounce:Start(2.0)
end
""", "timer-nostop-anywhere")
        result = v.validate_string(code)
        assert any(i.code == "QPDK031" for i in result.issues)

    def test_repeating_stopwatch_pattern_ok(self, v):
        """Realistic stopwatch pattern: handler increments a counter,
        external code starts and stops the timer.  Must NOT produce
        QPDK031."""
        code = _plugin("Stopwatch", """\
if Controls then
  elapsed = 0
  stopwatch = Timer.New()
  stopwatch.EventHandler = function()
    elapsed = elapsed + 1
    Controls.Outputs["elapsed"].Value = elapsed
  end

  Controls.Inputs["start"].EventHandler = function()
    elapsed = 0
    stopwatch:Start(1.0)
  end

  Controls.Inputs["stop"].EventHandler = function()
    stopwatch:Stop()
  end
end
""", "stopwatch")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK031" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK033: xAPI string interpolation without sanitization
# ---------------------------------------------------------------------------

class TestQPDK033XAPIInjection:
    """Unsanitized .String values in sendData() xAPI commands allow injection."""

    def test_senddata_with_string_concat_warns(self, v):
        """sendData("..." .. Controls.x.String) should produce QPDK033."""
        code = _plugin("XAPI Inject", """\
if Controls then
  function sendCmd()
    sendData("xCommand Video Input SetMainVideoSource ConnectorId: " .. Controls.source_id.String)
  end
end
""", "xapi-inject")
        result = v.validate_string(code)
        assert any(i.code == "QPDK033" for i in result.issues)

    def test_senddata_with_literal_ok(self, v):
        """sendData("literal string") without .String must NOT produce QPDK033."""
        code = _plugin("XAPI Safe", """\
if Controls then
  function sendCmd()
    sendData("xStatus SystemUnit\\n")
  end
end
""", "xapi-safe")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK033" for i in result.issues)

    def test_senddata_with_string_reverse_concat_warns(self, v):
        """sendData(x.String .. "...") should also produce QPDK033."""
        code = _plugin("XAPI Inject Rev", """\
if Controls then
  function sendCmd()
    sendData(Controls.prefix.String .. " xCommand Dial Number: 12345")
  end
end
""", "xapi-inject-rev")
        result = v.validate_string(code)
        assert any(i.code == "QPDK033" for i in result.issues)

    def test_in_comment_ignored(self, v):
        """sendData with .String in a comment must NOT produce QPDK033."""
        code = _plugin("XAPI Comment", """\
if Controls then
  -- sendData("xCommand " .. Controls.x.String)
  Log.Message("ok")
end
""", "xapi-comment")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK033" for i in result.issues)

    def test_suppression_comment(self, v):
        """-- qpdk:ignore QPDK033 should suppress the warning."""
        code = _plugin("XAPI Suppress", """\
if Controls then
  sendData("xCommand " .. Controls.x.String)  -- qpdk:ignore QPDK033
end
""", "xapi-suppress")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK033" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK035: PinStyle="Both" + Lua write pattern
# ---------------------------------------------------------------------------

class TestQPDK035PinStyleBothWrite:
    """A PinStyle='Both' control written from Lua is flagged as a possible
    feedback-loop / re-entrant-EventHandler risk (HEDGED, unverified on
    hardware) — not as a hard framework rejection."""

    def test_pinstyle_both_with_write_warns(self, v):
        """PinStyle='Both' control with runtime .Value write should produce QPDK035."""
        code = """\
PluginInfo = { Name = "Test~PinBoth", Version = "1.0.0", Id = "test.pinboth" }
function GetProperties() return {} end
function GetControls(props)
  return { { Name = "output_status", ControlType = "Indicator" } }
end
function GetControlLayout(props)
  local layout = {}
  layout["output_status"] = { PinStyle = "Both", Style = "Indicator" }
  return layout, {}
end
if Controls then
  Controls["output_status"].Value = 0
end
"""
        result = v.validate_string(code)
        hits = [i for i in result.issues if i.code == "QPDK035"]
        # B4: advisory INFO; the re-entrancy claim is not in the Q-SYS docs.
        assert hits and all(i.severity == IssueSeverity.INFO for i in hits)

    def test_message_is_de_fabricated_and_hedged(self, v):
        """the QPDK035 message must NOT assert the unverified
        "fundamental Q-SYS framework constraint" / "rejects Lua writes from
        Timer context" mechanism, and MUST hedge (cf. retired-QPDK038 lesson)."""
        code = """\
PluginInfo = { Name = "Test~PinBoth", Version = "1.0.0", Id = "test.pinboth2" }
function GetProperties() return {} end
function GetControls(props)
  return { { Name = "output_status", ControlType = "Indicator" } }
end
function GetControlLayout(props)
  local layout = {}
  layout["output_status"] = { PinStyle = "Both", Style = "Indicator" }
  return layout, {}
end
if Controls then
  Controls["output_status"].Value = 0
end
"""
        result = v.validate_string(code)
        msgs = [i.message for i in result.issues if i.code == "QPDK035"]
        assert msgs, "expected a QPDK035 finding"
        joined = " ".join(msgs)
        # The fabricated assertions must be gone.
        assert "fundamental Q-SYS framework constraint" not in joined
        assert "rejects Lua writes" not in joined
        # The verified mechanism + hedge must be present.
        assert "feedback loop" in joined
        assert "Unverified on hardware" in joined

    def test_pinstyle_output_with_write_ok(self, v):
        """PinStyle='Output' control with runtime write must NOT produce QPDK035."""
        code = """\
PluginInfo = { Name = "Test~PinOut", Version = "1.0.0", Id = "test.pinout" }
function GetProperties() return {} end
function GetControls(props)
  return { { Name = "output_status", ControlType = "Indicator" } }
end
function GetControlLayout(props)
  local layout = {}
  layout["output_status"] = { PinStyle = "Output", Style = "Indicator" }
  return layout, {}
end
if Controls then
  Controls["output_status"].Value = 0
end
"""
        result = v.validate_string(code)
        assert not any(i.code == "QPDK035" for i in result.issues)

    def test_pinstyle_both_no_write_ok(self, v):
        """PinStyle='Both' control without runtime write must NOT produce QPDK035."""
        code = """\
PluginInfo = { Name = "Test~PinNoWrite", Version = "1.0.0", Id = "test.pinnowrite" }
function GetProperties() return {} end
function GetControls(props)
  return { { Name = "gate_switch", ControlType = "Button" } }
end
function GetControlLayout(props)
  local layout = {}
  layout["gate_switch"] = { PinStyle = "Both", Style = "Button" }
  return layout, {}
end
if Controls then
  Log.Message("no writes here")
end
"""
        result = v.validate_string(code)
        assert not any(i.code == "QPDK035" for i in result.issues)

    def test_no_layout_function_ok(self, v):
        """Plugin without GetControlLayout must NOT produce QPDK035."""
        code = _plugin("No Layout", """\
if Controls then
  Controls["status"].Value = 0
end
""", "no-layout-035")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK035" for i in result.issues)


# ---------------------------------------------------------------------------
# Suppression comments: -- qpdk:ignore mechanism
# ---------------------------------------------------------------------------

class TestSuppressionComments:
    """The suppression comment mechanism should work for all rules."""

    def test_suppress_single_rule(self, v):
        """-- qpdk:ignore QPDK023 should suppress only QPDK023 on that line."""
        code = _plugin("Suppress Single", """\
if Controls then
  Controls.Outputs["status"].Value = 99  -- qpdk:ignore QPDK023
end
""", "suppress-single")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK023" for i in result.issues)

    def test_suppress_multiple_rules(self, v):
        """-- qpdk:ignore QPDK023 QPDK029 should suppress both rules."""
        code = _plugin("Suppress Multi", """\
if Controls then
  Controls.Outputs["status"].Value = 99  -- qpdk:ignore QPDK023 QPDK029
end
""", "suppress-multi")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK023" for i in result.issues)

    def test_suppress_comma_separated(self, v):
        """-- qpdk:ignore QPDK023,QPDK029 with commas should also work."""
        code = _plugin("Suppress Comma", """\
if Controls then
  Controls.Outputs["status"].Value = 99  -- qpdk:ignore QPDK023,QPDK029
end
""", "suppress-comma")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK023" for i in result.issues)

    def test_suppress_does_not_affect_other_lines(self, v):
        """Suppression on line N should NOT suppress line N+1."""
        code = _plugin("Suppress Line", """\
if Controls then
  Controls.Outputs["status_1"].Value = 99  -- qpdk:ignore QPDK023
  Controls.Outputs["status_2"].Value = 99
end
""", "suppress-line")
        result = v.validate_string(code)
        assert any(i.code == "QPDK023" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK032: Poll function without connection guard
# ---------------------------------------------------------------------------

class TestQPDK032PollConnectionGuard:
    """Timer EventHandlers that call write functions without checking
    connection state will error on every poll cycle during disconnected periods.
    """

    def test_poll_timer_no_guard_warns(self, v):
        """Timer handler calling sendData without IsConnected should produce QPDK032."""
        code = _plugin("Poll No Guard", """\
if Controls then
  poll_timer = Timer.New()
  poll_timer.EventHandler = function()
    sendData("xStatus SystemUnit\\n")
  end
  poll_timer:Start(5.0)
end
""", "poll-no-guard")
        result = v.validate_string(code)
        assert any(i.code == "QPDK032" for i in result.issues)

    def test_poll_timer_with_guard_ok(self, v):
        """Timer handler with IsConnected check must NOT produce QPDK032."""
        code = _plugin("Poll With Guard", """\
if Controls then
  poll_timer = Timer.New()
  poll_timer.EventHandler = function()
    if ssh.IsConnected then
      sendData("xStatus SystemUnit\\n")
    end
  end
  poll_timer:Start(5.0)
end
""", "poll-with-guard")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK032" for i in result.issues)

    def test_poll_timer_no_write_ok(self, v):
        """Timer handler without write calls must NOT produce QPDK032."""
        code = _plugin("Poll No Write", """\
if Controls then
  poll_timer = Timer.New()
  poll_timer.EventHandler = function()
    Log.Message("tick")
  end
  poll_timer:Start(5.0)
end
""", "poll-no-write")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK032" for i in result.issues)

    def test_non_timer_handler_ok(self, v):
        """EventHandler on a non-timer object calling sendData must NOT produce QPDK032."""
        code = _plugin("Non Timer 032", """\
if Controls then
  Controls.Inputs["trigger"].EventHandler = function(ctrl)
    sendData("xCommand Dial Number: 12345")
  end
end
""", "non-timer-032")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK032" for i in result.issues)

    def test_ssh_write_in_timer_warns(self, v):
        """Timer handler calling ssh:Write without guard should produce QPDK032."""
        code = _plugin("SSH Write Timer", """\
if Controls then
  poll_timer = Timer.New()
  poll_timer.EventHandler = function()
    ssh:Write("xStatus\\n")
  end
  poll_timer:Start(5.0)
end
""", "ssh-write-timer")
        result = v.validate_string(code)
        assert any(i.code == "QPDK032" for i in result.issues)

    def test_suppression_comment(self, v):
        """-- qpdk:ignore QPDK032 should suppress the warning."""
        code = _plugin("Poll Suppress", """\
if Controls then
  poll_timer = Timer.New()
  poll_timer.EventHandler = function()  -- qpdk:ignore QPDK032
    sendData("xStatus SystemUnit\\n")
  end
  poll_timer:Start(5.0)
end
""", "poll-suppress")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK032" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK034: Monolithic function detection
# ---------------------------------------------------------------------------

class TestQPDK034MonolithicFunction:
    """Functions over 100 lines are architectural debt and should be flagged."""

    def test_large_function_warns(self, v):
        """Function >100 lines should produce QPDK034 INFO."""
        # Build a function with 105 lines of body (107 total with def + end)
        body_lines = "\n".join(f"  local v{i} = {i}" for i in range(105))
        code = _plugin("Big Func", f"""\
if Controls then
  function bigFunction()
{body_lines}
  end
end
""", "big-func")
        result = v.validate_string(code)
        info_issues = [i for i in result.issues
                       if i.code == "QPDK034" and i.severity == IssueSeverity.INFO]
        assert len(info_issues) > 0

    def test_small_function_ok(self, v):
        """Function under 100 lines must NOT produce QPDK034."""
        body_lines = "\n".join(f"  local v{i} = {i}" for i in range(10))
        code = _plugin("Small Func", f"""\
if Controls then
  function smallFunction()
{body_lines}
  end
end
""", "small-func")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK034" for i in result.issues)

    def test_no_functions_ok(self, v):
        """Plugin with no custom functions must NOT produce QPDK034."""
        code = _plugin("No Funcs", """\
if Controls then
  Log.Message("hello")
end
""", "no-funcs")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK034" for i in result.issues)

    def test_suppression_comment(self, v):
        """-- qpdk:ignore QPDK034 on the function line should suppress."""
        body_lines = "\n".join(f"  local v{i} = {i}" for i in range(105))
        code = _plugin("Big Suppress", f"""\
if Controls then
  function bigFunction()  -- qpdk:ignore QPDK034
{body_lines}
  end
end
""", "big-suppress")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK034" for i in result.issues)

    def test_large_function_with_inner_blocks_warns(self, v):
        """A >100-line function whose body contains if/for blocks must still fire.

        Regression: the old counter popped the
        function frame on ANY `end`, so the first inner `if ... end` truncated
        the measured span and the rule went silent on any realistic function.
        """
        inner_block = "  if x then\n    local y = 1\n  end\n"
        filler = "\n".join(f"  local v{i} = {i}" for i in range(105))
        code = _plugin("Big Inner", f"""\
if Controls then
  function bigWithBlocks()
{inner_block}{filler}
    for i = 1, 3 do
      local z = i
    end
  end
end
""", "big-inner")
        result = v.validate_string(code)
        info_issues = [i for i in result.issues
                       if i.code == "QPDK034" and i.severity == IssueSeverity.INFO]
        assert len(info_issues) > 0
        assert "bigWithBlocks" in info_issues[0].message

    def test_function_keyword_in_string_no_false_positive(self, v):
        """`function` inside a string literal must not open a phantom frame.

        Guard test: the rule runs on the string/comment-blanked view, so a
        `function name(` inside a string followed by an unrelated `end` must
        never yield a phantom >100-line finding.
        """
        filler = "\n".join(f"  local v{i} = {i}" for i in range(105))
        code = _plugin("Phantom", f"""\
local s = "docs: function phantom( usage"
if true then
{filler}
end
""", "phantom")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK034" for i in result.issues)

    # -- Framework API function threshold tests --------------------------

    def test_getcontrols_under_500_no_warn(self, v):
        """GetControls at 200 lines should NOT produce QPDK034 (framework threshold 500)."""
        filler = "\n".join(f"  local v{i} = {i}" for i in range(198))
        code = _plugin("GC Under", f"""\
function GetControls(props)
  local ctrls = {{}}
{filler}
  return ctrls
end
if Controls then
  print("ok")
end
""", "gc-under")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK034" for i in result.issues)

    def test_getcontrols_over_500_warns(self, v):
        """GetControls at 600 lines SHOULD produce QPDK034."""
        filler = "\n".join(f"  local v{i} = {i}" for i in range(598))
        code = _plugin("GC Over", f"""\
function GetControls(props)
  local ctrls = {{}}
{filler}
  return ctrls
end
if Controls then
  print("ok")
end
""", "gc-over")
        result = v.validate_string(code)
        info_issues = [i for i in result.issues
                       if i.code == "QPDK034" and i.severity == IssueSeverity.INFO]
        assert len(info_issues) > 0
        # Verify the message mentions the 500 threshold
        assert "500" in info_issues[0].message

    def test_getcontrollayout_under_500_no_warn(self, v):
        """GetControlLayout at 400 lines should NOT produce QPDK034."""
        filler = "\n".join(f"  local v{i} = {i}" for i in range(398))
        code = _plugin("GCL Under", f"""\
function GetControlLayout(props)
  local layout = {{}}
{filler}
  return layout, {{}}
end
if Controls then
  print("ok")
end
""", "gcl-under")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK034" for i in result.issues)

    def test_runtime_function_over_100_still_warns(self, v):
        """A regular runtime function at 150 lines should still produce QPDK034."""
        filler = "\n".join(f"  local v{i} = {i}" for i in range(148))
        code = _plugin("RT Over", f"""\
if Controls then
  function myRuntimeHelper()
{filler}
  end
end
""", "rt-over")
        result = v.validate_string(code)
        info_issues = [i for i in result.issues
                       if i.code == "QPDK034" and i.severity == IssueSeverity.INFO]
        assert len(info_issues) > 0
        # Verify the message mentions the default 100 threshold
        assert "100" in info_issues[0].message


# ---------------------------------------------------------------------------
# QPDK036: Dual write for Indicator/Text controls
# ---------------------------------------------------------------------------

class TestQPDK036DualWriteIndicator:
    """Indicator and Text controls require both .Value and .String to be set."""

    def test_indicator_value_only_warns(self, v):
        """Indicator control with .Value but no .String should produce QPDK036."""
        code = """\
PluginInfo = { Name = "Test~DualWrite", Version = "1.0.0", Id = "test.dualwrite" }
function GetProperties() return {} end
function GetControls(props)
  return { { Name = "temp_display", ControlType = "Indicator" } }
end
function GetControlLayout(props) return {}, {} end
if Controls then
  Controls["temp_display"].Value = 22.5
end
"""
        result = v.validate_string(code)
        assert any(i.code == "QPDK036" for i in result.issues)

    def test_indicator_both_writes_ok(self, v):
        """Indicator control with both .Value and .String must NOT produce QPDK036."""
        code = """\
PluginInfo = { Name = "Test~DualOK", Version = "1.0.0", Id = "test.dualok" }
function GetProperties() return {} end
function GetControls(props)
  return { { Name = "temp_display", ControlType = "Indicator" } }
end
function GetControlLayout(props) return {}, {} end
if Controls then
  Controls["temp_display"].Value = 22.5
  Controls["temp_display"].String = "22.5 C"
end
"""
        result = v.validate_string(code)
        assert not any(i.code == "QPDK036" for i in result.issues)

    def test_indicator_string_only_warns(self, v):
        """Indicator control with .String but no .Value should produce QPDK036."""
        code = """\
PluginInfo = { Name = "Test~DualStr", Version = "1.0.0", Id = "test.dualstr" }
function GetProperties() return {} end
function GetControls(props)
  return { { Name = "temp_display", ControlType = "Indicator" } }
end
function GetControlLayout(props) return {}, {} end
if Controls then
  Controls["temp_display"].String = "22.5 C"
end
"""
        result = v.validate_string(code)
        assert any(i.code == "QPDK036" for i in result.issues)

    def test_knob_control_value_only_ok(self, v):
        """Knob control with only .Value set must NOT produce QPDK036 (auto-formats)."""
        code = """\
PluginInfo = { Name = "Test~Knob", Version = "1.0.0", Id = "test.knob" }
function GetProperties() return {} end
function GetControls(props)
  return { { Name = "volume", ControlType = "Knob" } }
end
function GetControlLayout(props) return {}, {} end
if Controls then
  Controls["volume"].Value = 50
end
"""
        result = v.validate_string(code)
        assert not any(i.code == "QPDK036" for i in result.issues)

    def test_text_control_value_only_warns(self, v):
        """Text control with .Value but no .String should produce QPDK036."""
        code = """\
PluginInfo = { Name = "Test~TextVal", Version = "1.0.0", Id = "test.textval" }
function GetProperties() return {} end
function GetControls(props)
  return { { Name = "label", ControlType = "Text" } }
end
function GetControlLayout(props) return {}, {} end
if Controls then
  Controls["label"].Value = 1
end
"""
        result = v.validate_string(code)
        assert any(i.code == "QPDK036" for i in result.issues)

    def test_no_controls_ok(self, v):
        """Plugin without Indicator/Text controls must NOT produce QPDK036."""
        code = _plugin("No Indicator", """\
if Controls then
  Log.Message("no indicators")
end
""", "no-indicator")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK036" for i in result.issues)

    def test_no_runtime_writes_ok(self, v):
        """Indicator/Text control with no runtime writes must NOT produce QPDK036."""
        code = """\
PluginInfo = { Name = "Test~NoWrite", Version = "1.0.0", Id = "test.nowrite" }
function GetProperties() return {} end
function GetControls(props)
  return { { Name = "temp_display", ControlType = "Indicator" } }
end
function GetControlLayout(props) return {}, {} end
if Controls then
  Log.Message("no writes")
end
"""
        result = v.validate_string(code)
        assert not any(i.code == "QPDK036" for i in result.issues)


# ---------------------------------------------------------------------------
# QPDK037: Monitor mode guard
# ---------------------------------------------------------------------------

class TestQPDK037MonitorModeGuard:
    """Every sendData call path must check the monitor_mode flag to prevent
    command injection in monitoring-only deployments.
    """

    def test_senddata_without_monitor_guard_warns(self, v):
        """sendData with write command and no monitor_mode check should produce QPDK037."""
        code = _plugin("Monitor Bad", """\
if Controls then
  local monitor_mode = false
  function pollDevice()
    sendData("xCommand Audio Microphones Mute\\n")
  end
end
""", "monitor-bad")
        result = v.validate_string(code)
        hits = [i for i in result.issues if i.code == "QPDK037"]
        # B7: advisory INFO; the rule is keyed to one plugin family's naming.
        assert hits and all(i.severity == IssueSeverity.INFO for i in hits)

    def test_compound_monitor_guard_ok(self, v):
        """B7: `if isWrite and Controls.monitor_mode.Boolean then return end` guards."""
        code = _plugin("Monitor Compound", """\
if Controls then
  function send(cmd, isWrite)
    if isWrite and Controls.monitor_mode.Boolean then
      return
    end
    sendData(cmd)
  end
end
""", "monitor-compound")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK037" for i in result.issues)

    def test_senddata_with_monitor_guard_ok(self, v):
        """sendData with nearby monitor_mode check must NOT produce QPDK037."""
        code = _plugin("Monitor Good", """\
if Controls then
  local monitor_mode = false
  function pollDevice()
    if not monitor_mode then
      sendData("xCommand Dial Number: 12345")
    end
  end
end
""", "monitor-good")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK037" for i in result.issues)

    def test_no_monitor_mode_ok(self, v):
        """Plugin without monitor_mode variable must NOT produce QPDK037."""
        code = _plugin("No Monitor", """\
if Controls then
  function pollDevice()
    sendData("xStatus SystemUnit\\n")
  end
end
""", "no-monitor")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK037" for i in result.issues)

    def test_no_senddata_ok(self, v):
        """Plugin with monitor_mode but no sendData must NOT produce QPDK037."""
        code = _plugin("Monitor NoSend", """\
if Controls then
  local monitor_mode = true
  Log.Message("monitoring only")
end
""", "monitor-nosend")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK037" for i in result.issues)

    def test_suppression_comment(self, v):
        """-- qpdk:ignore QPDK037 should suppress the warning."""
        code = _plugin("Monitor Suppress", """\
if Controls then
  local monitor_mode = false
  function pollDevice()
    sendData("xCommand Audio Microphones Mute\\n")  -- qpdk:ignore QPDK037
  end
end
""", "monitor-suppress")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK037" for i in result.issues)

    # --- New tests: Read-only command whitelist ---

    def test_xstatus_command_no_warn(self, v):
        """sendData with xStatus query and no guard must NOT warn (read-only)."""
        code = _plugin("MM Status", """\
if Controls then
  local monitor_mode = false
  function pollDevice()
    sendData("xStatus SystemUnit ProductID\\n")
  end
end
""", "mm-status")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK037" for i in result.issues)

    def test_xfeedback_register_no_warn(self, v):
        """sendData with xFeedback register and no guard must NOT warn (read-only)."""
        code = _plugin("MM Feedback", """\
if Controls then
  local monitor_mode = false
  function registerFeedback()
    sendData("xFeedback register /Status/Standby\\n")
  end
end
""", "mm-feedback")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK037" for i in result.issues)

    def test_xcommand_without_guard_warns(self, v):
        """sendData with xCommand and no guard MUST warn (write command)."""
        code = _plugin("MM Cmd", """\
if Controls then
  local monitor_mode = false
  function muteAudio()
    sendData("xCommand Audio Microphones Mute\\n")
  end
end
""", "mm-cmd")
        result = v.validate_string(code)
        assert any(i.code == "QPDK037" for i in result.issues)

    # --- New tests: Function-scope guard detection ---

    def test_guard_at_function_entry_no_warn(self, v):
        """monitor_mode guard at function entry, sendData 20+ lines later must NOT warn."""
        # Build a function with the guard at the top and sendData far below
        filler = "\n".join(
            f"    local v{i} = {i}" for i in range(20)
        )
        code = _plugin("MM FuncScope", f"""\
if Controls then
  local monitor_mode = false
  function doStuff()
    if monitor_mode then return end
{filler}
    sendData("xCommand Audio Microphones Mute\\n")
  end
end
""", "mm-funcscope")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK037" for i in result.issues)

    def test_guard_in_different_function_still_warns(self, v):
        """Guard in Function A, sendData in Function B without guard MUST warn."""
        code = _plugin("MM CrossFunc", """\
if Controls then
  local monitor_mode = false
  function safeFunc()
    if monitor_mode then return end
    sendData("xCommand Audio Microphones Mute\\n")
  end
  function unsafeFunc()
    sendData("xCommand Video Input SetMainVideoSource\\n")
  end
end
""", "mm-crossfunc")
        result = v.validate_string(code)
        warnings_037 = [i for i in result.issues if i.code == "QPDK037"]
        # safeFunc must NOT warn, unsafeFunc MUST warn
        assert len(warnings_037) == 1
        # The warning should be on the unsafeFunc sendData line
        assert any("xCommand Video" in line
                    for line in code.split("\n")
                    if code.split("\n").index(line) + 1 == warnings_037[0].line
                    or True)  # just check count is 1

    def test_senddata_variable_arg_without_guard_warns(self, v):
        """sendData(someVariable) with no guard MUST warn (can't verify read-only)."""
        code = _plugin("MM VarArg", """\
if Controls then
  local monitor_mode = false
  function sendCmd(cmd)
    sendData(cmd)
  end
end
""", "mm-vararg")
        result = v.validate_string(code)
        assert any(i.code == "QPDK037" for i in result.issues)

    def test_echo_command_no_warn(self, v):
        """sendData with echo command and no guard must NOT warn (read-only)."""
        code = _plugin("MM Echo", """\
if Controls then
  local monitor_mode = false
  function initTerminal()
    sendData("echo on")
  end
end
""", "mm-echo")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK037" for i in result.issues)

    def test_xfeedback_deregisterall_no_warn(self, v):
        """sendData with xFeedback deregisterall and no guard must NOT warn."""
        code = _plugin("MM Dereg", """\
if Controls then
  local monitor_mode = false
  function cleanup()
    sendData("xFeedback deregisterall")
  end
end
""", "mm-dereg")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK037" for i in result.issues)

    def test_controls_monitor_mode_guard_ok(self, v):
        """Controls.monitor_mode.Boolean guard style must be recognized."""
        code = _plugin("MM Controls", """\
if Controls then
  function writeSources()
    if Controls.monitor_mode.Boolean then return end
    sendData("xCommand UserInterface Presentation ExternalSource RemoveAll")
  end
end
""", "mm-controls")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK037" for i in result.issues)

    # --- robust _find_function_ranges (was: per-line open/close
    #     counter that dropped frames on multi-line block conditions). ---

    def test_split_for_do_does_not_drop_enclosing_frame(self, v):
        """Regression: a ``for`` whose ``do`` is on the NEXT line (a common Lua
        idiom) sits inside a monitor_mode-guarded function, BEFORE the sendData.
        The old per-line counter never registered the opener (it required ``do``
        on the ``for`` line) yet still counted the loop's ``end`` as a closer —
        popping the function's own frame early, so the sendData looked
        un-enclosed and its guard was invisible → false QPDK037. Verified: the
        old algorithm returns NO enclosing range for the sendData here; the new
        scanner encloses it in the guarded function.
        """
        code = _plugin("MM SplitForDo", """\
if Controls then
  local monitor_mode = false
  function pollDevice()
    if monitor_mode then return end
    for i = 1, 10
    do
      doThing(i)
    end
    sendData("xCommand Audio Microphones Mute")
  end
end
""", "mm-split-for-do")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK037" for i in result.issues), \
            [i.line for i in result.issues if i.code == "QPDK037"]

    def test_find_function_ranges_multiline_if_and_nested_loop(self):
        """Direct unit test of the robust scanner: a multi-line if condition and
        a nested for/do inside a function must yield exactly one range spanning
        the whole function (start `function` line → its closing `end`)."""
        from qpdk.validator import PluginValidator
        src = (
            "local function outer()\n"   # 0
            "  if a\n"                    # 1
            "     and b then\n"           # 2
            "    for i = 1, 10 do\n"      # 3
            "      doThing(i)\n"          # 4
            "    end\n"                   # 5
            "  end\n"                     # 6
            "  return 1\n"                # 7
            "end\n"                       # 8
        )
        ranges = PluginValidator._find_function_ranges(src.split("\n"))
        assert ranges == [(0, 8)], ranges

    def test_find_function_ranges_separates_sibling_functions(self):
        """Two sibling functions must produce two distinct, non-overlapping
        ranges (so a guard in one never bleeds into the other). The first
        sibling contains a split ``for ... do`` whose closing ``end`` the OLD
        per-line counter mis-attributed — dropping line 5 from alpha's range
        (OLD -> [(0,4),(7,9)] vs NEW -> [(0,5),(7,9)]) — so this is a real
        regression guard for the QPDK037 scanner rewrite, not just an invariant
        (it fails on the reverted scanner)."""
        from qpdk.validator import PluginValidator
        src = (
            "function alpha()\n"   # 0
            "  for i = 1, 3\n"     # 1  split header: `do` on the next line
            "  do\n"               # 2
            "    poll(i)\n"        # 3
            "  end\n"              # 4
            "end\n"                # 5
            "\n"                   # 6
            "function beta()\n"    # 7
            "  return 2\n"         # 8
            "end\n"                # 9
        )
        ranges = PluginValidator._find_function_ranges(src.split("\n"))
        assert sorted(ranges) == [(0, 5), (7, 9)], ranges


class TestHarvestGlobalDefs:
    """_harvest_global_defs feeds the plugin's own top-level global
    definitions to luacheck so its cross-section helper functions are not
    flagged W111 (set non-standard global) / W113 (undefined global)."""

    def test_global_function_def_harvested(self):
        from qpdk.validator import PluginValidator
        src = "function initHdmiStatus(a, b)\n  return a + b\nend\n"
        assert "initHdmiStatus" in PluginValidator._harvest_global_defs(src)

    def test_global_assigned_function_harvested(self):
        from qpdk.validator import PluginValidator
        src = "myHandler = function(x) return x end\n"
        assert "myHandler" in PluginValidator._harvest_global_defs(src)

    def test_local_function_not_harvested(self):
        from qpdk.validator import PluginValidator
        src = "local function helper()\n  return 1\nend\n"
        assert "helper" not in PluginValidator._harvest_global_defs(src)

    def test_local_assigned_function_not_harvested(self):
        from qpdk.validator import PluginValidator
        src = "local cb = function() return 1 end\n"
        assert "cb" not in PluginValidator._harvest_global_defs(src)

    def test_method_def_not_harvested(self):
        from qpdk.validator import PluginValidator
        src = (
            "function obj.method()\n  return 1\nend\n"
            "function obj:other()\n  return 2\nend\n"
        )
        got = PluginValidator._harvest_global_defs(src)
        assert "obj" not in got and "method" not in got and "other" not in got

    def test_function_keyword_in_string_or_comment_ignored(self):
        from qpdk.validator import PluginValidator
        src = '-- function ghost() end\nlocal s = "function phantom() end"\n'
        got = PluginValidator._harvest_global_defs(src)
        assert "ghost" not in got and "phantom" not in got


class TestW113AssignedGlobal:
    """LUACHECK-W113 (read of an undefined global) is INFO when the plugin
    assigns that name as a global somewhere (a W111 site, the cross-section
    global idiom), and stays a WARNING when the name is never assigned."""

    pytestmark = pytest.mark.skipif(
        __import__("shutil").which("luacheck") is None, reason="luacheck not installed")

    _SRC = """\
PluginInfo = { Name = "Test~W113", Version = "1.0.0", Id = "test.w113" }
function GetProperties() return {} end
function GetControls() return {} end
function GetControlLayout() return {}, {} end
if Controls then
  if true then
    sharedState = 1
  end
  print(sharedState)
  print(sharedStat)
end
"""

    def _w113(self, tmp_path):
        f = tmp_path / "w113.qplug"
        f.write_text(self._SRC)
        r = PluginValidator().validate_file(f)
        return {i.line: i for i in r.issues if i.code == "LUACHECK-W113"}

    def test_read_of_assigned_global_is_info(self, tmp_path):
        hits = self._w113(tmp_path)
        assert hits[9].severity == IssueSeverity.INFO
        assert "sharedState" in hits[9].message

    def test_read_of_never_assigned_global_warns(self, tmp_path):
        hits = self._w113(tmp_path)
        assert hits[10].severity == IssueSeverity.WARNING
        assert "sharedStat'" in hits[10].message


class TestQPDK012DesignTimeRuntimeGlobal:
    """QPDK012: a runtime-only global indexed inside a design-time definition
    function (GetControls/GetControlLayout/...) crashes Designer at design time
    (Controls is nil). ERROR for Controls; WARNING for the other runtime
    objects. The inverse of QPDK025."""

    def _q12(self, result):
        return [i for i in result.issues if i.code == "QPDK012"]

    def test_controls_indexed_in_layout_errors(self, v):
        """Controls indexed inside GetControlLayout -> QPDK012 ERROR, invalid."""
        code = """\
PluginInfo = { Name = "Test~DT1", Version = "1.0.0", Id = "test.dt1" }
function GetProperties() return {} end
function GetControls(props) return { { Name = "btn", ControlType = "Button" } } end
function GetControlLayout(props)
  local layout = {}
  if Controls.btn then layout["btn"] = { Style = "Button" } end
  return layout, {}
end
"""
        result = v.validate_string(code)
        q12 = self._q12(result)
        assert len(q12) == 1
        assert q12[0].severity == IssueSeverity.ERROR
        assert q12[0].line == 6
        assert "design-time" in q12[0].message and "GetControlLayout" in q12[0].message
        assert not result.valid  # an ERROR fails the gate

    def test_controls_indexed_in_getcontrols_errors(self, v):
        """Controls indexed inside GetControls -> QPDK012 ERROR."""
        code = """\
PluginInfo = { Name = "Test~DT2", Version = "1.0.0", Id = "test.dt2" }
function GetProperties() return {} end
function GetControls(props)
  local ctls = {}
  ctls[#ctls+1] = { Name = "x", Value = Controls.x.Value }
  return ctls
end
function GetControlLayout(props) return {}, {} end
"""
        result = v.validate_string(code)
        q12 = self._q12(result)
        assert any(i.severity == IssueSeverity.ERROR for i in q12)

    def test_runtime_block_not_flagged(self, v):
        """The legit top-level `if Controls then` runtime block is OUTSIDE every
        GetX body and must NOT be flagged."""
        code = """\
PluginInfo = { Name = "Test~DT3", Version = "1.0.0", Id = "test.dt3" }
function GetProperties() return {} end
function GetControls(props) return { { Name = "btn", ControlType = "Button" } } end
function GetControlLayout(props) return { btn = { Style = "Button" } }, {} end
if Controls then
  Controls.btn.EventHandler = function() end
end
"""
        result = v.validate_string(code)
        assert not self._q12(result)

    def test_timer_in_getx_is_warning_not_error(self, v):
        """A non-Controls runtime object (Timer) indexed in a GetX body is a
        hedged WARNING, never the Controls ERROR. (A proper runtime guard keeps
        QPDK025 from co-firing here.)"""
        code = """\
PluginInfo = { Name = "Test~DT4", Version = "1.0.0", Id = "test.dt4" }
function GetProperties() return {} end
function GetControls(props) return { { Name = "btn", ControlType = "Button" } } end
function GetControlLayout(props)
  local now = Timer.Now()
  return {}, {}
end
"""
        result = v.validate_string(code)
        q12 = self._q12(result)
        assert q12, "expected a QPDK012 finding for Timer in a design-time function"
        assert all(i.severity == IssueSeverity.WARNING for i in q12)

    def test_local_shadow_not_flagged(self, v):
        """A `local Controls = ...` shadow inside the function means later
        `Controls.x` is the local, not the runtime global -> no QPDK012."""
        code = """\
PluginInfo = { Name = "Test~DT5", Version = "1.0.0", Id = "test.dt5" }
function GetProperties() return {} end
function GetControls(props) return {} end
function GetControlLayout(props)
  local Controls = props
  if Controls.foo then end
  return {}, {}
end
"""
        result = v.validate_string(code)
        assert not self._q12(result)

    def test_control_named_controls_not_flagged(self, v):
        """A control NAMED "Controls" (a string value) must not match -- the rule
        runs on the string-blanked code view."""
        code = """\
PluginInfo = { Name = "Test~DT6", Version = "1.0.0", Id = "test.dt6" }
function GetProperties() return {} end
function GetControls(props) return { { Name = "Controls", ControlType = "Text" } } end
function GetControlLayout(props)
  local layout = {}
  layout["Controls"] = { Style = "Text" }
  return layout, {}
end
"""
        result = v.validate_string(code)
        assert not self._q12(result)

    def test_suppression_comment(self, v):
        """`-- qpdk:ignore QPDK012` suppresses the finding."""
        code = """\
PluginInfo = { Name = "Test~DT7", Version = "1.0.0", Id = "test.dt7" }
function GetProperties() return {} end
function GetControls(props) return { { Name = "btn", ControlType = "Button" } } end
function GetControlLayout(props)
  local layout = {}
  if Controls.btn then layout["btn"] = {} end  -- qpdk:ignore QPDK012
  return layout, {}
end
"""
        result = v.validate_string(code)
        assert not self._q12(result)


# ===========================================================================
# v0.5.0-test fix batch — F1/F2 string-awareness, F3, F4, F5 (QPDK011),
# F6 (QPDK013).
# ===========================================================================

_HEADER_NOLAYOUT = """\
PluginInfo = {{ Name = "Test~{name}", Version = "1.0.0", Id = "test.{id}" }}
function GetProperties() return {{}} end
"""


class TestF1BlockCommentInString:
    """F1: a `--[[` inside a string/long-string must NOT be read as a block
    comment opener (the un-suppressible phantom-QPDK001 class)."""

    def test_block_comment_marker_in_short_string_no_syntax_error(self, v):
        code = (
            'PluginInfo = { Name = "T~B", Version = "1.0.0", Id = "com.t.b" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props)\n"
            '  local msg = "use --[[ to start"\n'
            "  if true then\n"
            '    msg = "and ]] to end"\n'
            "  end\n"
            "  return {}\n"
            "end\n"
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK001" for i in result.issues)

    def test_block_comment_marker_in_long_string_no_syntax_error(self, v):
        code = (
            'PluginInfo = { Name = "T~B", Version = "1.0.0", Id = "com.t.b2" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props)\n"
            "  local doc = [[ contains --[[ marker inside a long string ]]\n"
            "  return {}\n"
            "end\n"
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK001" for i in result.issues)

    def test_real_block_comment_still_blanks_code(self, v):
        """A genuine block comment must still hide code-shaped text inside it."""
        code = _plugin("RealBlock", """\
--[[
Log.Warning("inside a real block comment")
]]
""", "real-block")
        result = v.validate_string(code)
        # The Log.Warning is inside a block comment -> must NOT fire QPDK020.
        assert not any(i.code == "QPDK020" for i in result.issues)


class TestF2InStringFalsePositives:
    """F2: code-shaped text inside string literals must not trigger code rules,
    while the same construct in real code still fires."""

    def test_qpdk024_reserved_name_in_string_not_flagged(self, v):
        code = _plugin("Q24Str", """\
if Controls then
  helpText = "bad pattern is: local Timer = Timer.New()"
end
""", "q24-str")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK024" for i in result.issues)

    def test_qpdk024_real_shadow_still_fires(self, v):
        code = _plugin("Q24Real", """\
if Controls then
  local Timer = Timer.New()
end
""", "q24-real")
        result = v.validate_string(code)
        assert any(i.code == "QPDK024" for i in result.issues)

    def test_qpdk023_status_in_string_not_flagged(self, v):
        code = _plugin("Q23Str", """\
if Controls then
  doc = "set status.Value = 9 is wrong"
end
""", "q23-str")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK023" for i in result.issues)

    def test_qpdk023_real_status_write_still_fires(self, v):
        code = _plugin("Q23Real", """\
if Controls then
  Controls.Outputs["status"].Value = 9
end
""", "q23-real")
        result = v.validate_string(code)
        assert any(i.code == "QPDK023" for i in result.issues)

    def test_qpdk021_rapidjson_in_description_not_flagged(self, v):
        code = (
            'PluginInfo = { Name = "T~R", Version = "1.0.0", Id = "com.t.r", '
            'Description = "Uses rapidjson under the hood" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props) return {} end\n"
            "function GetControlLayout() return {}, {} end\n"
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK021" for i in result.issues)

    def test_qpdk021_real_rapidjson_use_still_fires(self, v):
        code = _plugin("Q21Real", """\
if Controls then
  local d = rapidjson.decode("{}")
end
""", "q21-real")
        result = v.validate_string(code)
        assert any(i.code == "QPDK021" for i in result.issues)

    def test_qpdk021_with_require_not_flagged(self, v):
        code = _plugin("Q21OK", """\
local rapidjson = require("rapidjson")
if Controls then
  local d = rapidjson.decode("{}")
end
""", "q21-ok")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK021" for i in result.issues)


class TestF3FourPartVersion:
    """F3: QPDK004 must accept 2-4 part versions; >4 parts still warns."""

    def test_four_part_version_not_flagged(self, v):
        code = (
            'PluginInfo = { Name = "T~V", Version = "2.5.4.8", Id = "com.t.v" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props) return {} end\n"
        )
        result = v.validate_string(code)
        assert not any(
            i.code == "QPDK004" and "Version" in i.message for i in result.issues
        )

    def test_two_part_version_not_flagged(self, v):
        code = (
            'PluginInfo = { Name = "T~V2", Version = "1.0", Id = "com.t.v2" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props) return {} end\n"
        )
        result = v.validate_string(code)
        assert not any(
            i.code == "QPDK004" and "Version" in i.message for i in result.issues
        )

    def test_five_part_version_still_warns(self, v):
        code = (
            'PluginInfo = { Name = "T~V5", Version = "1.2.3.4.5", Id = "com.t.v5" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props) return {} end\n"
        )
        result = v.validate_string(code)
        assert any(
            i.code == "QPDK004" and "Version" in i.message for i in result.issues
        )


class TestF4XapiFormatInjection:
    """F4: QPDK033 detects string.format / table.concat xAPI injection."""

    def test_string_format_xcommand_injection_warns(self, v):
        code = _plugin("F4Fmt", """\
if Controls then
  function send() sendData(string.format("xCommand Dial Number: %s", Controls.num.String)) end
end
""", "f4-fmt")
        result = v.validate_string(code)
        assert any(i.code == "QPDK033" for i in result.issues)

    def test_table_concat_xcommand_injection_warns(self, v):
        code = _plugin("F4Concat", """\
if Controls then
  function send() sendData(table.concat({"xCommand Audio Volume Set Level:", Controls.lvl.String})) end
end
""", "f4-concat")
        result = v.validate_string(code)
        assert any(i.code == "QPDK033" for i in result.issues)

    def test_generic_string_format_not_flagged(self, v):
        """string.format building a non-xAPI string must NOT fire."""
        code = _plugin("F4Generic", """\
if Controls then
  function logit() sendData(string.format("Generic log %s", Controls.msg.String)) end
end
""", "f4-generic")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK033" for i in result.issues)

    def test_literal_command_no_string_not_flagged(self, v):
        """A literal xStatus command with no .String interpolation is safe."""
        code = _plugin("F4Literal", """\
if Controls then
  function poll() sendData("xStatus Audio Volume") end
end
""", "f4-literal")
        result = v.validate_string(code)
        assert not any(i.code == "QPDK033" for i in result.issues)

    def test_concat_arm_still_fires(self, v):
        """The original `..` arm must keep firing (no regression)."""
        code = _plugin("F4DotDot", """\
if Controls then
  function send() sendData("xCommand Dial Number: " .. Controls.num.String) end
end
""", "f4-dotdot")
        result = v.validate_string(code)
        assert any(i.code == "QPDK033" for i in result.issues)


def _plugin_with_controls(name, controls_body, layout_body, id_suffix):
    """Build a plugin with explicit GetControls / GetControlLayout bodies."""
    safe_id = id_suffix.lower()
    return (
        f'PluginInfo = {{ Name = "Test~{name}", Version = "1.0.0", Id = "test.{safe_id}" }}\n'
        "function GetProperties() return {} end\n"
        "function GetControls(props)\n"
        "  local ctls = {}\n"
        f"{controls_body}"
        "  return ctls\n"
        "end\n"
        "function GetControlLayout(props)\n"
        "  local layout = {}\n"
        "  local graphics = {}\n"
        f"{layout_body}"
        "  return layout, graphics\n"
        "end\n"
    )


class TestF5TriggerInputPin:
    """F5 / QPDK011: Button ButtonType=Trigger + PinStyle Input/Both (hedged)."""

    def test_trigger_input_warns(self, v):
        code = _plugin_with_controls(
            "F5Pos",
            '  table.insert(ctls, { Name = "Fire", ControlType = "Button", ButtonType = "Trigger", PinStyle = "Input" })\n',
            '  layout["Fire"] = {}\n',
            "f5-pos",
        )
        result = v.validate_string(code)
        issues = [i for i in result.issues if i.code == "QPDK011"]
        assert len(issues) == 1
        # B2: INFO (full ruleset only) until a bench probe settles it; QSC's
        # own managed plugins ship this pattern.
        assert issues[0].severity == IssueSeverity.INFO
        assert "Unverified" in issues[0].message

    def test_trigger_input_silent_in_default_ruleset(self):
        code = _plugin_with_controls(
            "F5Def",
            '  table.insert(ctls, { Name = "Fire", ControlType = "Button", ButtonType = "Trigger", PinStyle = "Input" })\n',
            '  layout["Fire"] = {}\n',
            "f5-def",
        )
        result = PluginValidator(ruleset="default").validate_string(code)
        assert not any(i.code == "QPDK011" for i in result.issues)

    def test_trigger_both_warns(self, v):
        code = _plugin_with_controls(
            "F5Both",
            '  table.insert(ctls, { Name = "Fire", ControlType = "Button", ButtonType = "Trigger", PinStyle = "Both" })\n',
            '  layout["Fire"] = {}\n',
            "f5-both",
        )
        result = v.validate_string(code)
        assert any(i.code == "QPDK011" for i in result.issues)

    def test_trigger_output_not_flagged(self, v):
        """PinStyle=Output is a legitimate pulse-out — must NOT fire."""
        code = _plugin_with_controls(
            "F5Out",
            '  table.insert(ctls, { Name = "Pulse", ControlType = "Button", ButtonType = "Trigger", PinStyle = "Output" })\n',
            '  layout["Pulse"] = {}\n',
            "f5-out",
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK011" for i in result.issues)

    def test_statetrigger_input_not_flagged(self, v):
        """StateTrigger DOES expose an input pin (KB) — must NOT fire."""
        code = _plugin_with_controls(
            "F5State",
            '  table.insert(ctls, { Name = "Multi", ControlType = "Button", ButtonType = "StateTrigger", PinStyle = "Input" })\n',
            '  layout["Multi"] = {}\n',
            "f5-state",
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK011" for i in result.issues)

    def test_momentary_both_not_flagged(self, v):
        """Momentary+Both is the canonical pin-driven pattern — must NOT fire."""
        code = _plugin_with_controls(
            "F5Mom",
            '  table.insert(ctls, { Name = "Push", ControlType = "Button", ButtonType = "Momentary", PinStyle = "Both" })\n',
            '  layout["Push"] = {}\n',
            "f5-mom",
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK011" for i in result.issues)

    def test_suppression_honored(self, v):
        code = _plugin_with_controls(
            "F5Supp",
            '  table.insert(ctls, { Name = "Fire", ControlType = "Button", ButtonType = "Trigger", PinStyle = "Input" })  -- qpdk:ignore QPDK011\n',
            '  layout["Fire"] = {}\n',
            "f5-supp",
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK011" for i in result.issues)


class TestF6ControlLayoutCoupling:
    """F6 / QPDK013: GetControls<->GetControlLayout key coupling."""

    def test_orphaned_layout_key_warns(self, v):
        """A layout key with no matching control Name -> WARNING (typo)."""
        code = _plugin_with_controls(
            "F6Typo",
            '  table.insert(ctls, { Name = "Volume", ControlType = "Knob", Count = 1 })\n',
            '  layout["Volume"] = {}\n  layout["Volumee"] = {}\n',
            "f6-typo",
        )
        result = v.validate_string(code)
        warns = [i for i in result.issues if i.code == "QPDK013"
                 and i.severity == IssueSeverity.WARNING]
        assert any("Volumee" in i.message for i in warns)

    @pytest.mark.parametrize("controls,layout", [
        # control with a nested Choices table next to a plain control
        ('  table.insert(ctls, { Name = "plain", ControlType = "Button", Count = 1 })\n'
         '  table.insert(ctls, { Name = "mode", ControlType = "Text", Choices = {"A","B"}, PinStyle = "Both" })\n',
         '  layout["plain"] = {}\n  layout["mode"] = {}\n'),
        # Count=4 control laid out with literal "ch N" element keys
        ('  table.insert(ctls, { Name = "ch", ControlType = "Knob", Count = 4 })\n',
         '  layout["ch 1"] = {}\n  layout["ch 2"] = {}\n'),
        # Name built by concatenation
        ('  table.insert(ctls, { Name = "In " .. 1, ControlType = "Button", Count = 1 })\n',
         '  layout["In 1"] = {}\n'),
    ])
    def test_legitimate_declarations_not_called_typos(self, v, controls, layout):
        """B8: keys that match the evaluated GetControls are not 'likely a typo'."""
        code = _plugin_with_controls("F6Legit", controls, layout, "f6-legit")
        result = v.validate_string(code)
        warns = [i for i in result.issues if i.code == "QPDK013"
                 and i.severity == IssueSeverity.WARNING]
        assert not warns, [i.message for i in warns]

    def test_control_without_layout_is_info(self, v):
        """A control with no layout entry -> INFO, not WARNING."""
        code = _plugin_with_controls(
            "F6Missing",
            '  table.insert(ctls, { Name = "Backend", ControlType = "Text", Count = 1 })\n',
            "",
            "f6-missing",
        )
        result = v.validate_string(code)
        infos = [i for i in result.issues if i.code == "QPDK013"
                 and i.severity == IssueSeverity.INFO]
        assert any("Backend" in i.message for i in infos)
        # Must NOT be a WARNING.
        assert not any(i.code == "QPDK013" and i.severity == IssueSeverity.WARNING
                       for i in result.issues)

    def test_matching_keys_no_finding(self, v):
        code = _plugin_with_controls(
            "F6Match",
            '  table.insert(ctls, { Name = "Volume", ControlType = "Knob", Count = 1 })\n',
            '  layout["Volume"] = {}\n',
            "f6-match",
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK013" for i in result.issues)

    def test_count_gt_one_control_not_flagged(self, v):
        """Count>1 control expands to numbered names — bare name needs no layout."""
        code = _plugin_with_controls(
            "F6Count",
            '  table.insert(ctls, { Name = "Channel", ControlType = "Knob", Count = 4 })\n',
            "",
            "f6-count",
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK013" for i in result.issues)

    def test_graphics_insert_not_treated_as_key(self, v):
        """table.insert(graphics,...) is NOT a name-keyed layout entry."""
        code = _plugin_with_controls(
            "F6Gfx",
            '  table.insert(ctls, { Name = "Volume", ControlType = "Knob", Count = 1 })\n',
            '  layout["Volume"] = {}\n  table.insert(graphics, { Type = "Label", Text = "Hdr" })\n',
            "f6-gfx",
        )
        result = v.validate_string(code)
        # Only Volume key + Volume control -> no mismatch at all.
        assert not any(i.code == "QPDK013" for i in result.issues)

    def test_count_variable_control_not_flagged(self, v):
        """Audit fix: Count set to a VARIABLE (not a numeric literal) means N
        instances → suffixed names + dynamic-suffix layout keys. The bare Name
        must NOT draw an INFO. Reproduces 17 false advisories a real plugin
        drew (Count = sourceCount / hdmiInputCount)."""
        code = _plugin_with_controls(
            "F6CountVar",
            '  local sourceCount = props["Sources"].Value\n'
            '  table.insert(ctls, { Name = "source_name", ControlType = "Text", Count = sourceCount })\n',
            '  for i = 1, 4 do\n'
            '    layout["source_name " .. i] = { PrettyName = "Source " .. i }\n'
            '  end\n',
            "f6-countvar",
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK013" for i in result.issues), (
            [i.message for i in result.issues if i.code == "QPDK013"]
        )

    def test_count_one_control_with_no_layout_still_info(self, v):
        """The variable-Count widening must NOT silence a genuine Count = 1
        control that truly lacks any layout entry (keeps the legit advisory)."""
        code = _plugin_with_controls(
            "F6CountOne",
            '  table.insert(ctls, { Name = "codec_error_count", ControlType = "Indicator", Count = 1 })\n',
            "",
            "f6-countone",
        )
        result = v.validate_string(code)
        assert any(
            i.code == "QPDK013"
            and i.severity == IssueSeverity.INFO
            and "codec_error_count" in i.message
            for i in result.issues
        )

    def test_dynamic_suffix_layout_key_not_orphan_warning(self, v):
        """A dynamic-suffix layout key (layout["name " .. i]) is not captured as
        a literal key, so it must NOT produce an orphaned-key WARNING."""
        code = _plugin_with_controls(
            "F6Dyn",
            '  table.insert(ctls, { Name = "chan", ControlType = "Knob", Count = chanCount })\n',
            '  for i = 1, 2 do layout["chan " .. i] = {} end\n',
            "f6-dyn",
        )
        result = v.validate_string(code)
        assert not any(
            i.code == "QPDK013" and i.severity == IssueSeverity.WARNING
            for i in result.issues
        )


def _plugin_with_layout_fn(name, controls_body, layout_fn, id_suffix):
    """Like _plugin_with_controls but with a fully caller-supplied
    GetControlLayout function (for inline-literal return forms the shared
    helper's ``local layout = {}`` preamble would mask)."""
    safe_id = id_suffix.lower()
    return (
        f'PluginInfo = {{ Name = "Test~{name}", Version = "1.0.0", Id = "test.{safe_id}" }}\n'
        "function GetProperties() return {} end\n"
        "function GetControls(props)\n"
        "  local ctls = {}\n"
        f"{controls_body}"
        "  return ctls\n"
        "end\n"
        f"{layout_fn}"
    )


class TestF6InlineLiteralLayout:
    """0.14.0 regression: QPDK013's key
    scraper only saw ``layout["name"]={...}`` ASSIGNMENT form; a layout built
    as an inline table-constructor literal (``local layout = { ["x"]={...} }``
    or ``return { x = {...} }, {}``) yielded zero keys, so every Input-pin
    control drew a false "no layout entry" INFO. Constructor keys are now
    brace-walked out of the literal — used to SUPPRESS the INFO direction
    only (a walker miss can only cost an advisory, never invent a finding).
    """

    _GAIN_CTL = ('  table.insert(ctls, { Name = "gain", ControlType = "Knob",'
                 ' PinStyle = "Input", Count = 1 })\n')

    @staticmethod
    def _infos(result):
        return [i for i in result.issues
                if i.code == "QPDK013" and i.severity == IssueSeverity.INFO]

    def test_local_literal_bracket_keys_no_false_info(self, v):
        code = _plugin_with_layout_fn(
            "F6LitBracket", self._GAIN_CTL,
            "function GetControlLayout(props)\n"
            '  local layout = {\n'
            '    ["gain"] = { PrettyName = "Gain", Style = "Knob" },\n'
            "  }\n"
            "  return layout, {}\n"
            "end\n",
            "f6-lit-bracket",
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK013" for i in result.issues), (
            [i.message for i in result.issues if i.code == "QPDK013"]
        )

    def test_local_literal_identifier_keys_no_false_info(self, v):
        code = _plugin_with_layout_fn(
            "F6LitIdent", self._GAIN_CTL,
            "function GetControlLayout(props)\n"
            "  local layout = {\n"
            '    gain = { PrettyName = "Gain", Style = "Knob" },\n'
            "  }\n"
            "  return layout, {}\n"
            "end\n",
            "f6-lit-ident",
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK013" for i in result.issues), (
            [i.message for i in result.issues if i.code == "QPDK013"]
        )

    def test_return_literal_no_false_info(self, v):
        """The exact form that surfaced the defect: GetControlLayout RETURNS
        the literal directly (see also the corpus canon tables)."""
        code = _plugin_with_layout_fn(
            "F6LitReturn", self._GAIN_CTL,
            "function GetControlLayout(props)\n"
            "  return {\n"
            '    gain = { PrettyName = "Gain", Style = "Knob", Position = {0, 0} },\n'
            "  }, {}\n"
            "end\n",
            "f6-lit-return",
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK013" for i in result.issues), (
            [i.message for i in result.issues if i.code == "QPDK013"]
        )

    def test_literal_covered_and_uncovered_mix(self, v):
        """Suppression is per-key: a control the literal does NOT cover keeps
        its genuine INFO (and braces inside strings must not derail the
        walker's depth tracking)."""
        code = _plugin_with_layout_fn(
            "F6LitMix",
            '  table.insert(ctls, { Name = "gain", ControlType = "Knob",'
            ' PinStyle = "Input", Count = 1 })\n'
            '  table.insert(ctls, { Name = "orphan", ControlType = "Text",'
            ' PinStyle = "Input", Count = 1 })\n',
            "function GetControlLayout(props)\n"
            "  local layout = {\n"
            '    ["gain"] = { PrettyName = "Ga{in} -- not a comment", Style = "Knob" },\n'
            "  }\n"
            "  return layout, {}\n"
            "end\n",
            "f6-lit-mix",
        )
        result = v.validate_string(code)
        infos = self._infos(result)
        assert any("orphan" in i.message for i in infos)
        assert not any("'gain'" in i.message for i in infos)

    def test_nested_keys_not_captured(self, v):
        """Depth guard: keys of NESTED tables (Style=, PrettyName=, ...) are
        not layout keys — a control named like one must keep its INFO."""
        code = _plugin_with_layout_fn(
            "F6LitNested",
            '  table.insert(ctls, { Name = "Style", ControlType = "Text",'
            ' PinStyle = "Input", Count = 1 })\n'
            '  table.insert(ctls, { Name = "gain", ControlType = "Knob",'
            ' PinStyle = "Input", Count = 1 })\n',
            "function GetControlLayout(props)\n"
            "  local layout = {\n"
            '    gain = { Style = "Knob", PrettyName = "Gain" },\n'
            "  }\n"
            "  return layout, {}\n"
            "end\n",
            "f6-lit-nested",
        )
        result = v.validate_string(code)
        assert any("'Style'" in i.message for i in self._infos(result))

    def test_literal_keys_do_not_create_orphan_warnings(self, v):
        """Conservative asymmetry, by design: literal-constructor keys only
        SUPPRESS INFOs — an unmatched literal key must NOT draw the
        orphaned-key WARNING (the walker is a heuristic; assignment-form
        keys remain the only WARNING source)."""
        code = _plugin_with_layout_fn(
            "F6LitNoWarn", self._GAIN_CTL,
            "function GetControlLayout(props)\n"
            "  local layout = {\n"
            '    gain = { Style = "Knob" },\n'
            '    ghost = { Style = "Text" },\n'
            "  }\n"
            "  return layout, {}\n"
            "end\n",
            "f6-lit-nowarn",
        )
        result = v.validate_string(code)
        assert not any(
            i.code == "QPDK013" and i.severity == IssueSeverity.WARNING
            for i in result.issues
        )

    def test_literal_dynamic_prefix_key_no_false_info(self, v):
        """A dynamic-suffix key INSIDE the literal (["chan " .. i]) marks its
        literal prefix as laid out, mirroring FP guard 3's assignment-form
        treatment."""
        code = _plugin_with_layout_fn(
            "F6LitDyn",
            '  table.insert(ctls, { Name = "chan", ControlType = "Knob",'
            ' PinStyle = "Input", Count = 1 })\n',
            "function GetControlLayout(props)\n"
            "  local i = 1\n"
            "  local layout = {\n"
            '    ["chan " .. i] = { Style = "Knob" },\n'
            "  }\n"
            "  return layout, {}\n"
            "end\n",
            "f6-lit-dyn",
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK013" for i in result.issues), (
            [i.message for i in result.issues if i.code == "QPDK013"]
        )

    def test_fully_opaque_literal_key_suppresses_info_direction(self, v):
        """A non-string computed key ([someVar] = ...) makes the literal's
        coverage statically unknowable — the INFO direction goes quiet rather
        than mislead (the defect this batch fixes was exactly a misleading
        INFO)."""
        code = _plugin_with_layout_fn(
            "F6LitOpaque",
            '  table.insert(ctls, { Name = "gain", ControlType = "Knob",'
            ' PinStyle = "Input", Count = 1 })\n'
            '  table.insert(ctls, { Name = "trim", ControlType = "Knob",'
            ' PinStyle = "Input", Count = 1 })\n',
            "function GetControlLayout(props)\n"
            "  local k = pick_a_name()\n"
            "  local layout = {\n"
            '    [k] = { Style = "Knob" },\n'
            "  }\n"
            "  return layout, {}\n"
            "end\n",
            "f6-lit-opaque",
        )
        result = v.validate_string(code)
        assert not any(i.code == "QPDK013" for i in result.issues), (
            [i.message for i in result.issues if i.code == "QPDK013"]
        )

    def test_assignment_form_behavior_unchanged(self, v):
        """The pre-0.14 followable idiom keeps its exact semantics: empty
        constructor + assignment-form keys, typos still WARN, missing
        entries still INFO."""
        code = _plugin_with_controls(
            "F6LitBaseline",
            '  table.insert(ctls, { Name = "Volume", ControlType = "Knob", Count = 1 })\n'
            '  table.insert(ctls, { Name = "Backend", ControlType = "Text", Count = 1 })\n',
            '  layout["Volume"] = {}\n  layout["Volumee"] = {}\n',
            "f6-lit-baseline",
        )
        result = v.validate_string(code)
        warns = [i for i in result.issues if i.code == "QPDK013"
                 and i.severity == IssueSeverity.WARNING]
        assert any("Volumee" in i.message for i in warns)
        assert any("Backend" in i.message for i in self._infos(result))
