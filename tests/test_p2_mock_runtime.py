"""Tests for P2 Mock Runtime Enhancements.

Tests cover:
- MR-P1-A: Ssh.New() full implementation
- MR-P1-B: TcpSocket.New() implementation
- MR-P1-C: Network.Interfaces() stub
- MR-P2-A: rapidjson module shim
- MR-P2-B: Control value clamping (min/max)
- MR-P2-C: Reflect global
- MR-P2-D: PinStyle limitation documented (no code test needed)
- P3: Stub APIs (HttpClient, Component, NamedControl, etc.)
"""

import pytest

from qpdk.mock_runtime.controls import MockControl
from qpdk.mock_runtime.runtime import QSYSRuntime
from qpdk.mock_runtime.ssh import MockSsh, SshAPI
from qpdk.mock_runtime.tcp_socket import MockTcpSocket


@pytest.fixture
def runtime():
    """A fresh QSYSRuntime for each test."""
    rt = QSYSRuntime()
    yield rt
    rt.reset()


# ---------------------------------------------------------------------------
# MR-P1-A: Ssh.New() Full Implementation
# ---------------------------------------------------------------------------

class TestMockSsh:
    """Test the Python-side MockSsh object directly."""

    def test_ssh_initial_state(self):
        """SSH object starts disconnected with empty buffers."""
        ssh = MockSsh()
        assert ssh.IsConnected is False
        assert ssh.BufferLength == 0
        assert ssh.Connected is None
        assert ssh.Reconnect is None
        assert ssh.Data is None
        assert ssh.Closed is None
        assert ssh.Error is None
        assert ssh.Timeout is None
        assert ssh.LoginFailed is None

    def test_ssh_connect_stores_pending(self):
        """Connect() stores connection parameters for test harness."""
        ssh = MockSsh()
        ssh.Connect("10.0.0.1", 22, "admin", "password123")
        assert ssh._pending_connect == ("10.0.0.1", 22, "admin", "password123")

    def test_ssh_simulate_connect(self):
        """simulate_connect() sets IsConnected and fires Connected callback."""
        ssh = MockSsh()
        calls = []
        ssh.Connected = lambda s: calls.append("connected")
        ssh.simulate_connect()
        assert ssh.IsConnected is True
        assert calls == ["connected"]

    def test_ssh_simulate_data(self):
        """simulate_data() adds to buffer and fires Data callback."""
        ssh = MockSsh()
        calls = []
        ssh.Data = lambda s: calls.append(s.BufferLength)
        ssh.simulate_data("hello")
        assert ssh.BufferLength == 5
        assert ssh._read_buffer == "hello"
        assert calls == [5]

    def test_ssh_read(self):
        """Read() returns buffer content and decrements BufferLength."""
        ssh = MockSsh()
        ssh.simulate_connect()
        ssh._read_buffer = "hello world"
        ssh.BufferLength = 11
        # Read 5 bytes
        data = ssh.Read(5)
        assert data == "hello"
        assert ssh.BufferLength == 6
        # Read all remaining
        data = ssh.Read()
        assert data == " world"
        assert ssh.BufferLength == 0

    def test_ssh_write_connected(self):
        """Write() appends to buffer when connected."""
        ssh = MockSsh()
        ssh.simulate_connect()
        ssh.Write("command1\r\n")
        ssh.Write("command2\r\n")
        assert ssh.get_written_data() == ["command1\r\n", "command2\r\n"]

    def test_ssh_write_disconnected_raises(self):
        """Write() raises RuntimeError when not connected."""
        ssh = MockSsh()
        with pytest.raises(RuntimeError, match="SSH:Write on disconnected socket"):
            ssh.Write("data")

    def test_ssh_disconnect(self):
        """Disconnect() sets IsConnected to False."""
        ssh = MockSsh()
        ssh.simulate_connect()
        assert ssh.IsConnected is True
        ssh.Disconnect()
        assert ssh.IsConnected is False

    def test_ssh_simulate_closed(self):
        """simulate_closed() disconnects and fires Closed callback."""
        ssh = MockSsh()
        calls = []
        ssh.Closed = lambda s: calls.append("closed")
        ssh.simulate_connect()
        ssh.simulate_closed()
        assert ssh.IsConnected is False
        assert calls == ["closed"]

    def test_ssh_simulate_error(self):
        """simulate_error() disconnects and fires Error callback."""
        ssh = MockSsh()
        calls = []
        ssh.Error = lambda s, msg: calls.append(msg)
        ssh.simulate_connect()
        ssh.simulate_error("connection lost")
        assert ssh.IsConnected is False
        assert calls == ["connection lost"]

    def test_ssh_simulate_timeout(self):
        """simulate_timeout() disconnects and fires Timeout callback."""
        ssh = MockSsh()
        calls = []
        ssh.Timeout = lambda s: calls.append("timeout")
        ssh.simulate_timeout()
        assert ssh.IsConnected is False
        assert calls == ["timeout"]

    def test_ssh_simulate_login_failed(self):
        """simulate_login_failed() disconnects and fires LoginFailed callback."""
        ssh = MockSsh()
        calls = []
        ssh.LoginFailed = lambda s: calls.append("login_failed")
        ssh.simulate_login_failed()
        assert ssh.IsConnected is False
        assert calls == ["login_failed"]

    def test_ssh_simulate_reconnect(self):
        """simulate_reconnect() reconnects and fires Reconnect callback."""
        ssh = MockSsh()
        calls = []
        ssh.Reconnect = lambda s: calls.append("reconnect")
        ssh.simulate_reconnect()
        assert ssh.IsConnected is True
        assert calls == ["reconnect"]

    def test_ssh_clear_write_buffer(self):
        """clear_write_buffer() empties the write buffer."""
        ssh = MockSsh()
        ssh.simulate_connect()
        ssh.Write("data")
        assert len(ssh.get_written_data()) == 1
        ssh.clear_write_buffer()
        assert len(ssh.get_written_data()) == 0


