"""Tests for Plugin Validator"""

import pytest
from pathlib import Path

from qpdk.validator import PluginValidator, ValidationResult, IssueSeverity


@pytest.fixture
def validator():
    return PluginValidator(qsys_version="9")


@pytest.fixture
def good_plugin_path():
    return Path(__file__).parent.parent / "examples" / "good" / "simple-button.qplug"


@pytest.fixture
def bad_plugin_missing_info():
    return Path(__file__).parent.parent / "examples" / "bad" / "missing-plugininfo.qplug"


@pytest.fixture
def bad_plugin_timer():
    return Path(__file__).parent.parent / "examples" / "bad" / "local-timer.qplug"


class TestPluginValidator:
    """Test cases for PluginValidator."""

    def test_validate_good_plugin(self, validator, good_plugin_path):
        """Good plugin should pass validation."""
        if not good_plugin_path.exists():
            pytest.skip("Example plugin not found")

        result = validator.validate_file(good_plugin_path)

        assert result.valid
        assert len(result.errors) == 0
        assert result.plugin_info is not None
        assert result.plugin_info.get("Name") == "Examples~Simple Button"
        assert result.plugin_info.get("Version") == "1.0.0"

    def test_validate_missing_plugininfo(self, validator, bad_plugin_missing_info):
        """Missing PluginInfo should fail validation."""
        if not bad_plugin_missing_info.exists():
            pytest.skip("Example plugin not found")

        result = validator.validate_file(bad_plugin_missing_info)

        assert not result.valid
        assert any(e.code == "QPDK002" for e in result.errors)

    def test_validate_timer_scoping_warning(self, validator, bad_plugin_timer):
        """Local timer should trigger warning."""
        if not bad_plugin_timer.exists():
            pytest.skip("Example plugin not found")

        result = validator.validate_file(bad_plugin_timer)

        # Should have timer scoping warning
        timer_warnings = [
            w for w in result.warnings
            if w.code == "QPDK005"
        ]
        assert len(timer_warnings) > 0

    def test_validate_nonexistent_file(self, validator):
        """Nonexistent file should return error."""
        result = validator.validate_file("/nonexistent/plugin.qplug")

        assert not result.valid
        assert any("not found" in e.message.lower() for e in result.errors)

    def test_validate_string_valid(self, validator):
        """Valid plugin string should pass."""
        code = '''
PluginInfo = {
    Name = "Test~Plugin",
    Version = "1.0.0",
    Id = "com.test.plugin"
}

function GetProperties()
    return {}
end

function GetControls(props)
    return {}
end
'''
        result = validator.validate_string(code)

        assert result.valid
        assert result.plugin_info["Name"] == "Test~Plugin"

    def test_validate_string_syntax_error(self, validator):
        """Syntax error should fail validation."""
        code = '''
PluginInfo = {
    Name = "Test~Plugin"
    -- Missing comma and closing brace
'''
        result = validator.validate_string(code)

        assert not result.valid
        assert any(e.code == "QPDK001" for e in result.errors)

    def test_qpdk039_unsubstituted_version_placeholder_is_error(self, validator):
        """A build-template placeholder in PluginInfo.Version is an ERROR (QPDK039),
        not a semantic-version WARNING (QPDK004). Indicates the build pipeline
        failed to substitute the token before producing the .qplug."""
        code = '''
PluginInfo = {
    Name = "Test~Plugin",
    Version = "__VERSION__",
    BuildVersion = "__BUILD_VERSION__",
    Id = "com.test.plugin"
}
function GetProperties() return {} end
function GetControls(props) return {} end
'''
        result = validator.validate_string(code)

        assert not result.valid
        # QPDK039 fires twice — once per placeholder field
        qpdk039_errors = [e for e in result.errors if e.code == "QPDK039"]
        assert len(qpdk039_errors) == 2
        # QPDK004 should NOT fire on the placeholder Version (suppressed because
        # QPDK039 already reported the underlying problem)
        qpdk004_version = [
            w for w in result.warnings
            if w.code == "QPDK004" and "Version should be semantic" in w.message
        ]
        assert qpdk004_version == [], "QPDK004 should not double-report on placeholder values"

    def test_real_version_after_substitution_passes(self, validator):
        """After build-time substitution, a 4-part version like 2.5.4.7 reaches
        QPDK004 (acceptable warning) but does NOT fire QPDK039."""
        code = '''
PluginInfo = {
    Name = "Test~Plugin",
    Version = "2.5.4.7",
    Id = "com.test.plugin"
}
function GetProperties() return {} end
function GetControls(props) return {} end
'''
        result = validator.validate_string(code)

        # 4-part is the documented-acceptable QPDK004 warning, not an error
        assert all(e.code != "QPDK039" for e in result.errors)


