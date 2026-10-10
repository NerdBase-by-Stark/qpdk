"""qpdk.testing.arrays — auto-derived array-control maps.

Hand-maintained array-control lists in per-plugin conftests rot; the
plugin's own GetControls Count fields are the truth. derive_array_controls reads
them via design-time evaluation — no hand list, strict raise if the
plugin would crash at Designer drag-in.
"""

from pathlib import Path

import pytest

from qpdk.testing import derive_array_controls

FIXTURES = Path(__file__).parent / "fixtures"
ANI22 = Path(__file__).parents[2] / "qsys-shure-ani22/release/ShureANI22Block-v0.1.0.qplug"
VITEC = Path(__file__).parents[2] / "qsys-vitec-exterity/release/VitecAvediaPlayerM93xx-v0.1.1.qplug"


class TestDerive:
    def test_mixed_counts(self):
        source = (FIXTURES / "arrays_mixed.qplug").read_text(encoding="utf-8")
        assert derive_array_controls(source) == {"arr4": 4, "arr6": 6}

    def test_design_crash_raises_with_phase_prefix(self):
        source = (FIXTURES / "design_crash.qplug").read_text(encoding="utf-8")
        with pytest.raises(RuntimeError) as exc:
            derive_array_controls(source)
        assert "GetControlLayout" in str(exc.value)
        assert "drag-in" in str(exc.value)


class TestCorpusRegression:
    """Derivation must exactly match the real suites' known maps."""

    @pytest.mark.skipif(not ANI22.exists(), reason="optional plugin fixture not on disk")
    def test_ani22_eleven_entry_map(self):
        derived = derive_array_controls(ANI22.read_text(encoding="utf-8"))
        assert derived == {
            "clip": 4, "encryption": 4, "ch_mute": 4, "digital_gain": 4,
            "analog_gain": 2, "analog_gain_up": 2, "analog_gain_down": 2,
            "phantom": 2, "led_in": 2, "led_brightness": 3, "output_level": 6,
        }

    @pytest.mark.skipif(not VITEC.exists(), reason="optional plugin fixture not on disk")
    def test_vitec_empty_map(self):
        assert derive_array_controls(VITEC.read_text(encoding="utf-8")) == {}


class TestProxyRespectsCount:
    """Regression: _PROXY_LUA once took a count arg and ignored it — with
    the mock auto-creating any requested control, every numeric index
    resolved and ipairs(Controls[arr]) never terminated. The proxy must return nil
    outside 1..count and report count via __len.

    Every Lua loop below carries an explicit iteration guard so a
    regression FAILS FAST instead of hanging the suite.
    """

    def _runtime(self):
        from qpdk.testing import PluginHarness
        return PluginHarness(FIXTURES / "arrays_mixed.qplug").make_runtime()

    def test_ipairs_terminates_at_count(self):
        rt = self._runtime()
        n = rt._lua.eval("""
        (function()
            local n = 0
            for i, c in ipairs(Controls.arr4) do
                n = n + 1
                if n > 64 then break end  -- regression guard: fail fast, never hang
            end
            return n
        end)()
        """)
        assert n == 4  # pre-fix: guard trips and n == 65

    def test_out_of_range_indices_return_nil(self):
        rt = self._runtime()
        assert rt._lua.eval("Controls.arr4[5] == nil") is True
        assert rt._lua.eval("Controls.arr4[0] == nil") is True
        assert rt._lua.eval("Controls.arr6[7] == nil") is True

    def test_in_range_boundary_still_resolves(self):
        rt = self._runtime()
        assert rt._lua.eval("Controls.arr4[1] ~= nil") is True
        assert rt._lua.eval("Controls.arr4[4] ~= nil") is True
        rt._lua.execute("Controls.arr4[4].Boolean = true")
        assert rt.controls["arr4 4"].Boolean is True

    def test_len_reports_count(self):
        rt = self._runtime()
        assert rt._lua.eval("#Controls.arr4") == 4
        assert rt._lua.eval("#Controls.arr6") == 6

    def test_non_numeric_key_still_nil(self):
        rt = self._runtime()
        assert rt._lua.eval("Controls.arr4.bogus == nil") is True
