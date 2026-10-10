"""
Mock SSH API

Simulates Q-SYS Ssh.New() for offline plugin testing.
"""

from typing import Callable, Any

from .tcp_socket import read_line


class MockSsh:
    """Mock implementation of a Q-SYS SSH object.

    Constructor: Ssh.New() returns an SSH object
    Properties: IsConnected, BufferLength
    Methods: Connect(host, port, user, pass), Disconnect(), Write(data), Read(n)
    Event callbacks (assigned by plugin, called by test harness):
        Connected, Reconnect, Data, Closed, Error, Timeout, LoginFailed
        (Ssh.htm lists all seven; none is documented as mandatory)

    Usage (from plugin Lua code):
        local ssh = Ssh.New()
        ssh.Connected = function(s) ... end
        ssh:Connect("10.0.0.1", 22, "admin", "pass")

    Usage (from test harness):
        runtime.ssh_api.instances[0].simulate_connect()
        runtime.ssh_api.instances[0].simulate_data("response\\r\\n")
        written = runtime.ssh_api.instances[0].get_written_data()
    """

    def __init__(self):
        self.IsConnected = False
        self.BufferLength = 0
        # Ssh.htm .IsInteractive: set true when the device needs interactive mode.
        self.IsInteractive = False
        # Seven event callbacks (Ssh.htm), assigned by plugin, called by test harness
        self.Connected = None
        self.Reconnect = None
        self.Data = None       # fires when data arrives (optional if using Read() polling)
        self.Closed = None
        self.Error = None
        self.Timeout = None
        self.LoginFailed = None
        self._write_buffer: list[str] = []
        self._read_buffer: str = ""
        self._pending_connect: tuple | None = None

    def Connect(self, host, port, user=None, password=None):
        """Initiate an SSH connection.

        Test harness calls simulate_connect() after this to simulate successful connection.
        """
        self._pending_connect = (host, port, user, password)

    def Disconnect(self):
        """Disconnect the SSH session."""
        self.IsConnected = False

    def Write(self, data):
        """Write data to the SSH connection.

        Raises RuntimeError if not connected, matching real Q-SYS behavior.
        """
        if not self.IsConnected:
            raise RuntimeError("SSH:Write on disconnected socket")
        self._write_buffer.append(data)

    def Read(self, n: int = -1) -> str:
        """Read data from the SSH buffer.

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
        complete line is buffered. Same semantics as MockTcpSocket.ReadLine
        (SSH sockets expose the same read API; cisco-modular calls
        SSH:ReadLine(TcpSocket.EOL.Custom, "\\n")).
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

    def simulate_login_failed(self):
        """Called by test harness to trigger LoginFailed callback."""
        self.IsConnected = False
        if self.LoginFailed:
            self.LoginFailed(self)

    def get_written_data(self) -> list[str]:
        """Get all data written to the SSH connection."""
        return self._write_buffer.copy()

    def clear_write_buffer(self):
        """Clear the write buffer."""
        self._write_buffer.clear()


class SshAPI:
    """Mock implementation of the Q-SYS Ssh global.

    Provides Ssh.New() constructor.
    Tracks all created SSH objects for test harness access.
    """

    def __init__(self):
        self._instances: list[MockSsh] = []

    def New(self) -> MockSsh:
        """Create a new SSH object (standard Q-SYS API)."""
        ssh = MockSsh()
        self._instances.append(ssh)
        return ssh

    @property
    def instances(self) -> list[MockSsh]:
        """Get all created SSH instances (for test harness)."""
        return self._instances

    def reset(self):
        """Reset all SSH instances."""
        self._instances.clear()