class TestSshAPI:
    """Test the SshAPI factory."""

    def test_ssh_api_new(self):
        """SshAPI.New() creates and tracks instances."""
        api = SshAPI()
        ssh1 = api.New()
        ssh2 = api.New()
        assert len(api.instances) == 2
        assert isinstance(ssh1, MockSsh)
        assert isinstance(ssh2, MockSsh)

    def test_ssh_api_reset(self):
        """SshAPI.reset() clears all instances."""
        api = SshAPI()
        api.New()
        api.New()
        api.reset()
        assert len(api.instances) == 0


class TestSshInLuaContext:
    """Test SSH mock works when called from Lua code."""

    def test_ssh_new_from_lua(self, runtime):
        """Ssh.New() works from Lua context."""
        code = """
PluginInfo = { Name = "Test~SSH", Version = "1.0.0", Id = "test.ssh" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local ssh = Ssh.New()
    ssh.Connected = function(s) end
    ssh.Reconnect = function(s) end
    ssh.Data = function(s) end
    ssh.Closed = function(s) end
    ssh.Error = function(s, msg) end
    ssh.Timeout = function(s) end
    ssh.LoginFailed = function(s) end
end
"""
        runtime.load_string(code)
        assert len(runtime.ssh_api.instances) == 1

    def test_ssh_connect_write_from_lua(self, runtime):
        """SSH Connect and Write work from Lua."""
        code = """
PluginInfo = { Name = "Test~SSHCW", Version = "1.0.0", Id = "test.sshcw" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    ssh_obj = Ssh.New()
    ssh_obj.Connected = function(s)
        s:Write("hello\\r\\n")
    end
    ssh_obj:Connect("10.0.0.1", 22, "admin", "pass")
end
"""
        runtime.load_string(code)
        # Simulate connection from test harness
        ssh = runtime.ssh_api.instances[0]
        ssh.simulate_connect()
        assert ssh.IsConnected is True
        assert ssh.get_written_data() == ["hello\r\n"]

    def test_ssh_all_callbacks_in_lua(self, runtime):
        """All 7 SSH callbacks can be assigned and called from Lua."""
        code = """
PluginInfo = { Name = "Test~SSHCb", Version = "1.0.0", Id = "test.sshcb" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    results = {}
    ssh_obj = Ssh.New()
    ssh_obj.Connected = function(s) results["connected"] = true end
    ssh_obj.Reconnect = function(s) results["reconnect"] = true end
    ssh_obj.Data = function(s) results["data"] = true end
    ssh_obj.Closed = function(s) results["closed"] = true end
    ssh_obj.Error = function(s, msg) results["error"] = true end
    ssh_obj.Timeout = function(s) results["timeout"] = true end
    ssh_obj.LoginFailed = function(s) results["login_failed"] = true end
end
"""
        runtime.load_string(code)
        ssh = runtime.ssh_api.instances[0]
        ssh.simulate_connect()
        ssh.simulate_data("test")
        ssh.simulate_closed()
        ssh.simulate_error("err")
        ssh.simulate_timeout()
        ssh.simulate_login_failed()
        ssh.simulate_reconnect()

        # Verify all callbacks were called via Lua global 'results'
        g = runtime._lua_globals
        results = g.results
        assert results["connected"] is True
        assert results["reconnect"] is True
        assert results["data"] is True
        assert results["closed"] is True
        assert results["error"] is True
        assert results["timeout"] is True
        assert results["login_failed"] is True


