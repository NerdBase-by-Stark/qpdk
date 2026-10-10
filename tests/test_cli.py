"""CLI contract tests.

Guards the CI-load-bearing exit-code (0/1/2), single-vs-multi-file JSON shape,
and TAP shape for BOTH `qpdk validate` and the standalone `qpdk-validate`
entry point. Assertions are on exit_code and parsed JSON keys, never rich text.
"""

import json
import os
import subprocess
import sys

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
# Machine formats must be ANSI-free even under FORCE_COLOR
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Directory arguments get a usage error, not a traceback
# ---------------------------------------------------------------------------

class TestDirectoryArgumentRejected:
    @pytest.mark.parametrize("command", ["validate", "design-check", "qsc-eval"])
    def test_directory_is_a_usage_error(self, runner, tmp_path, command):
        res = runner.invoke(qpdk_main, [command, str(tmp_path)])
        assert res.exit_code == 2
        assert "is a directory" in res.output
        # click usage errors exit via SystemExit; anything else is a crash
        assert res.exception is None or isinstance(res.exception, SystemExit)


class TestRulesHelp:
    def test_help_points_users_at_json(self, runner):
        res = runner.invoke(qpdk_main, ["rules", "--help"])
        assert res.exit_code == 0
        assert "--format json" in res.output
        assert "README" not in res.output


# ---------------------------------------------------------------------------
# Shipped examples: the good one passes every check, the bad ones are caught
# ---------------------------------------------------------------------------

import re  # noqa: E402
from pathlib import Path  # noqa: E402

_EXAMPLES = Path(__file__).parent.parent / "examples"
_GOOD_EXAMPLE = _EXAMPLES / "good" / "simple-button.qplug"


class TestShippedExamples:
    def test_good_example_validates_strict(self, runner):
        res = runner.invoke(qpdk_main, ["validate", str(_GOOD_EXAMPLE), "--strict"])
        assert res.exit_code == 0, res.output

    def test_good_example_design_check(self, runner):
        res = runner.invoke(qpdk_main, ["design-check", str(_GOOD_EXAMPLE)])
        assert res.exit_code == 0, res.output

    def test_good_example_qsc_eval(self, runner):
        res = runner.invoke(qpdk_main, ["qsc-eval", str(_GOOD_EXAMPLE), "-f", "json"])
        assert res.exit_code == 0, res.output
        result = json.loads(res.output)["results"][0]
        assert result["errors"] == []
        assert result["crash"] is None

    def test_good_example_uses_no_status_global(self):
        # Status/StatusState are not Q-SYS globals; the example must set its
        # declared Status control instead.
        src = _GOOD_EXAMPLE.read_text(encoding="utf-8")
        assert not re.search(r'(?<![\w."\[])Status\s*[.:(]', src)
        assert "StatusState" not in src

    def test_good_example_runs_in_mock_runtime(self):
        from qpdk.mock_runtime import QSYSRuntime

        rt = QSYSRuntime()  # no properties seeded: the example must cope
        rt.load_plugin(_GOOD_EXAMPLE)
        rt.tick(1.0)
        assert not rt.had_errors(), rt.error_messages
        assert rt.controls["Status"].Value == 0
        assert rt.controls["Status"].String == "OK"
        rt.controls["Toggle"].simulate_external_write(1)
        assert rt.controls["State"].Boolean is True

    def test_good_example_runs_in_harness(self):
        from qpdk.testing import PluginHarness

        harness = PluginHarness(_GOOD_EXAMPLE, property_defaults={"Initial State": True})
        rt = harness.make_runtime()
        rt.tick(1.0)
        assert not rt.had_errors(), rt.error_messages
        assert rt.controls["State"].Boolean is True

    @pytest.mark.parametrize("name", ["local-timer.qplug", "missing-plugininfo.qplug"])
    def test_bad_examples_are_caught(self, runner, name):
        res = runner.invoke(qpdk_main, ["validate", str(_EXAMPLES / "bad" / name), "--strict"])
        assert res.exit_code in (1, 2)
        assert "QPDK" in res.output


# ---------------------------------------------------------------------------
# scripts/bump-version.sh
# ---------------------------------------------------------------------------

