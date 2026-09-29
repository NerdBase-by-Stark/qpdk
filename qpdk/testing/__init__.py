"""qpdk.testing — pytest harness for Q-SYS plugin test suites.

Replaces the hand-rolled per-plugin conftest shims (array-control lists
that rot, Properties/HttpClient repairs that upstream fixes made
redundant) with one derived-from-the-plugin harness. See
internal notes Part 2.
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
