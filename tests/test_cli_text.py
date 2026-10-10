"""Human-facing CLI text: banners, help layout and the rule catalogue table.

These tests read rich text output on purpose. They guard what a person sees
in a terminal or CI log, which test_cli.py (exit codes and JSON) does not.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from qpdk.cli import main as qpdk_main
from qpdk.qrc_tester.cli import main as qpdk_qrc_main
from qpdk.validator.cli import main as qpdk_validate_main

_EXAMPLES = Path(__file__).parent.parent / "examples"
_GOOD_EXAMPLE = _EXAMPLES / "good" / "simple-button.qplug"

# A plugin with one WARNING (QPDK021, rapidjson without require) and no errors.
_WARNING = (
    'PluginInfo = { Name = "T~Warn", Version = "1.0.0", Id = "com.t.warn" }\n'
    "function GetProperties() return {} end\n"
    "function GetControls(props) return {} end\n"
    "function GetControlLayout() return {}, {} end\n"
    "if Controls then local x = rapidjson.decode('{}') end\n"
)


@pytest.fixture
def warn_plugin(tmp_path):
    p = tmp_path / "warn.qplug"
    p.write_text(_WARNING, encoding="utf-8")
    return str(p)


def _banner_words(output: str) -> set[str]:
    return set(output.replace("│", " ").split())


class TestStrictBanner:
    """A run that fails under --strict must not show a VALID banner."""

    @pytest.mark.parametrize("cli,prefix", [(qpdk_main, ["validate"]),
                                            (qpdk_validate_main, [])])
    def test_strict_with_warnings_says_failed(self, cli, prefix, warn_plugin):
        res = CliRunner().invoke(cli, [*prefix, warn_plugin, "--strict"])
        assert res.exit_code == 2
        words = _banner_words(res.output)
        assert "FAILED" in words
        assert "VALID" not in words
        assert "--strict" in res.output

    @pytest.mark.parametrize("cli,prefix", [(qpdk_main, ["validate"]),
                                            (qpdk_validate_main, [])])
    def test_without_strict_warnings_stay_valid(self, cli, prefix, warn_plugin):
        res = CliRunner().invoke(cli, [*prefix, warn_plugin])
        assert res.exit_code == 0
        words = _banner_words(res.output)
        assert "VALID" in words
        assert "FAILED" not in words

    def test_strict_clean_plugin_stays_valid(self):
        res = CliRunner().invoke(qpdk_main, ["validate", str(_GOOD_EXAMPLE), "--strict"])
        assert res.exit_code == 0
        assert "VALID" in _banner_words(res.output)


class TestRulesTable:
    def test_bracket_text_survives_rich_markup(self):
        # "[i]" in QPDK050's description used to be eaten as rich italic markup.
        res = CliRunner().invoke(qpdk_main, ["rules"], env={"COLUMNS": "400"})
        assert res.exit_code == 0
        line = next(ln for ln in res.output.splitlines() if "QPDK050" in ln)
        assert "[i]" in line

    def test_phase_column_is_explained(self):
        res = CliRunner().invoke(qpdk_main, ["rules"], env={"COLUMNS": "400"})
        assert "1 = crash-preventing, 2 = quality, 3 = advisory" in res.output

    def test_descriptions_have_no_internal_jargon_or_dashes(self):
        res = CliRunner().invoke(qpdk_main, ["rules", "--format", "json"])
        for rule in json.loads(res.output)["rules"]:
            text = rule["description"]
            for word in ("corpus", "canon", "probe", "managed-submission"):
                assert word not in text, (rule["code"], text)
            assert "—" not in text and "–" not in text, rule["code"]


class TestHelpLayout:
    def test_qpdk_validate_examples_keep_line_breaks(self):
        res = CliRunner().invoke(qpdk_validate_main, ["--help"])
        lines = [ln.strip() for ln in res.output.splitlines()]
        assert "qpdk-validate plugin.qplug" in lines
        assert "qpdk-validate plugin.qplug --strict" in lines

    def test_qpdk_qrc_examples_keep_line_breaks(self):
        res = CliRunner().invoke(qpdk_qrc_main, ["--help"])
        lines = [ln.strip() for ln in res.output.splitlines()]
        assert any(ln.startswith("qpdk-qrc --record session.json") for ln in lines)
        assert any(ln.startswith("qpdk-qrc --playback recorded.json") for ln in lines)

    @pytest.mark.parametrize("cli,prefix", [(qpdk_main, ["qrc"]), (qpdk_qrc_main, [])])
    def test_qrc_help_shows_defaults(self, cli, prefix):
        res = CliRunner().invoke(cli, [*prefix, "--help"])
        assert "[default: 1710]" in res.output
        assert "[default: 127.0.0.1]" in res.output

    @pytest.mark.parametrize("command", ["validate", "design-check", "qsc-eval",
                                         "test", "rules", "qrc"])
    def test_help_has_no_em_or_en_dashes(self, command):
        res = CliRunner().invoke(qpdk_main, [command, "--help"])
        assert "—" not in res.output and "–" not in res.output

    def test_validate_help_explains_default_ruleset(self):
        res = CliRunner().invoke(qpdk_main, ["validate", "--help"], terminal_width=200)
        assert "phase 1 and 2" not in res.output
        assert "not the same as leaving the flag off" in res.output


class TestGoodExample:
    def test_good_example_passes_qsc_eval_strict(self):
        res = CliRunner().invoke(qpdk_main, ["qsc-eval", str(_GOOD_EXAMPLE),
                                             "--strict", "-f", "json"])
        assert res.exit_code == 0, res.output
        assert json.loads(res.output)["results"][0]["warnings"] == []

    def test_good_example_clean_under_full_ruleset(self):
        res = CliRunner().invoke(qpdk_main, ["validate", str(_GOOD_EXAMPLE),
                                             "-r", "full", "--strict", "-f", "json"])
        assert res.exit_code == 0, res.output
        # QPDK000 only says luacheck is not installed on this machine.
        codes = [i["code"] for i in json.loads(res.output)["issues"]]
        assert [c for c in codes if c != "QPDK000"] == []