# ---------------------------------------------------------------------------
# MR-P1-B: TcpSocket.New() Implementation
# ---------------------------------------------------------------------------

class TestMockTcpSocket:
    """Test the Python-side MockTcpSocket object directly."""

    def test_tcp_initial_state(self):
        """TcpSocket starts disconnected with empty buffers."""
        tcp = MockTcpSocket()
        assert tcp.IsConnected is False
        assert tcp.BufferLength == 0
        assert tcp.Connected is None
        assert tcp.Reconnect is None
        assert tcp.Data is None
        assert tcp.Closed is None
        assert tcp.Error is None
        assert tcp.Timeout is None

    def test_tcp_no_login_failed(self):
        """TcpSocket has no LoginFailed callback (unlike SSH)."""
        tcp = MockTcpSocket()
        assert not hasattr(tcp, "LoginFailed")

    def test_tcp_connect_write(self):
        """TcpSocket Connect and Write work."""
        tcp = MockTcpSocket()
        tcp.simulate_connect()
        tcp.Write("data\r\n")
        assert tcp.get_written_data() == ["data\r\n"]

    def test_tcp_write_disconnected_raises(self):
        """TcpSocket Write() raises when not connected."""
        tcp = MockTcpSocket()
        with pytest.raises(RuntimeError, match="TcpSocket:Write on disconnected socket"):
            tcp.Write("data")

    def test_tcp_all_callbacks(self):
        """All 6 TcpSocket callbacks work."""
        tcp = MockTcpSocket()
        calls = []
        tcp.Connected = lambda s: calls.append("connected")
        tcp.Reconnect = lambda s: calls.append("reconnect")
        tcp.Data = lambda s: calls.append("data")
        tcp.Closed = lambda s: calls.append("closed")
        tcp.Error = lambda s, msg: calls.append(f"error:{msg}")
        tcp.Timeout = lambda s: calls.append("timeout")

        tcp.simulate_connect()
        tcp.simulate_data("test")
        tcp.simulate_closed()
        tcp.simulate_error("fail")
        tcp.simulate_timeout()
        tcp.simulate_reconnect()

        assert "connected" in calls
        assert "data" in calls
        assert "closed" in calls
        assert "error:fail" in calls
        assert "timeout" in calls
        assert "reconnect" in calls