class TestValidationResult:
    """Test cases for ValidationResult."""

    def test_result_to_json(self):
        """Result should serialize to JSON."""
        result = ValidationResult(plugin_path="test.qplug")
        result.add_error("TEST001", "Test error", line=10)
        result.add_warning("TEST002", "Test warning", line=20)

        json_str = result.to_json()

        assert "test.qplug" in json_str
        assert "TEST001" in json_str
        assert "TEST002" in json_str

    def test_result_to_tap(self):
        """Result should format as TAP."""
        result = ValidationResult(plugin_path="test.qplug")
        result.add_error("TEST001", "Test error")

        tap = result.to_tap()

        assert "TAP version 14" in tap
        assert "not ok" in tap

    def test_errors_property(self):
        """Errors property should filter correctly."""
        result = ValidationResult(plugin_path="test.qplug")
        result.add_error("E1", "Error 1")
        result.add_warning("W1", "Warning 1")
        result.add_info("I1", "Info 1")

        assert len(result.errors) == 1
        assert len(result.warnings) == 1
        assert len(result.infos) == 1


class TestEngineHardening:
    """Regression tests for the 2026-06-02 engine audit fixes
    (see internal notes)."""

    HEADER = (
        'PluginInfo = {{ Name = "T~H", Version = "1.0.0", Id = {id} }}\n'
        "function GetProperties() return {{}} end\n"
        "function GetControls(props) return {{}} end\n"
    )

    def test_numeric_id_does_not_crash(self, validator):
        """P0-1: a numeric PluginInfo.Id must not crash the validator
        (previously raised TypeError in the QPDK004 block)."""
        code = self.HEADER.format(id="12345")
        result = validator.validate_string(code)  # must not raise
        # No internal-error warning should be emitted either.
        assert all("Internal validator error" not in i.message for i in result.issues)

    def test_numeric_name_does_not_crash(self, validator):
        code = (
            'PluginInfo = { Name = 42, Version = "1.0.0", Id = "com.t.h" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props) return {} end\n"
        )
        result = validator.validate_string(code)  # must not raise
        assert all("Internal validator error" not in i.message for i in result.issues)

    def test_log_warning_inside_string_is_not_flagged(self, validator):
        """P0-2: code-shaped text inside a string literal must not trip a
        code-pattern rule. QPDK020 is an ERROR — this was failing valid plugins."""
        code = (
            'PluginInfo = { Name = "T~H", Version = "1.0.0", Id = "com.t.h" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props) return {} end\n"
            "if Controls then\n"
            '  local doc = "Do not use Log.Warning(); use Log.Message()."\n'
            "  Log.Message(doc)\n"
            "end\n"
        )
        result = validator.validate_string(code)
        assert all(i.code != "QPDK020" for i in result.issues)
        assert result.valid

    def test_real_log_warning_still_flagged(self, validator):
        """The string-literal fix must not silence a genuine Log.Warning() call."""
        code = (
            'PluginInfo = { Name = "T~H", Version = "1.0.0", Id = "com.t.h" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props) return {} end\n"
            "if Controls then\n"
            '  Log.Warning("real call")\n'
            "end\n"
        )
        result = validator.validate_string(code)
        assert any(i.code == "QPDK020" for i in result.errors)

    def test_qpdk020_honors_suppression(self, validator):
        """P0-3: every rule must honor `-- qpdk:ignore`, including QPDK020
        which previously ignored it."""
        code = (
            'PluginInfo = { Name = "T~H", Version = "1.0.0", Id = "com.t.h" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props) return {} end\n"
            "if Controls then\n"
            '  Log.Warning("x") -- qpdk:ignore QPDK020\n'
            "end\n"
        )
        result = validator.validate_string(code)
        assert all(i.code != "QPDK020" for i in result.issues)

    def test_runtime_guard_not_masked_by_comment(self, validator):
        """P1-3: a comment containing 'if Controls then' must not suppress
        the QPDK025 missing-guard ERROR."""
        code = (
            'PluginInfo = { Name = "T~H", Version = "1.0.0", Id = "com.t.h" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props) return {} end\n"
            "-- runtime code should be wrapped in: if Controls then ... end\n"
            "myTimer = Timer.New()\n"
            "myTimer.EventHandler = function() end\n"
        )
        result = validator.validate_string(code)
        assert any(i.code == "QPDK025" for i in result.errors)

    def test_strip_noncode_blanks_strings_and_comments(self):
        """Unit test for the _strip_noncode primitive."""
        src = 'x = "a -- b"  -- real comment\ny = [[\nLog.Warning() ]]\nz = 1\n'
        out = PluginValidator._strip_noncode(src)
        # line/column count preserved
        assert len(out.split("\n")) == len(src.split("\n"))
        # code-shaped text inside string/comment is gone; real code remains
        assert "Log.Warning" not in out
        assert "real comment" not in out
        assert "z = 1" in out


