"""Design-time execution mode (D6, Fable review 2026-07-06).

Q-SYS Designer evaluates a plugin file at DESIGN time — when the plugin is
dragged into a schematic — with `Controls` nil, then calls the definition
functions (GetProperties/GetControls/GetControlLayout/...). Three real
crashes escaped the mock into Designer because the mock always injected
Controls, so `if Controls then` was always true and the design-time-nil
crash class was unreproducible (reproduced Designer crash: cisco-modular
"GetControlLayout indexed nil Controls", commit ce220a6 in that repo).

`QSYSRuntime(design_time=True)` leaves Controls nil — the one VERIFIED
design-time difference — and `run_design_time()` executes the chunk plus
the definition functions, collecting errors instead of raising.
"""

from qpdk.mock_runtime.runtime import QSYSRuntime


_HEADER = (
    'PluginInfo = { Name = "DT~Test", Version = "1.0.0", Id = "com.dt.test" }\n'
)

_DEFS = """
function GetProperties()
  return { { Name = "Channels", Type = "integer", Value = 2, Min = 1, Max = 8 } }
end
function GetControls(props)
  return { { Name = "gain", ControlType = "Knob", ControlUnit = "dB" } }
end
function GetControlLayout(props)
  return { gain = { PrettyName = "Gain" } }, {}
end
"""


class TestDesignTimeCrashDetection:
    def test_toplevel_controls_index_is_caught(self):
        """Unguarded top-level Controls indexing = the drag-in crash."""
        code = _HEADER + _DEFS + "Controls.gain.Value = 0\n"
        runtime = QSYSRuntime(design_time=True)
        report = runtime.run_design_time(code)
        assert report.errors, "top-level nil-Controls index must be reported"
        assert any("Controls" in e or "nil" in e for e in report.errors)

    def test_getcontrollayout_controls_index_is_caught(self):
        """The cisco ce220a6 class: a GetX body indexing Controls."""
        code = _HEADER + """
function GetProperties() return {} end
function GetControls(props) return {} end
function GetControlLayout(props)
  local v = Controls.gain.Value
  return {}, {}
end
"""
        runtime = QSYSRuntime(design_time=True)
        report = runtime.run_design_time(code)
        assert any("GetControlLayout" in e for e in report.errors), (
            f"GetControlLayout crash must be attributed, got: {report.errors}")

    def test_guarded_plugin_is_clean(self):
        """The standard guarded plugin must produce zero design-time errors,
        and its runtime block must NOT execute (Controls is nil)."""
        code = _HEADER + _DEFS + """
ran_runtime = false
if Controls then
  ran_runtime = true
  Controls.gain.Value = 0
end
"""
        runtime = QSYSRuntime(design_time=True)
        report = runtime.run_design_time(code)
        assert report.errors == []
        assert runtime._lua_globals.ran_runtime is False

    def test_syntax_error_reported_not_raised(self):
        runtime = QSYSRuntime(design_time=True)
        report = runtime.run_design_time(_HEADER + "function broken(\n")
        assert report.errors
        assert any("chunk" in e for e in report.errors)


class TestDesignTimeDefinitionCalls:
    def test_props_built_from_getproperties_defaults(self):
        """GetControls must receive a props table keyed by property Name with
        .Value populated from the declared default."""
        code = _HEADER + """
seen_channels = nil
function GetProperties()
  return { { Name = "Channels", Type = "integer", Value = 4 } }
end
function GetControls(props)
  seen_channels = props["Channels"].Value
  return {}
end
function GetControlLayout(props) return {}, {} end
"""
        runtime = QSYSRuntime(design_time=True)
        report = runtime.run_design_time(code)
        assert report.errors == []
        assert runtime._lua_globals.seen_channels == 4

    def test_controls_result_captured(self):
        runtime = QSYSRuntime(design_time=True)
        report = runtime.run_design_time(_HEADER + _DEFS)
        assert report.errors == []
        assert report.controls is not None
        assert report.controls[1]["Name"] == "gain"

    def test_paged_plugin_gets_designer_page_index(self):
        """Paged plugins read props['page_index'].Value — Designer supplies
        that property (the production cisco-modular plugin depends on it), so
        the harness must synthesize it when GetPages exists."""
        code = _HEADER + """
function GetProperties() return {} end
function GetPages(props)
  return { { name = "Main" }, { name = "Setup" } }
end
function GetControls(props) return {} end
function GetControlLayout(props)
  local page = props["page_index"].Value
  return { marker = { PrettyName = "page " .. page } }, {}
end
"""
        runtime = QSYSRuntime(design_time=True)
        report = runtime.run_design_time(code)
        assert report.errors == []
        assert report.layout["marker"]["PrettyName"] == "page 1"


