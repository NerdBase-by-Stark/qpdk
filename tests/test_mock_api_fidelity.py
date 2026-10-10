"""Mock runtime API surface checked against the help.qsys.com Lua pages.

Each test names the page whose documented behaviour it pins.
"""

import pytest

from qpdk.mock_runtime.runtime import QSYSRuntime
from qpdk.testing import PluginHarness


@pytest.fixture
def runtime():
    return QSYSRuntime()


def g(rt):
    return rt._lua.globals()


# --- Invented globals (Q-SYS_Extensions_to_Lua.htm lists no Status) -------

class TestNoInventedGlobals:

    def test_status_and_statusstate_are_nil(self, runtime):
        runtime.load_string("S = type(Status)\nSS = type(StatusState)")
        assert g(runtime).S == "nil"
        assert g(runtime).SS == "nil"

    def test_status_call_fails_like_a_core(self, runtime):
        with pytest.raises(RuntimeError, match="Status"):
            runtime.load_string("Status.OK()")


# --- EventHandler argument identity (Timer.htm, Controls_IO.htm) ---------

class TestHandlerReceivesLuaObject:

    def test_timer_htm_identity_example(self, runtime):
        runtime.load_string(
            "timer1 = Timer.New()\n"
            "timer2 = Timer.New()\n"
            "function timerFunc(timer)\n"
            "  if timer == timer1 then print('timer 1!')\n"
            "  elseif timer == timer2 then print('timer 2')\n"
            "  else print('UNKNOWN') end\n"
            "end\n"
            "timer1.EventHandler = timerFunc\n"
            "timer2.EventHandler = timerFunc\n"
            "timer1:Start(1)\n"
            "timer2:Start(2)\n"
        )
        runtime.tick(2.0)
        out = runtime.get_print_output()
        assert "timer 1!" in out
        assert "timer 2" in out
        assert "UNKNOWN" not in out
        assert not runtime.had_errors()

    def test_control_handler_identity_dispatch(self, runtime):
        runtime.load_string(
            "function h(ctl)\n"
            "  if ctl == Controls.a then print('A')\n"
            "  elseif ctl == Controls.b then print('B')\n"
            "  else print('UNKNOWN') end\n"
            "  V = ctl.Value\n"
            "end\n"
            "Controls.a.EventHandler = h\n"
            "Controls.b.EventHandler = h\n"
        )
        runtime.controls["b"].simulate_external_write(3)
        assert runtime.get_print_output() == ["B"]
        assert g(runtime).V == 3.0
        assert not runtime.had_errors()

    def test_single_control_index_is_nil(self, runtime):
        runtime.load_string(
            "Controls.a.EventHandler = function(ctl) I = type(ctl.Index) end"
        )
        runtime.controls["a"].simulate_trigger()
        assert g(runtime).I == "nil"
        assert not runtime.had_errors()

    def test_python_handler_still_gets_python_control(self, runtime):
        runtime.load_string("X = Controls.a.Value")  # wrap the control
        seen = []
        runtime.controls["a"].EventHandler = lambda c: seen.append(c.value)
        runtime.controls["a"].simulate_external_write(5)
        assert seen == [5.0]


ARRAY_PLUGIN = """
PluginInfo = { Name = "Arr", Version = "1.0", Id = "arr.test" }
function GetProperties() return {} end
function GetControls()
  return { { Name = "Btn", ControlType = "Button", ButtonType = "Toggle",
             Count = 3, UserPin = true, PinStyle = "Both" } }
end
function GetControlLayout() return {}, {} end
if Controls then
  for i = 1, #Controls.Btn do
    Controls.Btn[i].EventHandler = function(ctl)
      LAST = ctl.Index
      SAME = (ctl == Controls.Btn[ctl.Index])
    end
  end
end
"""


def test_array_element_index_controls_io_example(tmp_path):
    """Controls_IO.htm Example 1 dispatches on calledControl.Index."""
    plugin = tmp_path / "arr.qplug"
    plugin.write_text(ARRAY_PLUGIN)
    h = PluginHarness(plugin)
    rt = h.make_runtime()
    rt.controls["Btn 2"].simulate_trigger()
    lg = rt._lua.globals()
    assert lg.LAST == 2
    assert lg.SAME is True
    assert not rt.had_errors()


# --- JSON.htm -------------------------------------------------------------

class TestJsonModule:

    def test_require_json_returns_the_module(self, runtime):
        runtime.load_string(
            'local j = require("json")\n'
            "OK = (j == json)\n"
            "S = j.encode({1, 2})\n"
        )
        assert g(runtime).OK is True
        assert g(runtime).S == "[1,2]"

    def test_json_null_encodes_as_null(self, runtime):
        runtime.load_string(
            'require("json")\n'
            "S = json.encode({ user = 'test', password = json.null })\n"
            "SELF = (json.null() == json.null)\n"
        )
        assert '"password":null' in g(runtime).S
        assert '"user":"test"' in g(runtime).S
        assert g(runtime).SELF is True


# --- RapidJSON.htm --------------------------------------------------------