class TestQPDK035GetControlsScope:
    """P1-1: QPDK035 must see PinStyle='Both' declared in GetControls (the common
    place), not only in GetControlLayout. Previously a latent false-negative."""

    def test_pinstyle_both_in_getcontrols_is_detected(self, validator):
        code = (
            'PluginInfo = { Name = "T~B", Version = "1.0.0", Id = "com.t.b" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props)\n"
            "  local ctls = {}\n"
            '  table.insert(ctls, { Name = "level", ControlType = "Knob", PinStyle = "Both" })\n'
            "  return ctls\n"
            "end\n"
            "function GetControlLayout(props) return {} end\n"
            "if Controls then\n"
            '  Controls["level"].Value = 5\n'
            "end\n"
        )
        result = validator.validate_string(code)
        assert any(i.code == "QPDK035" for i in result.warnings)

    def test_pinstyle_both_dot_notation_write_is_detected(self, validator):
        code = (
            'PluginInfo = { Name = "T~B", Version = "1.0.0", Id = "com.t.b" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props)\n"
            "  local ctls = {}\n"
            '  table.insert(ctls, { Name = "level", ControlType = "Knob", PinStyle = "Both" })\n'
            "  return ctls\n"
            "end\n"
            "if Controls then\n"
            "  Controls.level.Value = 5\n"
            "end\n"
        )
        result = validator.validate_string(code)
        assert any(i.code == "QPDK035" for i in result.warnings)

    def test_output_pin_not_flagged(self, validator):
        """Negative control: PinStyle='Output' must not fire QPDK035."""
        code = (
            'PluginInfo = { Name = "T~B", Version = "1.0.0", Id = "com.t.b" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props)\n"
            "  local ctls = {}\n"
            '  table.insert(ctls, { Name = "level", ControlType = "Knob", PinStyle = "Output" })\n'
            "  return ctls\n"
            "end\n"
            "if Controls then\n"
            "  Controls.level.Value = 5\n"
            "end\n"
        )
        result = validator.validate_string(code)
        assert all(i.code != "QPDK035" for i in result.issues)