class TestTcpSocketInLuaContext:
    """Test TcpSocket mock works when called from Lua code."""

    def test_tcp_new_from_lua(self, runtime):
        """TcpSocket.New() works from Lua context."""
        code = """
PluginInfo = { Name = "Test~TCP", Version = "1.0.0", Id = "test.tcp" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local tcp = TcpSocket.New()
    tcp.Connected = function(s) end
    tcp.Data = function(s) end
    tcp.Closed = function(s) end
    tcp.Error = function(s, msg) end
    tcp.Timeout = function(s) end
    tcp:Connect("10.0.0.1", 23)
end
"""
        runtime.load_string(code)
        assert len(runtime.tcp_api.instances) == 1

    def test_tcp_connect_write_from_lua(self, runtime):
        """TcpSocket Connect and Write work from Lua."""
        code = """
PluginInfo = { Name = "Test~TCPCW", Version = "1.0.0", Id = "test.tcpcw" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    tcp_obj = TcpSocket.New()
    tcp_obj.Connected = function(s)
        s:Write("hello\\r\\n")
    end
    tcp_obj:Connect("10.0.0.1", 23)
end
"""
        runtime.load_string(code)
        tcp = runtime.tcp_api.instances[0]
        tcp.simulate_connect()
        assert tcp.IsConnected is True
        assert tcp.get_written_data() == ["hello\r\n"]


# ---------------------------------------------------------------------------
# MR-P1-C: Network.Interfaces() Stub
# ---------------------------------------------------------------------------

class TestNetworkInterfaces:
    """Test Network.Interfaces() stub."""

    def test_network_interfaces_from_lua(self, runtime):
        """Network.Interfaces() returns empty table without crashing."""
        code = """
PluginInfo = { Name = "Test~Net", Version = "1.0.0", Id = "test.net" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local interfaces = Network.Interfaces()
end
"""
        runtime.load_string(code)  # Must not raise


# ---------------------------------------------------------------------------
# MR-P2-A: rapidjson Module Shim
# ---------------------------------------------------------------------------

class TestRapidjsonShim:
    """Test rapidjson module shim."""

    def test_rapidjson_require_from_lua(self, runtime):
        """require("rapidjson") returns a table with decode and encode."""
        code = """
PluginInfo = { Name = "Test~RJ", Version = "1.0.0", Id = "test.rj" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local rj = require("rapidjson")
    local decoded = rj.decode('{"key": "value"}')
    local encoded = rj.encode({key = "value"})
end
"""
        runtime.load_string(code)  # Must not raise

    def test_rapidjson_decode_returns_table(self, runtime):
        """rapidjson.decode returns a table (stub returns empty table)."""
        code = """
PluginInfo = { Name = "Test~RJD", Version = "1.0.0", Id = "test.rjd" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local rj = require("rapidjson")
    rj_result = rj.decode('{"a":1}')
end
"""
        runtime.load_string(code)
        g = runtime._lua_globals
        assert g.rj_result is not None

    def test_rapidjson_encode_returns_string(self, runtime):
        """rapidjson.encode returns a string (stub returns '{}')."""
        code = """
PluginInfo = { Name = "Test~RJE", Version = "1.0.0", Id = "test.rje" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local rj = require("rapidjson")
    rj_encoded = rj.encode({})
end
"""
        runtime.load_string(code)
        g = runtime._lua_globals
        assert g.rj_encoded == "{}"


# ---------------------------------------------------------------------------
# MR-P2-B: Control Value Clamping
# ---------------------------------------------------------------------------

