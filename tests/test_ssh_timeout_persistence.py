"""Step 2b — SSH timeout property writes must persist (mock-coverage P1-4b).

The cleanest false-green in the coverage audit: cisco (the 8k-line
flagship) assigns SSH.ReadTimeout/WriteTimeout/ReconnectTimeout
(cisco:5211-5213) and the ssh wrapper's newindex silently DROPPED all
three — config assignments vanished, everything stayed green. TcpSocket's
wrapper already persisted them; this mirrors it.
"""

from qpdk.mock_runtime.runtime import QSYSRuntime

_HEADER = (
    'PluginInfo = { Name = "SS~T", Version = "1.0.0", Id = "com.ss.t" }\n'
    "function GetProperties() return {} end\n"
    "function GetControls(props) return {} end\n"
    "function GetControlLayout() return {}, {} end\n"
)


class TestSshTimeoutPersistence:
    def test_timeout_writes_persist_and_read_back(self):
        rt = QSYSRuntime()
        rt.load_string(_HEADER + """
if Controls then
  SSH = Ssh.New()
  SSH.ReadTimeout = 7
  SSH.WriteTimeout = 9
  SSH.ReconnectTimeout = 11
  rb_read, rb_write, rb_reconn =
    SSH.ReadTimeout, SSH.WriteTimeout, SSH.ReconnectTimeout
end
""")
        g = rt._lua_globals
        assert g.rb_read == 7
        assert g.rb_write == 9
        assert g.rb_reconn == 11
        # Python-side object carries them too
        ssh = rt.ssh_api.instances[0]
        assert (ssh.ReadTimeout, ssh.WriteTimeout, ssh.ReconnectTimeout) == (7, 9, 11)

    def test_callbacks_still_dispatch(self):
        rt = QSYSRuntime()
        rt.load_string(_HEADER + """
if Controls then
  SSH = Ssh.New()
  SSH.ReadTimeout = 7
  SSH.Connected = function(s) got_connected = true end
end
""")
        rt.ssh_api.instances[0].simulate_connect()
        assert rt._lua_globals.got_connected is True
