"""qsc-eval contract tests: vendored QSC evaluator + reimplemented bash layer.

Assertions on exit codes, finding strings, and JSON shape — never rich text.
"""

import json

import pytest
from click.testing import CliRunner

from qpdk.cli import main as qpdk_main
from qpdk.qsc_eval import run_qsc_eval

# Canon-conformant minimal plugin: full PluginInfo, one property with
# Min/Max, controls with explicit PinStyle/ControlUnit/ButtonType, layout
# entries for every control, layout Styles from the official vocabulary.
_CANON = """
PluginInfo = {
  Name = "Test~QscEvalCanon", Version = "1.0.0",
  Id = "com.test.qsceval.canon", Author = "T", Manufacturer = "T", Description = "d",
}
function GetProperties()
  return { { Name = "Widgets", Type = "integer", Value = 2, Min = 1, Max = 4 } }
end
function GetControls(props)
  return {
    { Name = "Status", ControlType = "Indicator", IndicatorType = "Status",
      PinStyle = "Output", UserPin = true, Count = 1 },
    { Name = "go", ControlType = "Button", ButtonType = "Momentary",
      PinStyle = "Input", UserPin = true, Count = 1 },
  }
end
function GetControlLayout(props)
  return {
    Status = { Style = "Text", Position = {0, 0}, Size = {40, 16}, PrettyName = "Status" },
    go = { Style = "Button", ButtonStyle = "Momentary", Position = {0, 20},
           Size = {40, 16}, PrettyName = "Go" },
  }, {}
end
"""

# Defective plugin: reserved property name, non-canon layout style, a control
# with no PinStyle and no layout entry, BuildVersion set.
_DEFECTIVE = """
PluginInfo = {
  Name = "Test~QscEvalBad", Version = "1.0",
  Id = "com.test.qsceval.bad", Author = "T", BuildVersion = "9.9.9.9",
}
function GetProperties()
  return { { Name = "Show Debug", Type = "boolean", Value = false } }
end
function GetControls(props)
  return {
    { Name = "lamp", ControlType = "Indicator", IndicatorType = "Led",
      PinStyle = "Output", UserPin = true, Count = 1 },
    { Name = "orphan", ControlType = "Text", PinStyle = "Output", UserPin = true, Count = 1 },
    { Name = "pinless", ControlType = "Text", Count = 1 },
  }
end
function GetControlLayout(props)
  return {
    lamp = { Style = "LED", Position = {0, 0}, Size = {16, 16}, PrettyName = "Lamp" },
    pinless = { Style = "Text", Position = {0, 20}, Size = {40, 16} },
  }, {}
end
"""


@pytest.fixture()
def canon_qplug(tmp_path):
    p = tmp_path / "canon.qplug"
    p.write_text(_CANON, encoding="utf-8")
    return str(p)


@pytest.fixture()
def defective_qplug(tmp_path):
    p = tmp_path / "bad.qplug"
    p.write_text(_DEFECTIVE, encoding="utf-8")
    return str(p)


def test_canon_plugin_passes(canon_qplug):
    r = run_qsc_eval(canon_qplug)
    assert r.crash is None
    assert r.errors == []
    assert "PluginInfo.Manufacturer not set" not in r.warnings
    assert r.export["Pages"] == ["main"] or r.export["Pages"]


def test_defective_plugin_findings(defective_qplug):
    r = run_qsc_eval(defective_qplug)
    assert r.crash is None
    joined = "\n".join(r.errors)
    assert "conflicts with reserved properties" in joined          # Show Debug
    assert "Style 'LED' not recognized" in joined                  # canon is Led
    assert "Control without layout definition: orphan" in joined   # bash layer
    assert "Control without PinStyle: pinless" in joined           # bash layer
    assert any("BuildVersion" in n for n in r.notices)
    assert any("Manufacturer" in w for w in r.warnings)
    # notices for explicit PinStyle=None never fire here; pinless is an error
    assert "Status Control not found" in r.notices


def test_exceptions_config_suppresses(defective_qplug, tmp_path):
    cfg = tmp_path / "PluginEvalConfig.json"
    cfg.write_text(json.dumps(
        {"Exceptions": {"PinlessControls": ["pinless"]}}), encoding="utf-8")
    r = run_qsc_eval(defective_qplug, exceptions_path=str(cfg))
    assert "Control without PinStyle: pinless" not in r.errors


def test_prop_override_and_maxprops(canon_qplug):
    r = run_qsc_eval(canon_qplug, prop_overrides={"Widgets": "3"})
    assert r.export["Properties"]["Widgets"]["Value"] == 3
    r2 = run_qsc_eval(canon_qplug, maxprops=True)
    assert r2.export["Properties"]["Widgets"]["Value"] == 4


def test_design_time_crash_exit(tmp_path):
    p = tmp_path / "crash.qplug"
    p.write_text("PluginInfo = { Id='x', Name='x', Author='x', Version='1.0' }\n"
                 "function GetProperties() error('boom') end\n"
                 "function GetControls() return {} end\n"
                 "function GetControlLayout() return {}, {} end\n",
                 encoding="utf-8")
    r = run_qsc_eval(str(p))
    assert r.crash is not None and "boom" in r.crash

    runner = CliRunner()
    res = runner.invoke(qpdk_main, ["qsc-eval", str(p)])
    assert res.exit_code == 2


def test_cli_exit_codes_and_json(canon_qplug, defective_qplug):
    runner = CliRunner()
    ok = runner.invoke(qpdk_main, ["qsc-eval", canon_qplug, "--format", "json"])
    assert ok.exit_code == 0
    doc = json.loads(ok.output)
    assert doc["ok"] is True and doc["results"][0]["errors"] == []

    bad = runner.invoke(qpdk_main, ["qsc-eval", defective_qplug, "--format", "json"])
    assert bad.exit_code == 1
    doc = json.loads(bad.output)
    assert doc["ok"] is False and doc["results"][0]["errors"]

    # --strict flips warnings into failures: canon plugin has none -> still 0
    strict_ok = runner.invoke(qpdk_main, ["qsc-eval", canon_qplug, "--strict"])
    assert strict_ok.exit_code == 0
