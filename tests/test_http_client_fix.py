"""HttpClient must expose the real statics API (HttpClient.htm).

The real HttpClient API is STATIC functions taking a request table —
Download/Upload/Get/Post/Put/Patch/Delete plus the pure helpers
CreateUrl/EncodeParams/EncodeString/DecodeString, with EventHandler
signature (table, code, data, error, headers). Source: help.qsys.com
HttpClient.htm.
The doc lists NO `.New` constructor.

Pre-fix the mock exposed ONLY `.New` — every real-world call
(`HttpClient.Get{...}`, GudeExpert's `HttpClient.Download{...}`) was a
nil-call crash. Verb statics now capture into rt.http_requests so tests
drive the response via the captured EventHandler; `.New` is retained
for back-compat only.
"""

from qpdk.mock_runtime.runtime import QSYSRuntime

_HEADER = (
    'PluginInfo = { Name = "H~Test", Version = "1.0.0", Id = "com.h.test" }\n'
    "function GetProperties() return {} end\n"
    "function GetControls(props) return {} end\n"
    "function GetControlLayout() return {}, {} end\n"
)


class TestHttpVerbCapture:
    def test_get_is_captured_and_event_handler_drivable(self):
        rt = QSYSRuntime()
        rt.load_string(_HEADER + """
HttpClient.Get {
  Url = "http://device/api/status",
  Headers = { ["Accept"] = "application/json" },
  Timeout = 10,
  EventHandler = function(tbl, code, data, err, headers)
    got_url, got_code, got_data, got_err = tbl.Url, code, data, err
  end,
}
""")
        assert len(rt.http_requests) == 1
        verb, tbl = rt.http_requests[0]
        assert verb == "GET"
        assert tbl["Url"] == "http://device/api/status"
        assert tbl["Timeout"] == 10
        # Harness drives the response through the captured handler.
        tbl["EventHandler"](tbl, 200, '{"power":"on"}', None, rt._lua.table())
        g = rt._lua_globals
        assert g.got_url == "http://device/api/status"
        assert g.got_code == 200
        assert g.got_data == '{"power":"on"}'
        assert g.got_err is None

    def test_post_and_download_verbs(self):
        rt = QSYSRuntime()
        rt.load_string(_HEADER + """
HttpClient.Post { Url = "http://device/api/set", Data = "x=1" }
HttpClient.Download { Url = "http://device/fw.bin" }
""")
        verbs = [v for v, _ in rt.http_requests]
        assert verbs == ["POST", "DOWNLOAD"]

    def test_new_retained_for_back_compat(self):
        rt = QSYSRuntime()
        rt.load_string(_HEADER + "has_new = (HttpClient.New ~= nil)\n")
        assert rt._lua_globals.has_new is True

    def test_reset_clears_capture(self):
        rt = QSYSRuntime()
        rt.load_string(_HEADER + 'HttpClient.Get { Url = "http://x" }\n')
        assert rt.http_requests
        rt.reset()
        assert rt.http_requests == []


class TestHttpPureHelpers:
    """Exact expected strings come from the official doc's own examples."""

    def _rt(self):
        rt = QSYSRuntime()
        rt.load_string(_HEADER)
        return rt

    def test_encode_string_doc_example(self):
        rt = self._rt()
        out = rt._lua.eval('HttpClient.EncodeString("this is | some test")')
        assert out == "this%20is%20%7c%20some%20test"

    def test_decode_string_round_trip(self):
        rt = self._rt()
        out = rt._lua.eval(
            'HttpClient.DecodeString("this%20is%20%7c%20some%20test")')
        assert out == "this is | some test"

    def test_encode_params_single_pair(self):
        rt = self._rt()
        out = rt._lua.eval(
            'HttpClient.EncodeParams({ term = "hello | there" })')
        assert out == "term=hello%20%7c%20there"

    def test_create_url_host_port_path_query(self):
        rt = self._rt()
        out = rt._lua.eval(
            'HttpClient.CreateUrl({ Host = "http://www.go.com", Port = 1234, '
            'Path = "this/is/a/path" })')
        assert out == "http://www.go.com:1234/this/is/a/path"
        out = rt._lua.eval(
            'HttpClient.CreateUrl({ Host = "http://www.go.com", '
            'Path = "this/is/a/path with space", Query = { sky = "blue" } })')
        assert out == "http://www.go.com/this/is/a/path%20with%20space?sky=blue"
