"""ReadLine + TcpSocket.EOL semantics (G1 verification, 2026-07-09).

Official EOL table (help.qsys.com TcpSocket topic; SerialPorts.EOL is the
same table):
  Any        terminator = any run of CR and/or LF characters
  CrLf       optional \\r then \\n ("\\r\\n" or "\\n")
  CrLfStrict exactly "\\r\\n"
  Lf         "\\n"
  Null       single NUL
  Custom     caller-supplied string
ReadLine returns the line WITHOUT its terminator; nil when no complete line
is buffered ("Nil if the read failed" — documented pattern is loop-until-nil).
"""
import pytest

from qpdk.mock_runtime.tcp_socket import MockTcpSocket, read_line, EOL_KEYWORDS
from qpdk.mock_runtime.ssh import MockSsh


# -- read_line unit semantics -------------------------------------------

def test_lf():
    assert read_line("abc\ndef", "Lf") == ("abc", "def")
    assert read_line("abc", "Lf") == (None, "abc")
    # CR is data under Lf
    assert read_line("abc\r\ndef", "Lf") == ("abc\r", "def")


def test_crlf_matches_crlf_or_bare_lf():
    assert read_line("abc\r\ndef", "CrLf") == ("abc", "def")
    assert read_line("abc\ndef", "CrLf") == ("abc", "def")
    assert read_line("abc\r", "CrLf") == (None, "abc\r")


def test_crlfstrict():
    assert read_line("abc\r\ndef", "CrLfStrict") == ("abc", "def")
    assert read_line("abc\ndef", "CrLfStrict") == (None, "abc\ndef")


def test_null():
    assert read_line("abc\x00def", "Null") == ("abc", "def")
    assert read_line("abc", "Null") == (None, "abc")


def test_custom():
    assert read_line("cmd1!cmd2!", "Custom", "!") == ("cmd1", "cmd2!")
    assert read_line("** end\nOK\n", "Custom", "\n") == ("** end", "OK\n")
    with pytest.raises(ValueError):
        read_line("x", "Custom")  # missing terminator string


def test_any_consumes_whole_run():
    assert read_line("abc\r\r\n\ndef", "Any") == ("abc", "def")
    assert read_line("abc\rdef", "Any") == ("abc", "def")
    assert read_line("abc", "Any") == (None, "abc")


def test_unknown_keyword_raises():
    with pytest.raises(ValueError):
        read_line("x\n", "CRLF")  # wrong case is a plugin bug — surface it


# -- socket-level -------------------------------------------------------

@pytest.mark.parametrize("cls", [MockTcpSocket, MockSsh])
def test_socket_readline_loop_until_nil(cls):
    s = cls()
    s.simulate_data("*s Standby State: Off\r\n** end\r\n\r\nOK\r\npartial")
    lines = []
    while (ln := s.ReadLine("Custom", "\n")) is not None:
        lines.append(ln)
    assert lines == ["*s Standby State: Off\r", "** end\r", "\r", "OK\r"]
    assert s._read_buffer == "partial"
    assert s.BufferLength == len("partial")


# -- Lua-side: the exact cisco-modular call shape -----------------------

def test_lua_ssh_readline_custom():
    from qpdk.mock_runtime.runtime import QSYSRuntime
    rt = QSYSRuntime()
    rt._init_lua()
    rt._lua.execute("""
        ssh = Ssh.New()
        got = {}
        ssh.Data = function()
            local line = ssh:ReadLine(TcpSocket.EOL.Custom, "\\n")
            while line do
                got[#got + 1] = line
                line = ssh:ReadLine(TcpSocket.EOL.Custom, "\\n")
            end
        end
    """)
    sock = rt.ssh_api.instances[0]
    sock.simulate_data("alpha\nbeta\ngamma")  # gamma incomplete
    got = rt._lua.eval("got")
    assert list(got.values()) == ["alpha", "beta"]


def test_lua_tcpsocket_eol_table_complete():
    from qpdk.mock_runtime.runtime import QSYSRuntime
    rt = QSYSRuntime()
    rt._init_lua()
    for kw in EOL_KEYWORDS:
        assert rt._lua.eval(f"TcpSocket.EOL.{kw}") is not None, kw