class TestControlValueClamping:
    """Test min/max clamping on MockControl."""

    def test_no_clamping_by_default(self):
        """Without min/max, any value is accepted."""
        ctrl = MockControl(name="Test")
        ctrl.Value = 999.0
        assert ctrl.Value == 999.0
        ctrl.Value = -999.0
        assert ctrl.Value == -999.0

    def test_clamp_to_max(self):
        """Value is clamped to max_value when exceeded."""
        ctrl = MockControl(name="Knob", min_value=0.0, max_value=100.0)
        ctrl.Value = 150.0
        assert ctrl.Value == 100.0

    def test_clamp_to_min(self):
        """Value is clamped to min_value when below."""
        ctrl = MockControl(name="Knob", min_value=0.0, max_value=100.0)
        ctrl.Value = -50.0
        assert ctrl.Value == 0.0

    def test_value_within_range(self):
        """Value within range is not clamped."""
        ctrl = MockControl(name="Knob", min_value=0.0, max_value=100.0)
        ctrl.Value = 50.0
        assert ctrl.Value == 50.0

    def test_clamp_at_boundary(self):
        """Value at exact min/max boundary is accepted."""
        ctrl = MockControl(name="Knob", min_value=0.0, max_value=100.0)
        ctrl.Value = 0.0
        assert ctrl.Value == 0.0
        ctrl.Value = 100.0
        assert ctrl.Value == 100.0

    def test_clamp_on_simulate_external_write(self):
        """simulate_external_write also clamps."""
        ctrl = MockControl(name="Knob", min_value=0.0, max_value=100.0)
        calls = []
        ctrl.EventHandler = lambda c: calls.append(c.Value)
        ctrl.simulate_external_write(200.0)
        assert ctrl.Value == 100.0
        assert calls == [100.0]

    def test_min_only_clamping(self):
        """Only min_value set, no max clamping."""
        ctrl = MockControl(name="Test", min_value=10.0)
        ctrl.Value = 5.0
        assert ctrl.Value == 10.0
        ctrl.Value = 999.0
        assert ctrl.Value == 999.0

    def test_max_only_clamping(self):
        """Only max_value set, no min clamping."""
        ctrl = MockControl(name="Test", max_value=100.0)
        ctrl.Value = 200.0
        assert ctrl.Value == 100.0
        ctrl.Value = -999.0
        assert ctrl.Value == -999.0

    def test_to_dict_includes_min_max(self):
        """to_dict includes Min and Max fields."""
        ctrl = MockControl(name="Knob", min_value=0.0, max_value=100.0)
        d = ctrl.to_dict()
        assert d["Min"] == 0.0
        assert d["Max"] == 100.0

    def test_to_dict_none_min_max(self):
        """to_dict includes None for unset Min/Max."""
        ctrl = MockControl(name="Test")
        d = ctrl.to_dict()
        assert d["Min"] is None
        assert d["Max"] is None


# ---------------------------------------------------------------------------
# MR-P2-C: Reflect Global
# ---------------------------------------------------------------------------

class TestReflectGlobal:
    """Test Reflect is explicitly nil in global namespace."""

    def test_reflect_nil_in_lua(self, runtime):
        """Reflect is nil in Lua global namespace."""
        code = """
PluginInfo = { Name = "Test~Ref", Version = "1.0.0", Id = "test.ref" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    reflect_is_nil = (Reflect == nil)
end
"""
        runtime.load_string(code)
        g = runtime._lua_globals
        assert g.reflect_is_nil is True


# ---------------------------------------------------------------------------
# P3: Stub APIs
# ---------------------------------------------------------------------------

