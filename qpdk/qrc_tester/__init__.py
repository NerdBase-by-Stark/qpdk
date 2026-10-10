"""
QRC Protocol Mock Server

A mock implementation of the Q-SYS QRC (Q-SYS Remote Control) protocol
for integration testing without a real Q-SYS Core.

The QRC protocol is JSON-RPC 2.0 over TCP on port 1710.
"""

from .protocol import QRCError, QRCRequest, QRCResponse
from .server import QRCMockServer

__all__ = [
    "QRCMockServer",
    "QRCRequest",
    "QRCResponse",
    "QRCError",
]
