"""Plugin code must not reach the host Python, start processes, or write to
stdout (which carries JSON/TAP for machine consumers)."""
import json

from click.testing import CliRunner

from qpdk.cli import main as cli_main
from qpdk.mock_runtime import QSYSRuntime

_PROBE = '''PluginInfo = { Name = "Probe", Version = "1.0.0", Id = "test.probe", Author = "T" }
function GetProperties() return {} end
function GetControls() return {} end
function GetControlLayout() return {}, {} end
print("PLUGIN_PRINT", python, os.execute, io.popen, package.loadlib)
Log.Message("PLUGIN_LOG")
'''


def test_runtime_has_no_host_escape_globals(tmp_path):
    path = tmp_path / "probe.qplug"
    path.write_text(_PROBE, encoding="utf-8")
    rt = QSYSRuntime()
    rt.load_plugin(str(path))
    for expr in ("python", "os.execute", "os.exit", "io.popen", "package.loadlib"):
        assert rt._lua.eval(expr) is None, expr


def test_plugin_output_does_not_corrupt_json(tmp_path):
    path = tmp_path / "probe.qplug"
    path.write_text(_PROBE, encoding="utf-8")
    runner = CliRunner()
    for args in (["validate", str(path), "-f", "json"],
                 ["design-check", str(path), "-f", "json"]):
        res = runner.invoke(cli_main, args)
        json.loads(res.stdout)  # raises if plugin output leaked into stdout
        assert "PLUGIN_PRINT" not in res.stdout
        assert "PLUGIN_LOG" not in res.stdout
