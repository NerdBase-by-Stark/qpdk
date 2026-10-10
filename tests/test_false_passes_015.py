"""0.15 false-pass regressions (an internal review).

Each class reproduces one case where qpdk 0.14 gave a PASS to a plugin (or a
run) that should have failed. A false pass is worse than a false fail because
every pipeline gate trusts qpdk's verdict. Every test here was red on 0.14.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

from qpdk import PluginValidator
from qpdk.testing import PluginHarness

pytest_plugins = ["pytester"]

_HEADER = '''
PluginInfo = { Name = "FP~Fixture", Version = "1.0.0", Id = "com.test.fp-fixture" }
function GetProperties() return {} end
function GetControls(props)
  return {
    { Name = "status", ControlType = "Indicator", IndicatorType = "Status", Count = 1 },
    { Name = "gain", ControlType = "Knob", ControlUnit = "dB", Min = -100, Max = 10, Count = 4 },
  }
end
function GetControlLayout(props)
  local layout = {}
  layout["status"] = { PinStyle = "Output", Style = "Indicator", Position = {10, 10}, Size = {100, 16} }
  for i = 1, 4 do
    layout["gain " .. i] = { PinStyle = "Input", Style = "Fader", Position = {10 + 40 * i, 40}, Size = {36, 100} }
  end
  return layout, {}
end
'''


def _plugin(runtime_body: str) -> str:
    return _HEADER + "if Controls then\n" + runtime_body + "\nend\n"


def _codes(result, severity="errors"):
    return [i.code for i in getattr(result, severity)]


def _write(tmp_path, runtime_body, name="p.qplug"):
    p = tmp_path / name
    p.write_text(_plugin(runtime_body), encoding="utf-8")
    return p


# --- 1. Undeclared control references ----------------------------------------

class TestUndeclaredControl:
    BODY = 'Controls["stauts"].Value = 1'

    def test_validator_flags_undeclared_control(self):
        r = PluginValidator().validate_string(_plugin(self.BODY))
        assert "QPDK049" in _codes(r)
        assert not r.valid

    def test_declared_controls_are_not_flagged(self):
        body = 'Controls["status"].Value = 1\nControls.gain[2].Value = 0'
        r = PluginValidator().validate_string(_plugin(body))
        assert "QPDK049" not in _codes(r) + _codes(r, "warnings")

    def test_harness_runtime_rejects_undeclared_control(self, tmp_path):
        h = PluginHarness(_write(tmp_path, self.BODY))
        with pytest.raises(Exception, match="nil"):
            h.make_runtime()

    def test_harness_runtime_allows_declared_controls(self, tmp_path):
        h = PluginHarness(_write(tmp_path, 'Controls["status"].Value = 2\nControls.gain[3].Value = -6'))
        rt = h.make_runtime()
        assert rt.controls["status"].Value == 2


# --- 2. Count=1 control used as an array --------------------------------------

class TestScalarUsedAsArray:
    @pytest.mark.parametrize("body", [
        'local n = #Controls["status"]',
        'local n = #Controls.status',
        'local c = Controls.status[1]',
        'for _, c in ipairs(Controls.status) do end',
    ])
    def test_validator_flags_scalar_array_use(self, body):
        r = PluginValidator().validate_string(_plugin(body))
        assert "QPDK050" in _codes(r)

    def test_real_arrays_are_not_flagged(self):
        body = 'local n = #Controls.gain\nfor _, c in ipairs(Controls.gain) do end'
        r = PluginValidator().validate_string(_plugin(body))
        assert "QPDK050" not in _codes(r) + _codes(r, "warnings")

    def test_mock_length_of_scalar_control_raises(self, tmp_path):
        h = PluginHarness(_write(tmp_path, 'local n = #Controls["status"]'))
        with pytest.raises(Exception, match="length of a userdata"):
            h.make_runtime()


# --- 3. Timer API surface (KB: Sys/Timer.htm) ---------------------------------

class TestTimerSurface:
    def test_validator_flags_timer_create(self):
        r = PluginValidator().validate_string(_plugin("t = Timer.Create()"))
        assert "QPDK020" in _codes(r)

    def test_mock_timer_create_does_not_exist(self, tmp_path):
        h = PluginHarness(_write(tmp_path, "t = Timer.Create()"))
        with pytest.raises(Exception):
            h.make_runtime()

    def test_mock_timer_now_exists(self, tmp_path):
        h = PluginHarness(_write(tmp_path, "t0 = Timer.Now()"))
        rt = h.make_runtime()
        assert isinstance(rt._lua.globals().t0, (int, float))


# --- 4. Suppression directive inside a string --------------------------------

class TestSuppressionOnlyInComments:
    def test_directive_in_string_does_not_suppress_syntax_error(self):
        src = _plugin("") + 'local note = "-- qpdk:ignore QPDK001 LUACHECK-E011"; local x = )\n'
        r = PluginValidator().validate_string(src)
        assert not r.valid
        assert "QPDK001" in _codes(r)

    def test_syntax_error_cannot_be_suppressed_by_real_comment(self):
        src = _plugin("") + "local x = ) -- qpdk:ignore QPDK001\n"
        r = PluginValidator().validate_string(src)
        assert "QPDK001" in _codes(r)

    def test_real_comment_still_suppresses_ordinary_rule(self):
        src = _plugin('Log.Warning("x") -- qpdk:ignore QPDK020')
        r = PluginValidator().validate_string(src)
        assert "QPDK020" not in _codes(r)


# --- 5. Callback crash must fail the pytest fixture ---------------------------

_CRASH_PLUGIN = _plugin('Timer.CallAfter(function() error("AUDIT_CALLBACK_CRASH") end, 1)')


class TestFixtureFailsOnRecordedErrors:
    def _suite(self, pytester, body):
        # The inner run has no ini file of its own; set the pytest-asyncio
        # option so it does not warn (and fail under -W error).
        pytester.makeini("[pytest]\nasyncio_default_fixture_loop_scope = function\n")
        pytester.makeconftest(
            "import pytest\nfrom qpdk.testing import PluginHarness\n"
            "@pytest.fixture(scope='session')\n"
            "def qsys_harness(tmp_path_factory):\n"
            "    p = tmp_path_factory.mktemp('plug') / 'x.qplug'\n"
            f"    p.write_text({_CRASH_PLUGIN!r}, encoding='utf-8')\n"
            "    return PluginHarness(p)\n")
        pytester.makepyfile(body)

    def test_unexpected_callback_crash_fails_test(self, pytester):
        self._suite(pytester, "def test_it(rt):\n    rt.tick(2)\n    assert rt.current_time >= 1\n")
        res = pytester.runpytest("-q", "-p", "no:cacheprovider")
        res.assert_outcomes(passed=1, errors=1)
        res.stdout.fnmatch_lines(["*AUDIT_CALLBACK_CRASH*"])

    def test_marker_allows_expected_crash(self, pytester):
        self._suite(pytester,
                    "import pytest\n@pytest.mark.qsys_expect_errors\n"
                    "def test_it(rt):\n    rt.tick(2)\n    assert rt.had_errors()\n")
        res = pytester.runpytest("-q", "-p", "no:cacheprovider")
        res.assert_outcomes(passed=1)


# --- 6. luacheck fatal failure must not pass ----------------------------------

class TestLuacheckFatal:
    def test_fatal_luacheck_exit_is_an_error(self, tmp_path, monkeypatch):
        real_run = subprocess.run

        def fake_run(cmd, *a, **kw):
            if cmd and cmd[0] == "luacheck":
                return subprocess.CompletedProcess(cmd, 4, stdout="",
                                                   stderr="Error: unknown option")
            return real_run(cmd, *a, **kw)

        monkeypatch.setattr(subprocess, "run", fake_run)
        p = tmp_path / "ok.qplug"
        p.write_text(_plugin('Controls["status"].Value = 0'), encoding="utf-8")
        r = PluginValidator().validate_file(p)
        assert not r.valid
        assert any("luacheck" in i.message.lower() for i in r.errors)


# --- Refinements found by the 286-plugin corpus run ---------------------------

_VARIANT_PLUGIN = '''
PluginInfo = { Name = "FP~Variant", Version = "1.0.0", Id = "com.test.fp-variant" }
function GetProperties()
  return {
    { Name = "Widgets", Type = "boolean", Value = false },
    { Name = "Outputs", Type = "integer", Min = 1, Max = 8, Value = 1 },
  }
end
function GetControls(props)
  local c = { { Name = "status", ControlType = "Indicator", IndicatorType = "Status", Count = 1 },
              { Name = "out", ControlType = "Button", ButtonType = "Toggle", Count = props["Outputs"].Value } }
  if props["Widgets"].Value then
    table.insert(c, { Name = "widget_summary", ControlType = "Text", Count = 1 })
  end
  return c
end
function GetControlLayout(props) return {}, {} end
if Controls then
%s
end
'''


class TestCorpusRefinements:
    def test_name_declared_only_under_a_property_is_not_flagged(self):
        src = _VARIANT_PLUGIN % 'Controls.widget_summary.String = "x"'
        r = PluginValidator().validate_string(src)
        assert "QPDK049" not in _codes(r)

    def test_nil_checked_name_is_not_flagged(self):
        src = _VARIANT_PLUGIN % 'if Controls.maybe_there then Controls.maybe_there.Value = 1 end'
        r = PluginValidator().validate_string(src)
        assert "QPDK049" not in _codes(r)

    def test_unchecked_unknown_name_is_still_flagged(self):
        src = _VARIANT_PLUGIN % 'Controls.never_declared.Value = 1'
        r = PluginValidator().validate_string(src)
        assert "QPDK049" in _codes(r)

    def test_count_raised_by_property_is_a_warning_not_error(self):
        src = _VARIANT_PLUGIN % 'for i = 1, #Controls.out do end'
        r = PluginValidator().validate_string(src)
        assert "QPDK050" not in _codes(r)
        assert "QPDK050" in _codes(r, "warnings")

    def test_always_single_control_is_an_error(self):
        src = _VARIANT_PLUGIN % 'local n = #Controls.status'
        r = PluginValidator().validate_string(src)
        assert "QPDK050" in _codes(r)

    def test_length_of_control_text_is_not_flagged(self):
        src = _VARIANT_PLUGIN % 'if #Controls.status.String > 0 then end'
        r = PluginValidator().validate_string(src)
        assert "QPDK050" not in _codes(r) + _codes(r, "warnings")


# --- 8. Harness property overrides must change the control set (Astra #2) ------

_CHANNELS = '''
PluginInfo = { Name = "FP~Channels", Version = "1.0.0", Id = "com.test.fp-channels" }
function GetProperties()
  return { { Name = "Channels", Type = "integer", Min = 1, Max = 16, Value = 4 } }
end
function GetControls(props)
  return { { Name = "channel", ControlType = "Knob", ControlUnit = "dB", Min = -100, Max = 10,
             Count = props["Channels"].Value } }
end
function GetControlLayout(props) return {}, {} end
if Controls then
  observed = #Controls.channel
end
'''


class TestHarnessConfiguration:
    def _h(self, tmp_path, **kw):
        p = tmp_path / "ch.qplug"
        p.write_text(_CHANNELS, encoding="utf-8")
        return PluginHarness(p, **kw)

    def test_property_defaults_drive_control_count(self, tmp_path):
        rt = self._h(tmp_path, property_defaults={"Channels": 2}).make_runtime()
        assert rt._lua.globals().observed == 2

    def test_per_test_override_drives_control_count(self, tmp_path):
        rt = self._h(tmp_path).make_runtime(Channels=8)
        assert rt._lua.globals().observed == 8

    def test_out_of_range_channel_is_nil_at_smaller_config(self, tmp_path):
        rt = self._h(tmp_path).make_runtime(Channels=2)
        assert rt._lua.eval('Controls.channel[3]') is None


# --- Sol review of 0.15 (cross-family) ----------------------------------------

class TestSolReviewFindings:
    def test_out_of_range_flat_element_is_flagged(self):
        r = PluginValidator().validate_string(_plugin('Controls["gain 999"].Value = 2'))
        assert "QPDK049" in _codes(r)

    def test_in_range_flat_element_is_not_flagged(self):
        r = PluginValidator().validate_string(_plugin('Controls["gain 4"].Value = 2'))
        assert "QPDK049" not in _codes(r)

    def test_unguarded_access_elsewhere_is_still_flagged(self):
        body = 'if Controls.typo then print("ok") end\nControls.typo.Value = 1'
        r = PluginValidator().validate_string(_plugin(body))
        assert [i.line for i in r.errors if i.code == "QPDK049"]  # the second line

    def test_access_inside_guard_block_is_not_flagged(self):
        body = 'if Controls.maybe then\n  local x = 1\n  Controls.maybe.Value = x\nend'
        r = PluginValidator().validate_string(_plugin(body))
        assert "QPDK049" not in _codes(r)

    def test_inline_and_guard_is_not_flagged(self):
        body = 'local v = Controls.maybe and Controls.maybe.Value'
        r = PluginValidator().validate_string(_plugin(body))
        assert "QPDK049" not in _codes(r)