import shutil  # noqa: E402


@pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                    reason="needs git and bash")
def test_bump_version_commit_has_plain_message(tmp_path):
    root = Path(__file__).parent.parent
    (tmp_path / "scripts").mkdir()
    (tmp_path / "qpdk").mkdir()
    shutil.copy(root / "scripts" / "bump-version.sh", tmp_path / "scripts")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    (tmp_path / "qpdk" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    git = ["git", "-C", str(tmp_path)]
    subprocess.run([*git, "init", "-q"], check=True, env=env)
    subprocess.run([*git, "add", "."], check=True, env=env)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True, env=env)
    res = subprocess.run(["bash", str(tmp_path / "scripts" / "bump-version.sh"),
                          "patch", "--commit"], capture_output=True, text=True, env=env)
    if res.returncode != 0 and "grep" in res.stderr:
        pytest.skip("grep -P not available")
    assert res.returncode == 0, res.stderr
    msg = subprocess.run([*git, "log", "-1", "--format=%B"], capture_output=True,
                         text=True, check=True, env=env).stdout.strip()
    assert msg == "chore: bump version to 1.2.4"


# --- Default output hides INFO advisories ------------------------------------

_INFO_ONLY = '''PluginInfo = { Name = "Info~Only", Version = "1.0.0", Id = "test.info-only" }
function GetProperties() return {} end
function GetControls() return {} end
function GetControlLayout() return {}, {} end
if Controls then
  function Remember() sharedCount = 1 end
  function Recall() return sharedCount end
end
'''


class TestDefaultHidesInfo:
    def test_default_ruleset_hides_info_and_says_so(self, tmp_path):
        import shutil

        from click.testing import CliRunner

        from qpdk.cli import main as cli_main
        if shutil.which("luacheck") is None:
            pytest.skip("needs luacheck for the W111/W113 INFO findings")
        path = _write(tmp_path, "info.qplug", _INFO_ONLY)
        res = CliRunner().invoke(cli_main, ["validate", path, "-f", "json"])
        data = json.loads(res.output)
        assert data["summary"]["info"] == 0
        assert data["summary"]["warnings"] == 0
        assert data["summary"]["info_hidden"] >= 1  # luacheck W111/W113 on sharedCount
        full = CliRunner().invoke(cli_main, ["validate", path, "-f", "json", "-r", "full"])
        assert json.loads(full.output)["summary"]["info"] >= 1
        text = CliRunner().invoke(cli_main, ["validate", path])
        assert "info notes hidden" in text.output

    def test_quiet_hides_info_rows(self, tmp_path):
        from click.testing import CliRunner

        from qpdk.cli import main as cli_main
        path = _write(tmp_path, "info.qplug", _INFO_ONLY)
        res = CliRunner().invoke(cli_main, ["validate", path, "-r", "full", "-q"])
        assert "QPDK047" not in res.output


def test_no_ruleset_keeps_phase3_warnings(tmp_path):
    """Omitting --ruleset must not drop WARNING rules outside phases 1-2
    (QPDK029, unprotected socket write, is phase 3)."""
    from click.testing import CliRunner

    from qpdk.cli import main as cli_main
    src = _INFO_ONLY.replace("if Controls then", """if Controls then
  sock = TcpSocket.New()
  function Send(s) sock:Write(s) end""")
    path = _write(tmp_path, "w.qplug", src)
    res = CliRunner().invoke(cli_main, ["validate", path, "-f", "json"])
    codes = {i["code"] for i in json.loads(res.output)["issues"]}
    assert "QPDK029" in codes


def test_qsc_eval_strict_warning_is_not_shown_as_ok(tmp_path):
    """Under --strict a QSC warning fails the run, so the row must not say OK."""
    from click.testing import CliRunner

    from qpdk.cli import main as cli_main
    src = _INFO_ONLY.replace('Id = "test.info-only" }',
                             'Id = "test.info-only", Author = "Test" }')
    path = _write(tmp_path, "q.qplug", src)  # no Manufacturer: a QSC warning only
    res = CliRunner().invoke(cli_main, ["qsc-eval", path, "--strict"])
    assert res.exit_code == 1
    assert res.output.lstrip().startswith("FAIL")