class TestQPDK009VersionGating:
    """P1-2: QPDK009 must only flag version-ANNOTATED globals, not every
    global that happens to be absent from the hand-maintained V7 list."""

    BODY = (
        'PluginInfo = {{ Name = "T~V", Version = "1.0.0", Id = "com.t.v" }}\n'
        "function GetProperties() return {{}} end\n"
        "function GetControls(props) return {{}} end\n"
        "if Controls then\n"
        "  {use}\n"
        "end\n"
    )

    def test_pre_v9_api_not_flagged_under_v7(self):
        """json/Crypto predate v9 and carry no version annotation — no QPDK009."""
        v7 = PluginValidator(qsys_version="7")
        code = self.BODY.format(use="local d = json.encode({})")
        result = v7.validate_string(code)
        assert all(i.code != "QPDK009" for i in result.issues)

    def test_annotated_global_flagged_under_v7(self):
        """WebSocket is annotated (v9.5+) — QPDK009 should fire under v7."""
        v7 = PluginValidator(qsys_version="7")
        code = self.BODY.format(use="local ws = WebSocket.New()")
        result = v7.validate_string(code)
        assert any(i.code == "QPDK009" for i in result.warnings)

    def test_no_version_warning_under_v9(self):
        v9 = PluginValidator(qsys_version="9")
        code = self.BODY.format(use="local ws = WebSocket.New()")
        result = v9.validate_string(code)
        assert all(i.code != "QPDK009" for i in result.issues)


class TestQPDK008DuplicateId:
    """P1-4: duplicate-Id detection works across files on a shared validator."""

    def test_duplicate_id_flagged_on_second_file(self):
        validator = PluginValidator(qsys_version="9")
        code = (
            'PluginInfo = {{ Name = "T~D", Version = "1.0.0", Id = "com.dup.id" }}\n'
            "function GetProperties() return {{}} end\n"
            "function GetControls(props) return {{}} end\n"
        )
        first = validator.validate_string(code.format(), name="a.qplug")
        second = validator.validate_string(code.format(), name="b.qplug")
        assert all(i.code != "QPDK008" for i in first.issues)
        assert any(i.code == "QPDK008" for i in second.warnings)


class TestSeveritySingleSourceOfTruth:
    """S1: a rule must never emit a severity HOTTER than its RULE_METADATA
    severity. Guards against the QPDK031/032/037 INFO-vs-WARNING divergence
    (emit hotter than declared surprises --strict). A softer facet is allowed
    (e.g. QPDK028's recommended-handler INFO under a WARNING metadata)."""

    def test_emitted_severity_not_hotter_than_metadata(self):
        import glob
        from qpdk.validator.rulesets import get_rule_severity

        order = {IssueSeverity.INFO: 0, IssueSeverity.WARNING: 1, IssueSeverity.ERROR: 2}
        root = Path(__file__).parent.parent
        files = glob.glob(str(root / "tests" / "fixtures" / "*.qplug"))
        files += glob.glob(str(root / "examples" / "**" / "*.qplug"), recursive=True)
        too_hot = []
        for f in files:
            # Fresh validator per file (avoid cross-file QPDK008 state).
            res = PluginValidator(qsys_version="9", ruleset="full").validate_file(f)
            for issue in res.issues:
                meta = get_rule_severity(issue.code)
                if meta is not None and order[issue.severity] > order[meta]:
                    too_hot.append((Path(f).name, issue.code, issue.severity.value, meta.value))
        assert not too_hot, f"rules emitting hotter than metadata: {too_hot}"


