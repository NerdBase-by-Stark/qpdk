"""Integration tests for the full QPDK validation pipeline.

These tests exercise the complete validator with fixture files, ruleset
filtering, suppression comments, and CLI flag behavior.  They verify
end-to-end behavior rather than individual rule logic.

Fixture files tested:
  valid_plugin.qplug       -- should pass all checks
  missing_guard.qplug      -- should fail QPDK025 (runtime guard)
  local_timer.qplug        -- should fail QPDK005 (timer scoping)
  incomplete_ssh.qplug     -- should fail QPDK028 (SSH handlers)
"""

import pytest
from pathlib import Path

from qpdk.validator import PluginValidator, ValidationResult, IssueSeverity
from qpdk.validator.rulesets import (
    get_ruleset,
    get_rule_severity,
    get_rule_phase,
    RULE_METADATA,
    RULESETS,
    RULESET_P0,
    RULESET_DEFAULT,
    RULESET_FULL,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _codes(result: ValidationResult) -> set[str]:
    """Return the set of issue codes in a validation result."""
    return {i.code for i in result.issues}


def _errors(result: ValidationResult) -> set[str]:
    """Return the set of error codes in a validation result."""
    return {i.code for i in result.errors}


def _warnings(result: ValidationResult) -> set[str]:
    """Return the set of warning codes in a validation result."""
    return {i.code for i in result.warnings}


# ---------------------------------------------------------------------------
# Fixture file integration tests
# ---------------------------------------------------------------------------

class TestValidPluginFixture:
    """valid_plugin.qplug should pass all validation checks."""

    def test_valid_plugin_passes_full_ruleset(self):
        """Full ruleset should find no errors in valid_plugin.qplug."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "valid_plugin.qplug").read_text())
        assert result.valid, f"Expected valid, got errors: {[e.message for e in result.errors]}"
        assert len(result.errors) == 0

    def test_valid_plugin_has_plugin_info(self):
        """valid_plugin should parse PluginInfo correctly."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "valid_plugin.qplug").read_text())
        assert result.plugin_info is not None
        assert result.plugin_info["Name"] == "Test~Valid Plugin"
        assert result.plugin_info["Version"] == "1.0.0"
        assert result.plugin_info["Id"] == "test.valid-plugin"

    def test_valid_plugin_counts_controls(self):
        """valid_plugin should correctly count controls."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "valid_plugin.qplug").read_text())
        assert result.controls_count >= 3  # status, connect, device_name

    def test_valid_plugin_no_qpdk025(self):
        """valid_plugin has runtime guard -- no QPDK025."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "valid_plugin.qplug").read_text())
        assert "QPDK025" not in _codes(result)

    def test_valid_plugin_no_qpdk005(self):
        """valid_plugin uses module-level timer -- no QPDK005."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "valid_plugin.qplug").read_text())
        assert "QPDK005" not in _codes(result)

    def test_valid_plugin_no_qpdk020(self):
        """valid_plugin uses Log.Message -- no QPDK020."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "valid_plugin.qplug").read_text())
        assert "QPDK020" not in _codes(result)


class TestMissingGuardFixture:
    """missing_guard.qplug should fail on QPDK025 (runtime guard)."""

    def test_missing_guard_triggers_qpdk025(self):
        """missing_guard.qplug should produce QPDK025 error."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "missing_guard.qplug").read_text())
        assert "QPDK025" in _codes(result)

    def test_missing_guard_is_error_not_warning(self):
        """QPDK025 is phase 1 -- severity must be ERROR."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "missing_guard.qplug").read_text())
        qpdk025_issues = [i for i in result.issues if i.code == "QPDK025"]
        assert len(qpdk025_issues) > 0
        assert qpdk025_issues[0].severity == IssueSeverity.ERROR

    def test_missing_guard_caught_by_p0_ruleset(self):
        """QPDK025 is a P0 crash-preventing rule."""
        v = PluginValidator(ruleset="P0")
        result = v.validate_string((FIXTURES / "missing_guard.qplug").read_text())
        assert "QPDK025" in _codes(result)

    def test_missing_guard_has_valid_plugininfo(self):
        """missing_guard.qplug has valid PluginInfo -- no QPDK002."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "missing_guard.qplug").read_text())
        assert "QPDK002" not in _codes(result)


class TestLocalTimerFixture:
    """local_timer.qplug should fail on QPDK005 (timer scoping)."""

    def test_local_timer_triggers_qpdk005(self):
        """local_timer.qplug should produce QPDK005 warning."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "local_timer.qplug").read_text())
        assert "QPDK005" in _codes(result)

    def test_local_timer_is_warning(self):
        """QPDK005 is a WARNING -- dangerous but not always crash-causing."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "local_timer.qplug").read_text())
        qpdk005_issues = [i for i in result.issues if i.code == "QPDK005"]
        assert len(qpdk005_issues) > 0
        assert qpdk005_issues[0].severity == IssueSeverity.WARNING

    def test_local_timer_caught_by_p0_ruleset(self):
        """QPDK005 is phase 1 -- should be caught by P0 ruleset."""
        v = PluginValidator(ruleset="P0")
        result = v.validate_string((FIXTURES / "local_timer.qplug").read_text())
        assert "QPDK005" in _codes(result)

    def test_local_timer_reports_line_number(self):
        """QPDK005 should report the line number of the local declaration."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "local_timer.qplug").read_text())
        qpdk005_issues = [i for i in result.issues if i.code == "QPDK005"]
        assert len(qpdk005_issues) > 0
        assert qpdk005_issues[0].line is not None
        assert qpdk005_issues[0].line > 0

    def test_local_timer_has_valid_plugininfo(self):
        """local_timer.qplug has valid PluginInfo -- no QPDK002."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "local_timer.qplug").read_text())
        assert "QPDK002" not in _codes(result)


class TestIncompleteSSHFixture:
    """incomplete_ssh.qplug should fail on QPDK028 (SSH handlers)."""

    def test_incomplete_ssh_triggers_qpdk028(self):
        """incomplete_ssh.qplug should produce QPDK028 warnings."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "incomplete_ssh.qplug").read_text())
        assert "QPDK028" in _codes(result)

    def test_incomplete_ssh_missing_5_handlers(self):
        """Should flag at least 5 missing mandatory handlers."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "incomplete_ssh.qplug").read_text())
        qpdk028_warnings = [i for i in result.warnings if i.code == "QPDK028"]
        # Missing: Reconnect, Closed, Error, Timeout, LoginFailed = 5
        assert len(qpdk028_warnings) >= 5

    def test_incomplete_ssh_caught_by_default_ruleset(self):
        """QPDK028 is phase 2 -- should be caught by default ruleset."""
        v = PluginValidator(ruleset="default")
        result = v.validate_string((FIXTURES / "incomplete_ssh.qplug").read_text())
        assert "QPDK028" in _codes(result)

    def test_incomplete_ssh_not_caught_by_p0_ruleset(self):
        """QPDK028 is phase 2 -- should NOT be caught by P0 ruleset."""
        v = PluginValidator(ruleset="P0")
        result = v.validate_string((FIXTURES / "incomplete_ssh.qplug").read_text())
        assert "QPDK028" not in _codes(result)

    def test_incomplete_ssh_has_valid_plugininfo(self):
        """incomplete_ssh.qplug has valid PluginInfo -- no QPDK002."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "incomplete_ssh.qplug").read_text())
        assert "QPDK002" not in _codes(result)


# ---------------------------------------------------------------------------
# Ruleset configuration tests
# ---------------------------------------------------------------------------

class TestRulesetDefinitions:
    """Test that ruleset definitions are correct and complete."""

    def test_p0_is_subset_of_default(self):
        """P0 rules must be a subset of default rules."""
        assert RULESET_P0.issubset(RULESET_DEFAULT)

    def test_default_is_subset_of_full(self):
        """Default rules must be a subset of full rules."""
        assert RULESET_DEFAULT.issubset(RULESET_FULL)

    def test_p0_contains_crash_rules(self):
        """P0 must contain crash-preventing rules."""
        assert "QPDK001" in RULESET_P0  # syntax error
        assert "QPDK002" in RULESET_P0  # missing PluginInfo
        assert "QPDK003" in RULESET_P0  # missing field
        assert "QPDK020" in RULESET_P0  # invalid Log API
        assert "QPDK025" in RULESET_P0  # missing runtime guard
        assert "QPDK005" in RULESET_P0  # timer scoping

    def test_p0_does_not_contain_advisory_rules(self):
        """P0 must NOT contain advisory (phase 3) rules."""
        phase3_rules = {code for code, meta in RULE_METADATA.items() if meta["phase"] == 3}
        assert RULESET_P0.isdisjoint(phase3_rules)

    def test_full_contains_all_rules(self):
        """Full ruleset must contain all defined rules."""
        all_codes = set(RULE_METADATA.keys())
        assert all_codes == RULESET_FULL

    def test_get_ruleset_case_insensitive(self):
        """get_ruleset should handle case-insensitive lookup."""
        assert get_ruleset("p0") == get_ruleset("P0")
        assert get_ruleset("default") == get_ruleset("DEFAULT")

    def test_get_ruleset_invalid_raises(self):
        """get_ruleset should raise ValueError for unknown rulesets."""
        with pytest.raises(ValueError, match="Unknown ruleset"):
            get_ruleset("nonexistent")

    def test_all_rules_have_metadata(self):
        """Every rule code defined on PluginValidator should have metadata."""
        # Gather codes from the class attributes
        validator_codes = set()
        for attr_name in dir(PluginValidator):
            val = getattr(PluginValidator, attr_name, None)
            if isinstance(val, str) and val.startswith("QPDK"):
                validator_codes.add(val)
        # All should be in metadata
        for code in validator_codes:
            assert code in RULE_METADATA, f"Rule {code} missing from RULE_METADATA"


class TestRulesetFiltering:
    """Test that ruleset filtering actually skips rules."""

    def test_p0_skips_phase2_rules(self):
        """P0 ruleset should not flag QPDK028 (phase 2)."""
        code = """\
PluginInfo = { Name = "Test~SSH", Version = "1.0.0", Id = "test.ssh" }
function GetProperties() return {} end
function GetControls() return {} end
function GetControlLayout() return {}, {} end
if Controls then
  local ssh = Ssh.New()
  ssh.Connected = function() end
  ssh:Connect("1.2.3.4", 22, "a", "b")
end
"""
        v = PluginValidator(ruleset="P0")
        result = v.validate_string(code)
        assert "QPDK028" not in _codes(result)

    def test_full_catches_phase2_rules(self):
        """Full ruleset should flag QPDK028 (phase 2)."""
        code = """\
PluginInfo = { Name = "Test~SSH", Version = "1.0.0", Id = "test.ssh" }
function GetProperties() return {} end
function GetControls() return {} end
function GetControlLayout() return {}, {} end
if Controls then
  local ssh = Ssh.New()
  ssh.Connected = function() end
  ssh:Connect("1.2.3.4", 22, "a", "b")
end
"""
        v = PluginValidator(ruleset="full")
        result = v.validate_string(code)
        assert "QPDK028" in _codes(result)

    def test_p0_skips_phase3_rules(self):
        """P0 ruleset should not flag QPDK031 (phase 3)."""
        code = """\
PluginInfo = { Name = "Test~Timer", Version = "1.0.0", Id = "test.timer" }
function GetProperties() return {} end
function GetControls() return {} end
function GetControlLayout() return {}, {} end
if Controls then
  myTimer = Timer.New()
  myTimer.EventHandler = function()
    Log.Message("no stop")
  end
  myTimer:Start(5)
end
"""
        v = PluginValidator(ruleset="P0")
        result = v.validate_string(code)
        assert "QPDK031" not in _codes(result)

    def test_full_catches_phase3_rules(self):
        """Full ruleset should flag QPDK031 (phase 3)."""
        code = """\
PluginInfo = { Name = "Test~Timer", Version = "1.0.0", Id = "test.timer" }
function GetProperties() return {} end
function GetControls() return {} end
function GetControlLayout() return {}, {} end
if Controls then
  myTimer = Timer.New()
  myTimer.EventHandler = function()
    Log.Message("no stop")
  end
  myTimer:Start(5)
end
"""
        v = PluginValidator(ruleset="full")
        result = v.validate_string(code)
        assert "QPDK031" in _codes(result)

    def test_structural_rules_always_run(self):
        """QPDK001-003 should always run regardless of ruleset."""
        code = "this is not valid lua {"
        for ruleset_name in ["P0", "default", "full"]:
            v = PluginValidator(ruleset=ruleset_name)
            result = v.validate_string(code)
            assert "QPDK001" in _errors(result), f"QPDK001 missing in {ruleset_name}"

    def test_default_ruleset_skips_info_only_rules(self):
        """Default ruleset should not include phase 3 INFO rules."""
        phase3_rules = {code for code, meta in RULE_METADATA.items() if meta["phase"] == 3}
        default_rules = get_ruleset("default")
        assert default_rules.isdisjoint(phase3_rules)


# ---------------------------------------------------------------------------
# Suppression integration tests
# ---------------------------------------------------------------------------

class TestSuppressionIntegration:
    """Test suppression comments in full pipeline context."""

    def test_suppression_works_with_fixture_pattern(self):
        """Suppression comment on status value line should suppress QPDK023."""
        code = """\
PluginInfo = { Name = "Test~Sup", Version = "1.0.0", Id = "test.sup" }
function GetProperties() return {} end
function GetControls() return {} end
function GetControlLayout() return {}, {} end
if Controls then
  Controls.Outputs["status"].Value = 99  -- qpdk:ignore QPDK023
end
"""
        v = PluginValidator(ruleset="full")
        result = v.validate_string(code)
        assert "QPDK023" not in _codes(result)

    def test_suppression_does_not_cross_lines(self):
        """Suppression on one line should not affect the next line."""
        code = """\
PluginInfo = { Name = "Test~NoCross", Version = "1.0.0", Id = "test.nocross" }
function GetProperties() return {} end
function GetControls() return {} end
function GetControlLayout() return {}, {} end
if Controls then
  Controls.Outputs["status_1"].Value = 99  -- qpdk:ignore QPDK023
  Controls.Outputs["status_2"].Value = 99
end
"""
        v = PluginValidator(ruleset="full")
        result = v.validate_string(code)
        qpdk023_issues = [i for i in result.issues if i.code == "QPDK023"]
        assert len(qpdk023_issues) == 1  # only status_2 should be flagged

    def test_suppression_multiple_codes(self):
        """Suppression of multiple codes on same line."""
        code = """\
PluginInfo = { Name = "Test~Multi", Version = "1.0.0", Id = "test.multi" }
function GetProperties() return {} end
function GetControls() return {} end
function GetControlLayout() return {}, {} end
if Controls then
  local ssh = Ssh.New()
  ssh:Write("test")  -- qpdk:ignore QPDK029,QPDK024
end
"""
        v = PluginValidator(ruleset="full")
        result = v.validate_string(code)
        assert "QPDK029" not in _codes(result)


# ---------------------------------------------------------------------------
# Severity metadata tests
# ---------------------------------------------------------------------------

class TestSeverityMetadata:
    """Test phased severity level configuration."""

    def test_phase1_rules_are_error_or_warning(self):
        """Phase 1 rules should be ERROR or WARNING."""
        for code, meta in RULE_METADATA.items():
            if meta["phase"] == 1:
                assert meta["severity"] in (IssueSeverity.ERROR, IssueSeverity.WARNING), \
                    f"{code} is phase 1 but severity is {meta['severity']}"

    def test_get_rule_severity(self):
        """get_rule_severity should return correct values."""
        assert get_rule_severity("QPDK001") == IssueSeverity.ERROR
        assert get_rule_severity("QPDK005") == IssueSeverity.WARNING
        assert get_rule_severity("QPDK010") == IssueSeverity.INFO

    def test_get_rule_severity_unknown(self):
        """get_rule_severity should return None for unknown codes."""
        assert get_rule_severity("QPDK999") is None
        assert get_rule_severity("LUACHECK-W111") is None

    def test_get_rule_phase(self):
        """get_rule_phase should return correct phase numbers."""
        assert get_rule_phase("QPDK025") == 1
        assert get_rule_phase("QPDK028") == 2
        assert get_rule_phase("QPDK035") == 3

    def test_get_rule_phase_unknown(self):
        """get_rule_phase should return None for unknown codes."""
        assert get_rule_phase("QPDK999") is None

    def test_every_rule_has_description(self):
        """Every rule in metadata should have a non-empty description."""
        for code, meta in RULE_METADATA.items():
            assert "description" in meta, f"{code} missing description"
            assert len(meta["description"]) > 0, f"{code} has empty description"

    def test_every_rule_has_category(self):
        """Every rule in metadata should have a category."""
        for code, meta in RULE_METADATA.items():
            assert "category" in meta, f"{code} missing category"


# ---------------------------------------------------------------------------
# Full pipeline end-to-end tests
# ---------------------------------------------------------------------------

class TestEndToEnd:
    """End-to-end tests that exercise the complete validation pipeline."""

    def test_valid_plugin_full_pipeline(self):
        """valid_plugin.qplug should pass the full pipeline (validate_file)."""
        path = FIXTURES / "valid_plugin.qplug"
        v = PluginValidator(ruleset="full")
        result = v.validate_file(path)
        # Should be valid (no errors)
        assert result.valid, f"Errors: {[e.message for e in result.errors]}"

    def test_missing_guard_full_pipeline(self):
        """missing_guard.qplug should fail the full pipeline."""
        path = FIXTURES / "missing_guard.qplug"
        v = PluginValidator(ruleset="full")
        result = v.validate_file(path)
        assert not result.valid
        assert "QPDK025" in _errors(result)

    def test_local_timer_full_pipeline(self):
        """local_timer.qplug should produce QPDK005 in full pipeline."""
        path = FIXTURES / "local_timer.qplug"
        v = PluginValidator(ruleset="full")
        result = v.validate_file(path)
        assert "QPDK005" in _warnings(result)

    def test_incomplete_ssh_full_pipeline(self):
        """incomplete_ssh.qplug should produce QPDK028 in full pipeline."""
        path = FIXTURES / "incomplete_ssh.qplug"
        v = PluginValidator(ruleset="full")
        result = v.validate_file(path)
        assert "QPDK028" in _warnings(result)

    def test_p0_ruleset_reduces_noise(self):
        """P0 ruleset on incomplete_ssh should not flag QPDK028 (phase 2)."""
        path = FIXTURES / "incomplete_ssh.qplug"
        v = PluginValidator(ruleset="P0")
        result = v.validate_file(path)
        # QPDK028 is phase 2, not in P0
        assert "QPDK028" not in _codes(result)

    def test_json_output_contains_all_fields(self):
        """JSON output should contain all expected fields."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "valid_plugin.qplug").read_text())
        d = result.to_dict()
        assert "plugin_path" in d
        assert "valid" in d
        assert "summary" in d
        assert "issues" in d
        assert "structure" in d

    def test_tap_output_format(self):
        """TAP output should follow the TAP protocol."""
        v = PluginValidator(ruleset="full")
        result = v.validate_string((FIXTURES / "missing_guard.qplug").read_text())
        tap = result.to_tap()
        assert tap.startswith("TAP version 14")
        assert "not ok" in tap  # should have at least one failure

    def test_validate_nonexistent_file(self):
        """Nonexistent file should produce QPDK001 error."""
        v = PluginValidator(ruleset="full")
        result = v.validate_file("/nonexistent/file.qplug")
        assert not result.valid
        assert any("not found" in e.message.lower() for e in result.errors)

    def test_backwards_compatibility_default_init(self):
        """Default PluginValidator() should run all rules (backwards compat)."""
        v = PluginValidator()
        # Should use full ruleset by default
        assert v.active_rules == RULESET_FULL
