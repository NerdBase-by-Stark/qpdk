"""
QRC Protocol Mock Server

A mock implementation of the Q-SYS QRC (Q-SYS Remote Control) protocol
for integration testing without a real Q-SYS Core.

The QRC protocol is JSON-RPC 2.0 over TCP on port 1710.
"""

from .server import QRCMockServer
from .protocol import QRCRequest, QRCResponse, QRCError

__all__ = [
    "QRCMockServer",
    "QRCRequest",
    "QRCResponse",
    "QRCError",
]
