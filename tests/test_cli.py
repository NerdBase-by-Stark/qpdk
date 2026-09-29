"""CLI contract tests (F8).

Guards the CI-load-bearing exit-code (0/1/2), single-vs-multi-file JSON shape,
and TAP shape for BOTH `qpdk validate` and the standalone `qpdk-validate`
entry point. Assertions are on exit_code and parsed JSON keys, never rich text.
"""

import json

import pytest
from click.testing import CliRunner

from qpdk.cli import main as qpdk_main
from qpdk.validator.cli import main as qpdk_validate_main


# --- minimal plugin sources ------------------------------------------------

_GOOD = (
    'PluginInfo = { Name = "T~Good", Version = "1.0.0", Id = "com.t.good" }\n'
    "function GetProperties() return {} end\n"
    "function GetControls(props) return {} end\n"
    "function GetControlLayout() return {}, {} end\n"
)

# Log.Warning() is QPDK020 — an ERROR (flips valid -> False, exit 1).
_ERROR = _GOOD + "if Controls then Log.Warning('x') end\n"

# A control with spaces in the name -> QPDK010 INFO; under --strict a WARNING
# is needed for exit 2. Use a 4-part-ok plugin with a genuine WARNING: a
# rapidjson use without require (QPDK021, WARNING).
_WARNING = (
    'PluginInfo = { Name = "T~Warn", Version = "1.0.0", Id = "com.t.warn" }\n'
    "function GetProperties() return {} end\n"
    "function GetControls(props) return {} end\n"
    "function GetControlLayout() return {}, {} end\n"
    "if Controls then local x = rapidjson.decode('{}') end\n"
)


@pytest.fixture
def runner():
    return CliRunner()


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# `qpdk validate` (primary CLI)
# ---------------------------------------------------------------------------

class TestQpdkValidateExitCodes:
    def test_good_exit_0(self, runner, tmp_path):
        path = _write(tmp_path, "good.qplug", _GOOD)
        res = runner.invoke(qpdk_main, ["validate", path])
        assert res.exit_code == 0

    def test_error_exit_1(self, runner, tmp_path):
        path = _write(tmp_path, "err.qplug", _ERROR)
        res = runner.invoke(qpdk_main, ["validate", path])
        assert res.exit_code == 1

    def test_strict_warning_exit_2(self, runner, tmp_path):
        path = _write(tmp_path, "warn.qplug", _WARNING)
        res = runner.invoke(qpdk_main, ["validate", path, "--strict"])
        assert res.exit_code == 2

    def test_warning_without_strict_exit_0(self, runner, tmp_path):
        path = _write(tmp_path, "warn.qplug", _WARNING)
        res = runner.invoke(qpdk_main, ["validate", path])
        assert res.exit_code == 0


class TestQpdkValidateJsonShape:
    def test_single_file_is_top_level_object(self, runner, tmp_path):
        path = _write(tmp_path, "good.qplug", _GOOD)
        res = runner.invoke(qpdk_main, ["validate", path, "-f", "json"])
        data = json.loads(res.output)
        # single-file contract: one result object with plugin_path + valid
        assert isinstance(data, dict)
        assert "plugin_path" in data
        assert "valid" in data
        assert data["valid"] is True
        assert "total" not in data  # not the multi-file envelope

    def test_multi_file_is_envelope(self, runner, tmp_path):
        p1 = _write(tmp_path, "a.qplug", _GOOD)
        p2 = _write(tmp_path, "b.qplug", _ERROR)
        res = runner.invoke(qpdk_main, ["validate", p1, p2, "-f", "json"])
        data = json.loads(res.output)
        assert isinstance(data, dict)
        assert data["total"] == 2
        assert "results" in data
        assert len(data["results"]) == 2

    def test_tap_format(self, runner, tmp_path):
        path = _write(tmp_path, "err.qplug", _ERROR)
        res = runner.invoke(qpdk_main, ["validate", path, "-f", "tap"])
        assert "TAP version" in res.output
        assert "ok " in res.output
        assert "not ok " in res.output  # the QPDK020 error


# ---------------------------------------------------------------------------
# `qpdk design-check` (Designer drag-in evaluation)
# ---------------------------------------------------------------------------