class TestDesignTimeExtensions:
    """Build spec step 3: graphics capture, page iteration, plugin_show_debug."""

    def test_graphics_captured_from_second_return(self):
        code = _HEADER + """
function GetProperties() return {} end
function GetControls(props) return {} end
function GetControlLayout(props)
  return { gain = { PrettyName = "Gain" } },
         { { Type = "GroupBox", Text = "Main" } }
end
"""
        report = QSYSRuntime(design_time=True).run_design_time(code)
        assert report.errors == []
        assert report.graphics is not None
        assert report.graphics[1]["Type"] == "GroupBox"

    def test_single_return_leaves_graphics_none(self):
        code = _HEADER + """
function GetProperties() return {} end
function GetControls(props) return {} end
function GetControlLayout(props)
  return { gain = { PrettyName = "Gain" } }
end
"""
        report = QSYSRuntime(design_time=True).run_design_time(code)
        assert report.errors == []
        assert report.graphics is None

    def test_all_pages_iterated(self):
        """A paged plugin must have EVERY page evaluated — a crash or layout
        that only exists on page 2+ was previously invisible (only page 1 ran)."""
        code = _HEADER + """
function GetProperties() return {} end
function GetPages(props)
  return { { name = "A" }, { name = "B" }, { name = "C" } }
end
function GetControls(props) return {} end
function GetControlLayout(props)
  local p = props["page_index"].Value
  return { ["marker_" .. p] = { PrettyName = "on page " .. p } }, {}
end
"""
        report = QSYSRuntime(design_time=True).run_design_time(code)
        assert report.errors == []
        assert len(report.pages) == 3
        for i, page in enumerate(report.pages, start=1):
            assert page["index"] == i
            assert page["layout"][f"marker_{i}"] is not None
        # Back-compat: report.layout is still page 1's layout.
        assert report.layout["marker_1"] is not None

    def test_page_crash_reported_with_page_tag_others_still_run(self):
        code = _HEADER + """
function GetProperties() return {} end
function GetPages(props)
  return { { name = "A" }, { name = "B" } }
end
function GetControls(props) return {} end
function GetControlLayout(props)
  if props["page_index"].Value == 2 then
    local boom = NoSuchGlobal.field
  end
  return { ok = { PrettyName = "fine" } }, {}
end
"""
        report = QSYSRuntime(design_time=True).run_design_time(code)
        assert any("page 2" in e for e in report.errors), report.errors
        assert len(report.pages) == 2
        assert report.pages[0]["layout"] is not None
        assert report.pages[1]["layout"] is None

    def test_plugin_show_debug_synthesized_for_rectify(self):
        """QSC store plugins (ClockAudio, Gude) read
        props["plugin_show_debug"] in RectifyProperties — a property they
        never declare. Best explanation is Designer injects it (inference,
        same class as page_index); synthesize {Value=false} when undeclared."""
        code = _HEADER + """
function GetProperties() return {} end
function RectifyProperties(props)
  saw_debug = (props["plugin_show_debug"].Value == false)
  return props
end
function GetControls(props) return {} end
function GetControlLayout(props) return {}, {} end
"""
        rt = QSYSRuntime(design_time=True)
        report = rt.run_design_time(code)
        assert report.errors == []
        assert rt._lua_globals.saw_debug is True

    def test_declared_plugin_show_debug_not_overridden(self):
        code = _HEADER + """
function GetProperties()
  return { { Name = "plugin_show_debug", Type = "boolean", Value = true } }
end
function RectifyProperties(props)
  saw_debug = props["plugin_show_debug"].Value
  return props
end
function GetControls(props) return {} end
function GetControlLayout(props) return {}, {} end
"""
        rt = QSYSRuntime(design_time=True)
        report = rt.run_design_time(code)
        assert report.errors == []
        assert rt._lua_globals.saw_debug is True


class TestRuntimeModeUnchanged:
    def test_default_runtime_still_injects_controls(self):
        code = _HEADER + """
ran_runtime = false
if Controls then
  ran_runtime = true
end
"""
        runtime = QSYSRuntime()
        runtime.load_string(code)
        assert runtime._lua_globals.ran_runtime is True
