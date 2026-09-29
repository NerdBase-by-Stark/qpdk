"""UI-LINT QPDK040-046 — geometry lint over EVALUATED layouts (spec Part 1).

Every threshold is corpus-mined (build spec §1.3-1.4), and the acceptance
bar is the findings matrix in §1.5: zero findings across the professional
QSC plugins and all six production plugins; exactly 5 QPDK042 findings on
the known-buggy MXA920's page 1 (right edges 430 vs a 420px background).
Rules with no corpus true-positive are locked by mutation tests instead.
"""

from pathlib import Path

import pytest

from qpdk.mock_runtime.runtime import QSYSRuntime
from qpdk.validator.ui_lint import UI_LINT_VOCABULARY, lint_report

REPO = Path(__file__).parents[2]

CORPUS_CLEAN = [
    REPO / "qsc-managed-plugins/ClockAudio_CDT100.3.7.0.0/content/clockaudiocdt100.qplug",
    REPO / "qsc-managed-plugins/GudeExpert.1.1.0.0/content/gude_expert.qplug",
    REPO / "cisco-modular-plugin/release/CiscoRoomOS-Enhanced-v2.5.5.2.qplug",
    REPO / "qsys-shure-ani22/release/ShureANI22Block-v0.1.0.qplug",
    REPO / "qsys-vitec-exterity/release/VitecAvediaPlayerM93xx-v0.1.1.qplug",
    REPO / "self-healing/plugin/self-healing-manager-v1.4.0.1.qplug",
    REPO / "qsys-status-gate/plugin/status-gate-v1.3.0.0.qplug",
    REPO / "qsys-mute-state-controller/plugin/room-state-controller-v1.4.0.qplug",
]
MXA920 = REPO / "shure-plugins/release/ShureMXACeilingArray-v1.0.0.qplug"

_HEADER = (
    'PluginInfo = { Name = "UL~T", Version = "1.0.0", Id = "com.ul.t" }\n'
    "function GetProperties() return {} end\n"
    "function GetControls(props) return {} end\n"
)


def _lint(source: str):
    report = QSYSRuntime(design_time=True).run_design_time(source)
    assert report.errors == [], report.errors
    return lint_report(report)


def _codes(findings):
    return [f["code"] for f in findings]


class TestCorpusRegression:
    """§1.5 findings matrix: the corpus is the FP contract."""

    @pytest.mark.parametrize("plugin", CORPUS_CLEAN, ids=lambda p: p.stem)
    def test_zero_findings_on_working_plugins(self, plugin):
        if not plugin.exists():
            pytest.skip(f"{plugin} not on disk")
        findings = _lint(plugin.read_text(encoding="utf-8"))
        assert findings == [], (
            f"{plugin.name}: UI-LINT must be silent on working plugins, got "
            f"{[(f['code'], f['name'], f.get('page')) for f in findings]}")

    def test_mxa920_page1_exactly_five_qpdk042(self):
        """True-positive lock: the known-buggy MXA920, with a test-local
        PageNames repair (its real crash is design-check's finding, not
        UI-LINT's), yields EXACTLY the 5 confirmed right-edge overflows."""
        if not MXA920.exists():
            pytest.skip("MXA920 not on disk")
        source = MXA920.read_text(encoding="utf-8")
        # Repair the missing global so GetControlLayout evaluates (the miners'
        # shim): pages list verbatim from its GetPages for the default model.
        repair = ('PageNames = { "Setup", "Lights", "Presets", "Channels", '
                  '"IntelliMix", "Coverage Areas" }\n')
        findings = _lint(repair + source)
        q42 = [f for f in findings if f["code"] == "QPDK042" and f["page"] == 1]
        assert sorted(f["name"] for f in q42) == sorted([
            "audio_network_subnet", "encryption_status", "serial_number",
            "device_id", "control_network_mac",
        ]), q42


