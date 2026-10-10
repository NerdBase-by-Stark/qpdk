"""
Q-SYS Plugin Development Kit (QPDK)

A toolkit for developing, validating, and testing Q-SYS plugins without Q-SYS Designer.

Components:
- validator: Static analysis and structural validation
- qrc_tester: Mock QRC protocol server for integration testing
- mock_runtime: Lua sandbox simulating Q-SYS APIs
- testing: pytest harness and fixtures built on the mock runtime
- qsc_eval: QSC's official Plugin-Evaluation-Script, run in Lua 5.3
"""

__version__ = "0.16.0"

from .qrc_tester import QRCMockServer
from .validator import PluginValidator, ValidationResult

__all__ = [
    "PluginValidator",
    "ValidationResult",
    "QRCMockServer",
    "__version__",
]