# Crashes at design time: top-level Controls index with Controls nil.
_DESIGN_CRASH = (
    'PluginInfo = { Name = "T~Crash", Version = "1.0.0", Id = "com.t.crash" }\n'
    "function GetProperties() return {} end\n"
    "function GetControls(props) return {} end\n"
    "function GetControlLayout() return {}, {} end\n"
    "Controls.led.Value = 1\n"
)


class TestDesignCheckCommand:
    def test_clean_plugin_exit_0(self, runner, tmp_path):
        path = _write(tmp_path, "good.qplug", _GOOD)
        res = runner.invoke(qpdk_main, ["design-check", path])
        assert res.exit_code == 0, res.output

    def test_design_crash_exit_1_with_error(self, runner, tmp_path):
        path = _write(tmp_path, "crash.qplug", _DESIGN_CRASH)
        res = runner.invoke(qpdk_main, ["design-check", path])
        assert res.exit_code == 1
        assert "Controls" in res.output or "nil" in res.output

    def test_json_format(self, runner, tmp_path):
        good = _write(tmp_path, "good.qplug", _GOOD)
        crash = _write(tmp_path, "crash.qplug", _DESIGN_CRASH)
        res = runner.invoke(qpdk_main, ["design-check", good, crash, "-f", "json"])
        assert res.exit_code == 1
        parsed = json.loads(res.output)
        assert parsed["clean"] is False
        by_plugin = {r["plugin"]: r for r in parsed["results"]}
        assert by_plugin[good]["errors"] == []
        assert by_plugin[crash]["errors"]


# ---------------------------------------------------------------------------
# Machine formats must be ANSI-free even under FORCE_COLOR (D7)
# ---------------------------------------------------------------------------

import os
import subprocess
import sys


def _run_cli_forced_color(entry_module, argv, tmp_path):
    """Run a CLI in a subprocess with FORCE_COLOR=3 (the corruption repro:
    rich honors FORCE_COLOR even when stdout is redirected, so machine
    formats must bypass rich entirely)."""
    code = (
        "import sys\n"
        f"sys.argv = {argv!r}\n"
        f"from {entry_module} import main\n"
        "main()\n"
    )
    env = dict(os.environ, FORCE_COLOR="3")
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, env=env, cwd=str(tmp_path),
    )


class TestMachineOutputAnsiFree:
    def test_qpdk_validate_json_parses_under_force_color(self, tmp_path):
        path = _write(tmp_path, "good.qplug", _GOOD)
        proc = _run_cli_forced_color(
            "qpdk.cli", ["qpdk", "validate", path, "-f", "json"], tmp_path)
        assert b"\x1b[" not in proc.stdout, "ANSI escapes leaked into JSON"
        parsed = json.loads(proc.stdout)
        assert parsed["valid"] is True

    def test_qpdk_validate_tap_ansi_free_under_force_color(self, tmp_path):
        path = _write(tmp_path, "good.qplug", _GOOD)
        proc = _run_cli_forced_color(
            "qpdk.cli", ["qpdk", "validate", path, "-f", "tap"], tmp_path)
        assert b"\x1b[" not in proc.stdout

    def test_qpdk_validate_standalone_json_parses_under_force_color(self, tmp_path):
        path = _write(tmp_path, "good.qplug", _GOOD)
        proc = _run_cli_forced_color(
            "qpdk.validator.cli", ["qpdk-validate", path, "-f", "json"], tmp_path)
        assert b"\x1b[" not in proc.stdout, "ANSI escapes leaked into JSON"
        parsed = json.loads(proc.stdout)
        assert parsed["total"] == 1

    def test_qpdk_rules_json_parses_under_force_color(self, tmp_path):
        proc = _run_cli_forced_color(
            "qpdk.cli", ["qpdk", "rules", "-f", "json"], tmp_path)
        assert b"\x1b[" not in proc.stdout
        assert len(json.loads(proc.stdout)["rules"]) > 20


# ---------------------------------------------------------------------------
# `qpdk rules` (rule catalog)
# ---------------------------------------------------------------------------

