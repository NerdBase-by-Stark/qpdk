"""qpdk.testing: pytest harness for Q-SYS plugin test suites.

`PluginHarness` builds the control set from the plugin's own GetControls,
so tests do not need a hand-maintained list of controls or arrays. See
"Writing a test suite" in the QPDK README.
"""

from .arrays import derive_array_controls, inject_array_proxies
from .harness import PluginHarness
from .helpers import call_global, log_messages, single_tcp

__all__ = [
    "PluginHarness",
    "derive_array_controls",
    "inject_array_proxies",
    "single_tcp",
    "call_global",
    "log_messages",
]
