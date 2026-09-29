"""qpdk.testing.harness — PluginHarness construction contract (spec §2.2).

Order proven green on both real suites: runtime -> seed properties
(defaults then overrides) -> seed pre-load controls -> inject array
proxies (BEFORE load — arrays_mixed indexes an array control in its main
chunk) -> load_plugin.
"""

from pathlib import Path

import pytest

from qpdk.testing import PluginHarness

FIXTURES = Path(__file__).parent / "fixtures"
MIXED = FIXTURES / "arrays_mixed.qplug"


def _harness(**kw):
    kw.setdefault("property_defaults", {"Poll Rate": 5})
    kw.setdefault("control_seeds", {"core_logging": True})
    return PluginHarness(MIXED, **kw)


class TestMakeRuntime:
    def test_loads_with_array_proxies_before_load(self):
        """arrays_mixed indexes Controls.arr4[1] in its main chunk — this
        only survives if proxies are injected BEFORE load_plugin."""
        rt = _harness().make_runtime()
        assert rt._lua_globals.seen_arr4_slot1 is True

    def test_array_proxy_round_trip(self):
        rt = _harness().make_runtime()
        rt._lua.execute('Controls.arr4[2].Boolean = true')
        assert rt.controls["arr4 2"].Boolean is True

    def test_property_default_visible_from_lua(self):
        rt = _harness().make_runtime()
        assert rt._lua.eval('Properties["Poll Rate"].Value') == 5

    def test_property_override_beats_default(self):
        rt = _harness().make_runtime(**{"Poll Rate": 30})
        assert rt._lua.eval('Properties["Poll Rate"].Value') == 30

    def test_control_seed_visible_at_load(self):
        """Seeds must land BEFORE the plugin chunk runs."""
        harness = PluginHarness(
            MIXED, control_seeds={"core_logging": True})
        rt = harness.make_runtime()
        assert rt.controls["core_logging"].Boolean is True

    def test_empty_control_seeds_skips_seeding(self):
        harness = PluginHarness(MIXED, control_seeds={})
        rt = harness.make_runtime()
        assert rt.controls["core_logging"].Boolean is False

    def test_per_call_control_seeds_override(self):
        harness = _harness()
        rt = harness.make_runtime(control_seeds={"core_logging": False})
        assert rt.controls["core_logging"].Boolean is False

    def test_arrays_cached_single_derivation(self):
        harness = _harness()
        assert harness.arrays == {"arr4": 4, "arr6": 6}
        first = harness.arrays
        harness.make_runtime()
        assert harness.arrays is first

    def test_readline_param_gated_until_g1(self):
        with pytest.raises(NotImplementedError):
            PluginHarness(MIXED, readline=True).make_runtime()