class TestMutations:
    """Rules with no corpus true-positive must fire on injected defects."""

    def _layout_plugin(self, layout_body, graphics_body='{ Type = "GroupBox", Position = { 0, 0 }, Size = { 400, 300 } }'):
        return _HEADER + f"""
function GetControlLayout(props)
  local layout = {{
{layout_body}
  }}
  local graphics = {{ {graphics_body} }}
  return layout, graphics
end
"""

    def test_qpdk040_overlap(self):
        code = self._layout_plugin("""
    a = { Style = "Button", Position = { 10, 10 }, Size = { 50, 20 } },
    b = { Style = "Button", Position = { 30, 15 }, Size = { 50, 20 } },
""")
        assert "QPDK040" in _codes(_lint(code))

    def test_qpdk041_zero_size(self):
        code = self._layout_plugin("""
    a = { Style = "Button", Position = { 10, 10 }, Size = { 0, 20 } },
""")
        assert "QPDK041" in _codes(_lint(code))

    def test_qpdk042_overflow_beyond_tolerance(self):
        # background 400 wide; right edge 410 = 10px overflow > 6px tolerance
        code = self._layout_plugin("""
    a = { Style = "Button", Position = { 280, 10 }, Size = { 130, 24 } },
""")
        assert "QPDK042" in _codes(_lint(code))

    def test_qpdk042_within_tolerance_silent(self):
        # right edge 406 = 6px overflow == tolerance -> silent (vitec ships 6)
        code = self._layout_plugin("""
    a = { Style = "Button", Position = { 276, 10 }, Size = { 130, 24 } },
""")
        assert "QPDK042" not in _codes(_lint(code))

    def test_qpdk043_axis_straddle(self):
        code = self._layout_plugin("""
    a = { Style = "Button", Position = { -10, 10 }, Size = { 50, 20 } },
""")
        assert "QPDK043" in _codes(_lint(code))

    def test_qpdk044_fractional_coordinate(self):
        code = self._layout_plugin("""
    a = { Style = "Button", Position = { 10.5, 10 }, Size = { 50, 20 } },
""")
        assert "QPDK044" in _codes(_lint(code))

    def test_qpdk045_style_typo(self):
        code = self._layout_plugin("""
    a = { Style = "Buton", Position = { 10, 10 }, Size = { 50, 20 } },
""")
        f = [x for x in _lint(code) if x["code"] == "QPDK045"]
        assert f and "corpus-verified vocabulary" in f[0]["message"]

    def test_qpdk046_sliver(self):
        code = self._layout_plugin("""
    a = { Style = "Text", Position = { 10, 10 }, Size = { 50, 13 } },
""")
        assert "QPDK046" in _codes(_lint(code))


class TestExemptions:
    def test_parked_rects_exempt(self):
        """The off-canvas 1x1 parking idiom (self_healing, 26 controls) must
        produce no QPDK040/042/043/046 findings."""
        code = TestMutations()._layout_plugin("""
    p1 = { Style = "Text", Position = { -50, -50 }, Size = { 1, 1 } },
    p2 = { Style = "Text", Position = { -50, -50 }, Size = { 1, 1 } },
""")
        assert _lint(code) == []

    def test_none_style_in_vocabulary(self):
        """Defense-in-depth (§1.4): 'None' MUST be in the vocabulary — with
        the positionless exemption bypassed, the shipped pin-only idiom
        (1,159 production entries) must still yield zero QPDK045 findings."""
        assert "None" in UI_LINT_VOCABULARY

    def test_positionless_entries_skipped(self):
        code = TestMutations()._layout_plugin("""
    pin_only = { Style = "None", PrettyName = "backend~pin" },
""")
        assert _lint(code) == []

    def test_no_style_key_exempt_from_045(self):
        code = TestMutations()._layout_plugin("""
    a = { Position = { 10, 10 }, Size = { 50, 20 } },
""")
        assert "QPDK045" not in _codes(_lint(code))

    def test_page_without_background_skips_042(self):
        code = TestMutations()._layout_plugin("""
    a = { Style = "Button", Position = { 800, 10 }, Size = { 130, 24 } },
""", graphics_body="")
        assert "QPDK042" not in _codes(_lint(code))


class TestPageCrashSkip:
    def test_crashed_page_skipped_others_linted(self):
        code = _HEADER + """
function GetPages(props)
  return { { name = "A" }, { name = "B" } }
end
function GetControlLayout(props)
  if props["page_index"].Value == 2 then
    local boom = NoSuchGlobal.x
  end
  return {
    a = { Style = "Button", Position = { 10, 10 }, Size = { 50, 20 } },
    b = { Style = "Button", Position = { 30, 15 }, Size = { 50, 20 } },
  }, { { Type = "GroupBox", Position = { 0, 0 }, Size = { 400, 300 } } }
end
"""
        report = QSYSRuntime(design_time=True).run_design_time(code)
        assert any("page 2" in e for e in report.errors)
        findings = lint_report(report)
        # page 1 still linted (overlap found); page 2 produced nothing
        assert any(f["code"] == "QPDK040" and f["page"] == 1 for f in findings)
        assert not any(f.get("page") == 2 for f in findings)


class TestRegistration:
    def test_rule_metadata_registered(self):
        from qpdk.validator.rulesets import RULE_METADATA
        from qpdk.validator.result import IssueSeverity
        for code, sev in [("QPDK040", IssueSeverity.WARNING),
                          ("QPDK041", IssueSeverity.WARNING),
                          ("QPDK042", IssueSeverity.WARNING),
                          ("QPDK043", IssueSeverity.INFO),
                          ("QPDK044", IssueSeverity.INFO),
                          ("QPDK045", IssueSeverity.INFO),
                          ("QPDK046", IssueSeverity.INFO)]:
            assert code in RULE_METADATA, code
            assert RULE_METADATA[code]["severity"] == sev, code
