"""qpdk.testing.plugin — pytest fixtures end-to-end via pytester (spec §2.5.3).

Covers the final-form config the migrated-suite proofs never exercised:
a suite that ONLY overrides `qsys_harness` and consumes the plugin's own
`rt` fixture + `qsys_props` marker.
"""

pytest_plugins = ["pytester"]

_PLUGIN_SRC = '''
PluginInfo = { Name = "PT~Fix", Version = "1.0.0", Id = "com.test.pt-fix" }
function GetProperties() return {} end
function GetControls(props)
  return { { Name = "led", ControlType = "Button", ButtonType = "Toggle" } }
end
function GetControlLayout(props) return {}, {} end
if Controls then
  rate = Properties["Poll Rate"] and Properties["Poll Rate"].Value or -1
end
'''

_CONFTEST = '''
import pytest
from qpdk.testing import PluginHarness

@pytest.fixture(scope="session")
def qsys_harness(tmp_path_factory):
    plugin = tmp_path_factory.mktemp("plug") / "fixture.qplug"
    plugin.write_text({src!r}, encoding="utf-8")
    return PluginHarness(plugin, property_defaults={{"Poll Rate": 7}})
'''


# The inner pytest run has its own rootdir with no ini file, so it never sees
# the project's pytest settings; set the pytest-asyncio option it warns about.
_INNER_INI = "[pytest]\nasyncio_default_fixture_loop_scope = function\n"


def _write_suite(pytester, test_body):
    pytester.makeini(_INNER_INI)
    pytester.makeconftest(_CONFTEST.format(src=_PLUGIN_SRC))
    pytester.makepyfile(test_body)


class TestFixtures:
    def test_rt_fixture_works_end_to_end(self, pytester):
        _write_suite(pytester, """
def test_rt(rt):
    assert rt._lua_globals.rate == 7
""")
        result = pytester.runpytest("-q", "-p", "no:cacheprovider")
        result.assert_outcomes(passed=1)

    def test_qsys_props_marker_overrides_property(self, pytester):
        _write_suite(pytester, """
import pytest

@pytest.mark.qsys_props(**{"Poll Rate": 42})
def test_marker(rt):
    assert rt._lua_globals.rate == 42
""")
        result = pytester.runpytest("-q", "-p", "no:cacheprovider")
        result.assert_outcomes(passed=1)

    def test_missing_harness_override_is_actionable(self, pytester):
        pytester.makeini(_INNER_INI)
        pytester.makepyfile("""
def test_needs_harness(rt):
    pass
""")
        result = pytester.runpytest("-q", "-p", "no:cacheprovider")
        result.assert_outcomes(errors=1)
        result.stdout.fnmatch_lines(["*override the 'qsys_harness' fixture*"])
        # Points at the README walkthrough, not at a source file.
        result.stdout.fnmatch_lines(["*'Writing a test suite' in the QPDK README*"])
