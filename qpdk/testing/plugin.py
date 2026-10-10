"""pytest plugin: fixtures for Q-SYS plugin test suites.

Loaded machine-wide via the pytest11 entry point, so it must stay
import-cheap and inert: nothing here does work unless a test actually
requests these fixtures.

A suite opts in by overriding `qsys_harness` in its conftest:

    import pytest
    from qpdk.testing import PluginHarness

    @pytest.fixture(scope="session")
    def qsys_harness():
        return PluginHarness("release/MyPlugin-v1.0.0.qplug",
                             property_defaults={"Initial State": False})

Naming note: `rt` is a generic fixture name. If your project defines its own
`rt` fixture, yours wins under pytest's override rules and this one is
silently unused.
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "qsys_props(**overrides): per-test Q-SYS property overrides applied "
        "by the `rt` fixture (declarative form of "
        "harness.make_runtime(Prop=value)).",
    )
    config.addinivalue_line(
        "markers",
        "qsys_expect_errors: this test deliberately makes the plugin raise "
        "(for example a callback crash); the `rt` fixture will not fail it "
        "for recorded runtime errors.",
    )


@pytest.fixture(scope="session")
def qsys_harness():
    """MUST be overridden by the plugin's own conftest."""
    pytest.fail(
        "qpdk.testing: override the 'qsys_harness' fixture in your conftest.py "
        "to return a PluginHarness for your plugin. See 'Writing a test suite' "
        "in the QPDK README."
    )


@pytest.fixture(scope="session")
def qsys_arrays(qsys_harness):
    """The auto-derived array-control map (for structural assertions)."""
    return dict(qsys_harness.arrays)


@pytest.fixture()
def rt(request, qsys_harness):
    """Fresh runtime per test; applies @pytest.mark.qsys_props overrides.

    On teardown, any error the plugin raised inside a callback (timer, socket,
    EventHandler) fails the test. On a Core those crashes are real faults, and
    the mock only records them, so without this check a test whose own
    assertions pass would hide them. Mark deliberate crash tests with
    @pytest.mark.qsys_expect_errors.
    """
    overrides = {}
    marker = request.node.get_closest_marker("qsys_props")
    if marker is not None:
        overrides.update(marker.kwargs)
    runtime = qsys_harness.make_runtime(**overrides)
    yield runtime
    if request.node.get_closest_marker("qsys_expect_errors") is None and runtime.had_errors():
        pytest.fail(
            "plugin raised runtime error(s) during the test (mark the test "
            "@pytest.mark.qsys_expect_errors if intended):\n  "
            + "\n  ".join(str(e) for e in runtime.error_messages),
            pytrace=False,
        )