class TestQPDK039PlaceholderStyles:
    """P2: QPDK039 must catch the common placeholder styles, and a `${...}`
    placeholder must not truncate PluginInfo parsing (phantom QPDK003)."""

    def _mk(self, version):
        return (
            f'PluginInfo = {{ Name = "T~P", Version = "{version}", Id = "com.t.p" }}\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props) return {} end\n"
        )

    @pytest.mark.parametrize("ph", ["__VERSION__", "${VERSION}", "@VERSION@", "{{VERSION}}"])
    def test_placeholder_styles_flagged(self, validator, ph):
        result = validator.validate_string(self._mk(ph))
        assert any(i.code == "QPDK039" for i in result.errors), f"{ph} not flagged"

    def test_dollar_placeholder_does_not_truncate_plugininfo(self, validator):
        """Previously '${VERSION}' (with its '}') truncated the PluginInfo table,
        producing phantom QPDK003 'missing field' errors for fields after it."""
        result = validator.validate_string(self._mk("${VERSION}"))
        # Id comes after Version; it must still be parsed (no phantom missing-Id).
        assert result.plugin_info is not None
        assert result.plugin_info.get("Id") == "com.t.p"
        missing_field = [e for e in result.errors if e.code == "QPDK003"]
        assert missing_field == [], f"phantom missing-field errors: {[e.message for e in missing_field]}"


class TestQPDK013DynamicLayoutKeys:
    """Round 3 (peer-reported): QPDK013 must recognize controls laid out via a
    dynamic concat key `layout["base " .. i]` even when the control has no Count,
    while still flagging genuine orphans (typo WARNING) and truly layout-less
    controls (INFO)."""

    def _controls(self, body_ctls, body_layout):
        return (
            'PluginInfo = { Name = "T~L", Version = "1.0.0", Id = "com.t.l" }\n'
            "function GetProperties() return {} end\n"
            "function GetControls(props)\n  local ctls = {}\n"
            f"{body_ctls}\n  return ctls\nend\n"
            "function GetControlLayout(props)\n  local layout = {}\n"
            f"{body_layout}\n  return layout\nend\n"
        )

    def test_dynamic_only_key_not_flagged(self, validator):
        code = self._controls(
            '  table.insert(ctls, { Name = "gauge", ControlType = "Text" })',
            '  layout["gauge " .. 1] = { Style = "Text" }',
        )
        result = validator.validate_string(code)
        assert all(i.code != "QPDK013" for i in result.issues), \
            [i.message for i in result.issues if i.code == "QPDK013"]

    def test_truly_unlaid_out_still_info(self, validator):
        code = self._controls(
            '  table.insert(ctls, { Name = "backend", ControlType = "Text" })',
            '  layout["other"] = { Style = "Text" }',
        )
        result = validator.validate_string(code)
        assert any(i.code == "QPDK013" and i.severity == IssueSeverity.INFO
                   for i in result.issues)

    def test_typo_key_still_warning(self, validator):
        code = self._controls(
            '  table.insert(ctls, { Name = "volume", ControlType = "Knob" })',
            '  layout["volme"] = { Style = "Fader" }',
        )
        result = validator.validate_string(code)
        assert any(i.code == "QPDK013" and i.severity == IssueSeverity.WARNING
                   for i in result.issues)

    # --- Round 4: FP guard 4 — PinStyle="Output"/"None" controls are
    #     panel-less by design, so a missing layout must NOT draw an INFO. ---

    def test_pinstyle_output_no_layout_not_flagged(self, validator):
        """An Output-pin backend control with no layout entry must NOT draw a
        QPDK013 INFO (it wires to the schematic, not the panel)."""
        code = self._controls(
            '  table.insert(ctls, { Name = "codec_last_error", '
            'ControlType = "Indicator", UserPin = true, PinStyle = "Output" })',
            '  layout["other"] = { Style = "Text" }',
        )
        result = validator.validate_string(code)
        assert all(i.code != "QPDK013" or i.message.find("codec_last_error") == -1
                   for i in result.issues), \
            [i.message for i in result.issues if i.code == "QPDK013"]

    def test_pinstyle_none_no_layout_not_flagged(self, validator):
        """A PinStyle="None" pure-backend control with no layout entry must NOT
        draw a QPDK013 INFO."""
        code = self._controls(
            '  table.insert(ctls, { Name = "internal_state", '
            'ControlType = "Text", PinStyle = "None" })',
            '  layout["other"] = { Style = "Text" }',
        )
        result = validator.validate_string(code)
        assert all(i.code != "QPDK013" or i.message.find("internal_state") == -1
                   for i in result.issues), \
            [i.message for i in result.issues if i.code == "QPDK013"]

    def test_pinstyle_input_no_layout_still_info(self, validator):
        """A non-Output/None control (here PinStyle="Input") with no layout
        still draws the INFO — guard 4 must not over-suppress."""
        code = self._controls(
            '  table.insert(ctls, { Name = "trigger_in", '
            'ControlType = "Button", PinStyle = "Input" })',
            '  layout["other"] = { Style = "Button" }',
        )
        result = validator.validate_string(code)
        assert any(i.code == "QPDK013" and i.severity == IssueSeverity.INFO
                   and "trigger_in" in i.message for i in result.issues)


