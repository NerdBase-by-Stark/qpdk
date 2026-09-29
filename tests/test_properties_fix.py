"""U1 — Properties global must actually work in runtime mode (build spec step 1).

Pre-fix, `_inject_properties_api` assigned the __index metamethod via a lupa
attribute write that never landed (runtime.py:572): `getmetatable(Properties)`
had no __index and EVERY Lua-side read returned nil — even after
`runtime.properties.set(...)`. 11/11 corpus plugins read Properties (122
sites); guarded reads silently fell back to defaults (false-green: seeded
test values ignored), unguarded reads crashed at load (false-red). Field
evidence: vitec's conftest shipped an `install_properties_index()` repair.

G2 decision (missing-key semantics, KB checked 2026-07-06, undocumented):
an UNDECLARED property reads as **nil** from Lua — on hardware, Properties
is a plain Lua table built from the declared GetProperties entries, so a
missing key is nil by standard Lua semantics. This is an INFERENCE, not
hardware-verified; the Python-side MockProperties.__getitem__ keeps its
legacy `{"Value": None}` shape for back-compat.
"""

from qpdk.mock_runtime.runtime import QSYSRuntime


_HEADER = (
    'PluginInfo = { Name = "P~Test", Version = "1.0.0", Id = "com.p.test" }\n'
    "function GetProperties() return {} end\n"
    "function GetControls(props) return {} end\n"
    "function GetControlLayout() return {}, {} end\n"
)


class TestPropertiesRuntimeReads:
    def test_lua_reads_seeded_property(self):
        rt = QSYSRuntime()
        rt.properties.set("IP Address", "192.168.1.50")
        rt.load_string(_HEADER + """
if Controls then end
seen_ip = Properties["IP Address"].Value
""")
        assert rt._lua_globals.seen_ip == "192.168.1.50"

    def test_metatable_index_is_wired(self):
        rt = QSYSRuntime()
        rt.load_string(_HEADER)
        assert rt._lua.eval("getmetatable(Properties).__index ~= nil") is True

    def test_guarded_read_prefers_seeded_value(self):
        """The ANI22-style guard pattern must see the seeded value, not the
        fallback — this was THE false-green mechanism (seeded values ignored,
        fallback silently taken, suite green)."""
        rt = QSYSRuntime()
        rt.properties.set("Poll Rate", 5)
        rt.load_string(_HEADER + """
if Properties["Poll Rate"] then
  rate = Properties["Poll Rate"].Value
else
  rate = 30
end
""")
        assert rt._lua_globals.rate == 5

    def test_undeclared_property_is_nil(self):
        """G2: undeclared -> nil (plain-Lua-table inference, unverified on
        hardware). The guard `if Properties[x] then` must take the fallback."""
        rt = QSYSRuntime()
        rt.load_string(_HEADER + """
undeclared_is_nil = (Properties["Never Declared"] == nil)
if Properties["Never Declared"] then
  fallback = false
else
  fallback = true
end
""")
        assert rt._lua_globals.undeclared_is_nil is True
        assert rt._lua_globals.fallback is True

    def test_python_side_shape_unchanged(self):
        """Back-compat: Python-side __getitem__ keeps the legacy shape."""
        rt = QSYSRuntime()
        assert rt.properties["never set"] == {"Value": None}