class TestQpdkRulesCommand:
    """The catalog must reflect RULE_METADATA exactly and stay machine-clean."""

    def test_json_matches_metadata(self, runner):
        from qpdk.validator.rulesets import RULE_METADATA

        res = runner.invoke(qpdk_main, ["rules", "-f", "json"])
        assert res.exit_code == 0
        catalog = json.loads(res.output)["rules"]
        assert {r["code"] for r in catalog} == set(RULE_METADATA)
        by_code = {r["code"]: r for r in catalog}
        assert by_code["QPDK001"]["severity"] == "error"
        assert "P0" in by_code["QPDK001"]["rulesets"]

    def test_table_renders(self, runner):
        res = runner.invoke(qpdk_main, ["rules"])
        assert res.exit_code == 0
        assert "QPDK001" in res.output


# ---------------------------------------------------------------------------
# `qpdk test` (pytest runner)
# ---------------------------------------------------------------------------

_PASSING_TEST = "def test_ok():\n    assert True\n"
_FAILING_TEST = "def test_broken():\n    assert False\n"


class TestQpdkTestCommand:
    """`qpdk test` must never silent-green: no test dir -> non-zero.

    Fixture test dirs are generated in tmp_path (not tests/fixtures/) so the
    main suite's own collection never picks up the intentionally-failing
    fixture tests.
    """

    def _plugin(self, tmp_path):
        return _write(tmp_path, "plugin.qplug", _GOOD)

    def test_missing_test_dir_nonzero(self, runner, tmp_path):
        path = self._plugin(tmp_path)
        res = runner.invoke(qpdk_main, ["test", path])
        assert res.exit_code != 0
        assert "no test directory found" in res.output

    def test_explicit_test_dir_missing_nonzero(self, runner, tmp_path):
        path = self._plugin(tmp_path)
        res = runner.invoke(qpdk_main, ["test", path, "--test-dir", str(tmp_path / "nope")])
        assert res.exit_code != 0

    def test_passing_test_dir_zero(self, runner, tmp_path):
        path = self._plugin(tmp_path)
        tdir = tmp_path / "test"
        tdir.mkdir()
        (tdir / "test_pass.py").write_text(_PASSING_TEST, encoding="utf-8")
        res = runner.invoke(qpdk_main, ["test", path])
        assert res.exit_code == 0

    def test_failing_test_dir_nonzero(self, runner, tmp_path):
        path = self._plugin(tmp_path)
        tdir = tmp_path / "tests"
        tdir.mkdir()
        (tdir / "test_fail.py").write_text(_FAILING_TEST, encoding="utf-8")
        res = runner.invoke(qpdk_main, ["test", path])
        assert res.exit_code != 0

    def test_explicit_test_dir_passing_zero(self, runner, tmp_path):
        path = self._plugin(tmp_path)
        tdir = tmp_path / "elsewhere"
        tdir.mkdir()
        (tdir / "test_pass.py").write_text(_PASSING_TEST, encoding="utf-8")
        res = runner.invoke(qpdk_main, ["test", path, "--test-dir", str(tdir)])
        assert res.exit_code == 0


# ---------------------------------------------------------------------------
# `qpdk-validate` (standalone CLI)
# ---------------------------------------------------------------------------

class TestQpdkValidateStandalone:
    def test_empty_args_exit_1(self, runner):
        res = runner.invoke(qpdk_validate_main, [])
        assert res.exit_code == 1

    def test_good_exit_0(self, runner, tmp_path):
        path = _write(tmp_path, "good.qplug", _GOOD)
        res = runner.invoke(qpdk_validate_main, [path])
        assert res.exit_code == 0

    def test_error_exit_1(self, runner, tmp_path):
        path = _write(tmp_path, "err.qplug", _ERROR)
        res = runner.invoke(qpdk_validate_main, [path])
        assert res.exit_code == 1

    def test_strict_warning_exit_2(self, runner, tmp_path):
        path = _write(tmp_path, "warn.qplug", _WARNING)
        res = runner.invoke(qpdk_validate_main, [path, "--strict"])
        assert res.exit_code == 2

    def test_json_envelope_shape(self, runner, tmp_path):
        path = _write(tmp_path, "good.qplug", _GOOD)
        res = runner.invoke(qpdk_validate_main, [path, "-f", "json"])
        data = json.loads(res.output)
        # standalone CLI always emits the multi-file envelope shape
        assert data["total"] == 1
        assert "results" in data
