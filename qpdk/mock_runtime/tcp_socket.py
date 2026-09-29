"""
Mock TcpSocket API

Simulates Q-SYS TcpSocket.New() for offline plugin testing.
"""

import re
from typing import Callable, Any

# TcpSocket.EOL keywords — official table, help.qsys.com TcpSocket topic
# (verified 2026-07-09; SerialPorts.EOL uses the same table). Q-SYS's actual
# runtime values are opaque; plugins only pass them through to ReadLine, so
# string tokens are sufficient here.
EOL_KEYWORDS = ("Any", "CrLf", "CrLfStrict", "Lf", "Null", "Custom")

_ANY_RUN = re.compile(r"[\r\n]+")


def read_line(buffer: str, eol: str, custom: str | None = None) -> tuple[str | None, str]:
    """One ReadLine step against a buffer. Returns (line, remaining_buffer).

    line is None when no complete line is buffered ("Nil if the read failed",
    per the official doc — the documented pattern is to loop until nil).
    Official EOL semantics (help.qsys.com TcpSocket EOL table):
      Any        terminator = any run of CR and/or LF characters
      CrLf       optional \\r then \\n (matches "\\r\\n" or "\\n")
      CrLfStrict exactly "\\r\\n"
      Lf         "\\n"
      Null       a single NUL byte
      Custom     the string passed as `custom`
    The terminator is consumed and NOT included in the returned line.
    Any-run tail behavior (run touching end of buffer, more CR/LF possibly in
    flight) is not specified by the doc and unverified on a real Core: this
    implementation consumes the maximal run currently buffered.
    """
    if eol == "Lf":
        i = buffer.find("\n")
        return (None, buffer) if i < 0 else (buffer[:i], buffer[i + 1:])
    if eol == "CrLfStrict":
        i = buffer.find("\r\n")
        return (None, buffer) if i < 0 else (buffer[:i], buffer[i + 2:])
    if eol == "CrLf":
        i = buffer.find("\n")
        if i < 0:
            return None, buffer
        line = buffer[:i]
        if line.endswith("\r"):
            line = line[:-1]
        return line, buffer[i + 1:]
    if eol == "Null":
        i = buffer.find("\x00")
        return (None, buffer) if i < 0 else (buffer[:i], buffer[i + 1:])
    if eol == "Custom":
        if not custom:
            raise ValueError("ReadLine(EOL.Custom) requires a custom terminator string")
        i = buffer.find(custom)
        return (None, buffer) if i < 0 else (buffer[:i], buffer[i + len(custom):])
    if eol == "Any":
        m = _ANY_RUN.search(buffer)
        return (None, buffer) if m is None else (buffer[:m.start()], buffer[m.end():])
    raise ValueError(f"unknown EOL keyword: {eol!r} (expected one of {EOL_KEYWORDS})")


class MockTcpSocket:
    """Mock implementation of a Q-SYS TcpSocket object.

    Similar structure to SSH but with 6 callbacks (all optional per Q-SYS docs):
    Connected, Reconnect, Data, Closed, Error, Timeout.
    Unlike SSH, TcpSocket has no LoginFailed callback.

    Usage (from plugin Lua code):
        local tcp = TcpSocket.New()
        tcp.Connected = function(s) ... end
        tcp:Connect("10.0.0.1", 23)

    Usage (from test harness):
        runtime.tcp_api.instances[0].simulate_connect()
        runtime.tcp_api.instances[0].simulate_data("response\\r\\n")
        written = runtime.tcp_api.instances[0].get_written_data()
    """

    def __init__(self):
        self.IsConnected = False
        self.BufferLength = 0
        # 6 optional lifecycle callbacks -- assigned by plugin, called by test harness
        self.Connected = None
        self.Reconnect = None
        self.Data = None
        self.Closed = None
        self.Error = None
        self.Timeout = None
        self._write_buffer: list[str] = []
        self._read_buffer: str = ""
        self._pending_connect: tuple | None = None
        # TcpSocket-specific properties
        self.ReadTimeout = 0
        self.WriteTimeout = 0
        self.ReconnectTimeout = 0

    def Connect(self, host, port):
        """Initiate a TCP connection.

        Test harness calls simulate_connect() after this to simulate successful connection.
        """
        self._pending_connect = (host, port)

    def Disconnect(self):
        """Disconnect the TCP socket."""
        self.IsConnected = False

    def Write(self, data):
        """Write data to the TCP connection.

        Raises RuntimeError if not connected, matching real Q-SYS behavior.
        """
        if not self.IsConnected:
            raise RuntimeError("TcpSocket:Write on disconnected socket")
        self._write_buffer.append(data)

    def Read(self, n: int = -1) -> str:
        """Read data from the TCP buffer.

        Args:
            n: Number of bytes to read. -1 reads all available data.

        Returns:
            The data read from the buffer.
        """
        if n < 0:
            data = self._read_buffer
            self._read_buffer = ""
        else:
            data = self._read_buffer[:n]
            self._read_buffer = self._read_buffer[n:]
        self.BufferLength = len(self._read_buffer)
        return data

    def ReadLine(self, eol: str, custom: str | None = None) -> str | None:
        """Read one line from the buffer per the official EOL semantics.

        Returns the line WITHOUT its terminator, or None ("nil") when no
        complete line is buffered — the documented pattern is to loop until
        nil. See read_line() for the verified EOL table.
        """
        line, self._read_buffer = read_line(self._read_buffer, eol, custom)
        self.BufferLength = len(self._read_buffer)
        return line

    # -- Test harness methods (called from Python tests, not from Lua) --

    def simulate_connect(self):
        """Called by test harness to trigger Connected callback."""
        self.IsConnected = True
        if self.Connected:
            self.Connected(self)

    def simulate_reconnect(self):
        """Called by test harness to trigger Reconnect callback."""
        self.IsConnected = True
        if self.Reconnect:
            self.Reconnect(self)

    def simulate_data(self, data: str):
        """Called by test harness to inject incoming data."""
        self._read_buffer += data
        self.BufferLength = len(self._read_buffer)
        if self.Data:
            self.Data(self)

    def simulate_closed(self):
        """Called by test harness to trigger Closed callback."""
        self.IsConnected = False
        if self.Closed:
            self.Closed(self)

    def simulate_error(self, message: str = ""):
        """Called by test harness to trigger Error callback."""
        self.IsConnected = False
        if self.Error:
            self.Error(self, message)

    def simulate_timeout(self):
        """Called by test harness to trigger Timeout callback."""
        self.IsConnected = False
        if self.Timeout:
            self.Timeout(self)

    def get_written_data(self) -> list[str]:
        """Get all data written to the TCP connection."""
        return self._write_buffer.copy()

    def clear_write_buffer(self):
        """Clear the write buffer."""
        self._write_buffer.clear()


class TcpSocketAPI:
    """Mock implementation of the Q-SYS TcpSocket global.

    Provides TcpSocket.New() constructor.
    Tracks all created TcpSocket objects for test harness access.
    """

    def __init__(self):
        self._instances: list[MockTcpSocket] = []

    def New(self) -> MockTcpSocket:
        """Create a new TCP socket (standard Q-SYS API)."""
        tcp = MockTcpSocket()
        self._instances.append(tcp)
        return tcp

    @property
    def instances(self) -> list[MockTcpSocket]:
        """Get all created TcpSocket instances (for test harness)."""
        return self._instances

    def reset(self):
        """Reset all TcpSocket instances."""
        self._instances.clear()