class TestRapidjson:

    def test_null_round_trip(self, runtime):
        runtime.load_string(
            'local rj = require("rapidjson")\n'
            "local t = rj.decode('[null]')\n"
            "IS_NULL = (t[1] == rj.null)\n"
            "S = rj.encode({rj.null})\n"
        )
        assert g(runtime).IS_NULL is True
        assert g(runtime).S == "[null]"

    def test_array_and_object_markers(self, runtime):
        runtime.load_string(
            'local rj = require("rapidjson")\n'
            "A = rj.encode(rj.array())\n"
            "O = rj.encode(rj.object())\n"
            "local t = {}\n"
            "SAME = (rj.array(t) == t)\n"
        )
        assert g(runtime).A == "[]"
        assert g(runtime).O == "{}"
        assert g(runtime).SAME is True

    def test_encode_options_table(self, runtime):
        """Example 2 on RapidJSON.htm: encode(tbl, {pretty=true, sort_keys=true})."""
        runtime.load_string(
            'local rj = require("rapidjson")\n'
            "P = rj.encode({b = 1, a = 2}, { pretty = true, sort_keys = true })\n"
            "C = rj.encode({b = 1, a = 2}, { sort_keys = true })\n"
        )
        assert g(runtime).P == '{\n    "a": 2,\n    "b": 1\n}'
        assert g(runtime).C == '{"a":2,"b":1}'

    def test_decode_error_returns_nil_and_message(self, runtime):
        runtime.load_string(
            'local rj = require("rapidjson")\n'
            "V, E = rj.decode('{bad')\n"
            "T = type(E)\n"
        )
        assert g(runtime).V is None
        assert g(runtime).T == "string"


# --- NamedControl.htm ----------------------------------------------------

def test_named_control_documented_statics(runtime):
    runtime.load_string(
        'NamedControl.SetValue("Gain", 7, 15)\n'
        'V = NamedControl.GetValue("Gain")\n'
        'NamedControl.SetString("Text", "hi")\n'
        'S = NamedControl.GetString("Text")\n'
        'NamedControl.SetPosition("Gain", 0.5)\n'
        'P = NamedControl.GetPosition("Gain")\n'
        'NamedControl.Trigger("Trig")\n'
        "OLD_GET = type(NamedControl.Get)\n"
        "OLD_SET = type(NamedControl.Set)\n"
    )
    lg = g(runtime)
    assert lg.V == 7.0
    assert lg.S == "hi"
    assert lg.P == 0.5
    assert lg.OLD_GET == "nil"
    assert lg.OLD_SET == "nil"


# --- Design.htm -----------------------------------------------------------

def test_design_get_status_and_inventory(runtime):
    runtime.load_string(
        "local s = Design.GetStatus()\n"
        "N, P, R, C = s.DesignName, s.Platform, s.IsRedundant, s.DesignCode\n"
        "INV = type(Design.GetInventory())\n"
        "FIELD = Design.Platform\n"
    )
    lg = g(runtime)
    assert lg.N == "Mock Design"
    assert isinstance(lg.P, str)
    assert lg.R is False
    assert isinstance(lg.C, str)
    assert lg.INV == "table"
    assert lg.FIELD is None


# --- Component.htm --------------------------------------------------------

def test_component_listing_functions_exist(runtime):
    runtime.load_string(
        "A = #Component.GetComponents()\n"
        'B = #Component.GetControls("led")\n'
    )
    assert g(runtime).A == 0
    assert g(runtime).B == 0


# --- TcpSocket.htm :Search ------------------------------------------------

def test_tcp_socket_search(runtime):
    runtime.load_string("sock = TcpSocket.New()")
    tcp = runtime.tcp_api.instances[-1]
    tcp.simulate_connect()
    tcp.simulate_data("abcOKdefOK")
    runtime.load_string(
        "A = sock:Search('OK')\n"
        "B = sock:Search('OK', 5)\n"
        "C = sock:Search('zz')\n"
        "L = sock.BufferLength\n"
    )
    lg = g(runtime)
    assert lg.A == 4
    assert lg.B == 9
    assert lg.C is None
    assert lg.L == 10  # Search does not consume the buffer


# --- Control_Aspects_Metadata.htm / Ssh.htm property writes ---------------

def test_metadata_aspect_writes_persist(runtime):
    runtime.load_string(
        "Controls.x.Choices = {'a', 'b'}\n"
        "Controls.x.IsIndeterminate = true\n"
        "Controls.x.CssClass = 'warn'\n"
        "N = #Controls.x.Choices\n"
        "I = Controls.x.IsIndeterminate\n"
        "C = Controls.x.CssClass\n"
    )
    lg = g(runtime)
    assert lg.N == 2
    assert lg.I is True
    assert lg.C == "warn"
    assert runtime.controls["x"].choices == ["a", "b"]


def test_ssh_is_interactive_persists(runtime):
    runtime.load_string("s = Ssh.New()\ns.IsInteractive = true\nV = s.IsInteractive")
    assert g(runtime).V is True
