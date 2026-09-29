"""
Q-SYS Plugin Development Kit (QPDK)

A toolkit for developing, validating, and testing Q-SYS plugins without Q-SYS Designer.

Components:
- validator: Static analysis and structural validation
- qrc_tester: Mock QRC protocol server for integration testing
- mock_runtime: Lua sandbox simulating Q-SYS APIs
"""

__version__ = "0.15.0"

from .validator import PluginValidator, ValidationResult
from .qrc_tester import QRCMockServer

__all__ = [
    "PluginValidator",
    "ValidationResult",
    "QRCMockServer",
    "__version__",
]
