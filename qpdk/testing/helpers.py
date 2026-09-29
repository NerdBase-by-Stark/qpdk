"""Small helpers extracted from the proven suite conftests.

Only shapes with observed real-suite usage are included (spec §2.2):
protocol drivers (login sequences, snapshot replies) are plugin-specific
and belong in each suite's own conftest.
"""

from qpdk.mock_runtime.runtime import QSYSRuntime


def single_tcp(rt: QSYSRuntime):
    """The plugin's sole TCP socket; asserts exactly one exists."""
    assert len(rt.tcp_api.instances) == 1, (
        f"expected exactly 1 TcpSocket, got {len(rt.tcp_api.instances)}")
    return rt.tcp_api.instances[0]


def call_global(rt: QSYSRuntime, name: str, *args):
    """Call a plugin top-level global function directly.

    Replaces the fragile repr-into-`lua.execute` idiom: arguments cross the
    lupa boundary as real values, so bytes/quotes/newlines cannot corrupt a
    generated code string.
    """
    fn = rt._lua_globals[name]
    if fn is None:
        raise AttributeError(f"plugin has no global function {name!r}")
    return fn(*args)


def log_messages(rt: QSYSRuntime) -> list[str]:
    """Just the text of every captured Log message."""
    return [m["text"] for m in rt.log.messages]