class TestRuleCatalogue:
    """B10/A-6: the catalogue lists only live rules, includes QPDK000, and
    marks the rules that run under design-check rather than validate."""

    def test_retired_codes_not_in_catalogue(self):
        from qpdk.validator.rulesets import RETIRED_RULES, RULE_METADATA
        assert {"QPDK006", "QPDK007"} <= set(RETIRED_RULES)
        assert not set(RETIRED_RULES) & set(RULE_METADATA)

    def test_qpdk000_catalogued_as_error(self):
        from qpdk.validator.rulesets import RULE_METADATA
        assert RULE_METADATA["QPDK000"]["severity"] == IssueSeverity.ERROR

    def test_design_check_rules_labelled(self):
        from qpdk.validator.rulesets import DESIGN_CHECK_RULES, RULE_METADATA
        assert DESIGN_CHECK_RULES == {f"QPDK04{i}" for i in range(7)}
        for code, meta in RULE_METADATA.items():
            labelled = meta["description"].startswith("design-check only:")
            assert labelled == (code in DESIGN_CHECK_RULES), code

    def test_live_rule_count(self):
        from qpdk.validator.rulesets import DESIGN_CHECK_RULES, RULE_METADATA
        assert len(RULE_METADATA) == 41
        assert len(RULE_METADATA) - len(DESIGN_CHECK_RULES) == 34


class TestCrashedRuleIsError:
    """D-2: a rule that crashes must fail the plugin (QPDK000 ERROR), and an
    unexpected exception in design-time evaluation must not silently turn
    QPDK049/050 off."""

    _SRC = (
        'PluginInfo = { Name = "T~Crash", Version = "1.0.0", Id = "t.crash" }\n'
        "function GetProperties() return {} end\n"
        'function GetControls() return { { Name = "a", ControlType = "Button" } } end\n'
        "function GetControlLayout() return {}, {} end\n"
    )

    def test_crashed_rule_makes_plugin_invalid(self, monkeypatch):
        def boom(self, content, result):
            raise TypeError("synthetic")
        monkeypatch.setattr(PluginValidator, "_validate_control_layout_coupling", boom)
        r = PluginValidator().validate_string(self._SRC)
        hits = [i for i in r.errors if i.code == "QPDK000"]
        assert hits and "synthetic" in hits[0].message
        assert not r.valid

    def test_evaluate_does_not_swallow_unexpected_exceptions(self, monkeypatch):
        from qpdk.mock_runtime import runtime
        from qpdk.validator import control_refs

        def boom(self, *a, **k):
            raise RuntimeError("evaluator bug")
        monkeypatch.setattr(runtime.QSYSRuntime, "run_design_time", boom)
        with pytest.raises(RuntimeError, match="evaluator bug"):
            control_refs._evaluate(self._SRC)
        r = PluginValidator().validate_string(self._SRC)
        assert any(i.code == "QPDK000" and "evaluator bug" in i.message for i in r.errors)
        assert not r.valid