class TestStubAPIs:
    """Test P3 stub APIs don't crash when used from Lua."""

    def test_http_client_from_lua(self, runtime):
        """HttpClient.New() and methods work without crashing."""
        code = """
PluginInfo = { Name = "Test~HC", Version = "1.0.0", Id = "test.hc" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local hc = HttpClient.New()
    hc.Download("http://example.com", "/tmp/file")
    hc.Upload("http://example.com", "/tmp/file")
    hc.Request("http://example.com", "GET")
end
"""
        runtime.load_string(code)

    def test_component_new_from_lua(self, runtime):
        """Component.New() returns table without crashing."""
        code = """
PluginInfo = { Name = "Test~Comp", Version = "1.0.0", Id = "test.comp" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local c = Component.New("MyComponent")
end
"""
        runtime.load_string(code)

    def test_named_control_from_lua(self, runtime):
        """NamedControl methods work without crashing."""
        code = """
PluginInfo = { Name = "Test~NC", Version = "1.0.0", Id = "test.nc" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local val = NamedControl.GetValue("MyControl")
    local str = NamedControl.GetString("MyControl")
    NamedControl.SetValue("MyControl", 1.0)
    NamedControl.SetString("MyControl", "hello")
    NamedControl.SetPosition("MyControl", 0.5)
    local pos = NamedControl.GetPosition("MyControl")
end
"""
        runtime.load_string(code)

    def test_uci_from_lua(self, runtime):
        """Uci.OpenPage() works without crashing."""
        code = """
PluginInfo = { Name = "Test~Uci", Version = "1.0.0", Id = "test.uci" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    Uci.OpenPage("MainPage")
end
"""
        runtime.load_string(code)

    def test_snapshot_from_lua(self, runtime):
        """Snapshot.Load() and .Save() work without crashing."""
        code = """
PluginInfo = { Name = "Test~Snap", Version = "1.0.0", Id = "test.snap" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    Snapshot.Load(1, 1)
    Snapshot.Save(1, 1)
end
"""
        runtime.load_string(code)

    def test_mixer_from_lua(self, runtime):
        """Mixer.New() and methods work without crashing."""
        code = """
PluginInfo = { Name = "Test~Mix", Version = "1.0.0", Id = "test.mix" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local m = Mixer.New("MyMixer")
end
"""
        runtime.load_string(code)

    def test_serial_ports_from_lua(self, runtime):
        """SerialPorts is an accessible table."""
        code = """
PluginInfo = { Name = "Test~SP", Version = "1.0.0", Id = "test.sp" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local sp = SerialPorts
end
"""
        runtime.load_string(code)

    def test_gpio_from_lua(self, runtime):
        """Gpio is an accessible table."""
        code = """
PluginInfo = { Name = "Test~GPIO", Version = "1.0.0", Id = "test.gpio" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local g = Gpio
end
"""
        runtime.load_string(code)

    def test_udp_socket_from_lua(self, runtime):
        """UdpSocket.New() and methods work without crashing."""
        code = """
PluginInfo = { Name = "Test~UDP", Version = "1.0.0", Id = "test.udp" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local udp = UdpSocket.New()
end
"""
        runtime.load_string(code)

    def test_audio_from_lua(self, runtime):
        """Audio is an accessible table."""
        code = """
PluginInfo = { Name = "Test~Audio", Version = "1.0.0", Id = "test.audio" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local a = Audio
end
"""
        runtime.load_string(code)


# ---------------------------------------------------------------------------
# Integration: Plugin loading with multiple APIs
# ---------------------------------------------------------------------------

class TestIntegration:
    """Test that a plugin using multiple new APIs loads correctly."""

    def test_plugin_with_ssh_and_tcp(self, runtime):
        """Plugin that creates both SSH and TcpSocket objects loads."""
        code = """
PluginInfo = { Name = "Test~Int", Version = "1.0.0", Id = "test.int" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    local ssh = Ssh.New()
    ssh.Connected = function(s) Log.Message("SSH connected") end
    ssh:Connect("10.0.0.1", 22, "admin", "pass")

    local tcp = TcpSocket.New()
    tcp.Connected = function(s) Log.Message("TCP connected") end
    tcp:Connect("10.0.0.2", 23)

    local interfaces = Network.Interfaces()
    local rj = require("rapidjson")
end
"""
        runtime.load_string(code)
        assert len(runtime.ssh_api.instances) == 1
        assert len(runtime.tcp_api.instances) == 1

    def test_reset_clears_ssh_and_tcp(self, runtime):
        """Runtime reset clears SSH and TcpSocket instances."""
        code = """
PluginInfo = { Name = "Test~Rst", Version = "1.0.0", Id = "test.rst" }
function GetProperties() return {} end
function GetControls() return {} end
if Controls then
    Ssh.New()
    TcpSocket.New()
end
"""
        runtime.load_string(code)
        assert len(runtime.ssh_api.instances) == 1
        assert len(runtime.tcp_api.instances) == 1

        runtime.reset()
        assert len(runtime.ssh_api.instances) == 0
        assert len(runtime.tcp_api.instances) == 0
