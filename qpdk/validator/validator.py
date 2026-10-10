"""
Q-SYS Plugin Validator

Comprehensive validation for Q-SYS .qplug files including:
- Lua syntax validation
- Plugin structure validation
- Semantic checks (Timer scoping, etc.)
- Version-specific API validation
- P2 quality and safety rules (QPDK022-035)
"""

import re
import subprocess
from pathlib import Path
from typing import Any

from .qsys_globals import QSYS_GLOBALS_V7, QSYS_GLOBALS_V9, REQUIRED_PLUGIN_FIELDS
from .result import ValidationResult
from .rulesets import get_ruleset


class PluginValidator:
    """Validates Q-SYS plugin files."""

    # Error codes
    SYNTAX_ERROR = "QPDK001"
    MISSING_PLUGININFO = "QPDK002"
    MISSING_FIELD = "QPDK003"
    INVALID_FIELD = "QPDK004"
    TIMER_SCOPING = "QPDK005"
    # QPDK006/QPDK007 are retired (luacheck W1xx / W211/W311 own those checks);
    # see RETIRED_RULES in rulesets.py. The codes are never reused.
    DUPLICATE_PLUGIN_ID = "QPDK008"
    VERSION_API = "QPDK009"
    CONTROL_NAMING = "QPDK010"
    TRIGGER_INPUT_PIN = "QPDK011"      # F5 — Button Trigger + PinStyle Input/Both (hedged WARNING)
    DESIGN_TIME_RUNTIME_GLOBAL = "QPDK012"  # runtime-only global indexed in a design-time GetX body (ERROR Controls / WARNING others)
    CONTROL_LAYOUT_COUPLING = "QPDK013"  # F6 — GetControls<->GetControlLayout key coupling (WARNING/INFO)
    LOG_API = "QPDK020"
    RAPIDJSON_REQUIRE = "QPDK021"
    TONUMBER_NIL_GUARD = "QPDK022"
    STATUS_VALUE_RANGE = "QPDK023"
    RESERVED_NAME_SHADOW = "QPDK024"
    RUNTIME_GUARD = "QPDK025"
    MISSING_CONTROL_LAYOUT = "QPDK026"
    DUPLICATE_PROPERTY = "QPDK027"
    SSH_HANDLERS = "QPDK028"
    SSH_WRITE_PCALL = "QPDK029"
    TIMER_MISSING_STOP = "QPDK031"
    POLL_CONNECTION_GUARD = "QPDK032"
    XAPI_INJECTION = "QPDK033"
    MONOLITHIC_FUNCTION = "QPDK034"
    PINSTYLE_BOTH_WRITE = "QPDK035"
    DUAL_WRITE_INDICATOR = "QPDK036"
    MONITOR_MODE_GUARD = "QPDK037"
    # QPDK038 (REFLECT_STATUSGP) held back — unverified framework claim. See rulesets.py.
    BUILD_TEMPLATE_UNSUBSTITUTED = "QPDK039"
    PLUGININFO_MANUFACTURER = "QPDK047"  # advisory: Manufacturer not set (managed-submission convention)
    QPLUGX_STRUCTURE = "QPDK048"  # encrypted .qplugx/.qplugx2 artifact structural check
    UNDECLARED_CONTROL = "QPDK049"  # Controls ref to a name GetControls never declares (nil on a Core)
    SCALAR_AS_ARRAY = "QPDK050"     # Count=1 control used with #, [i] or ipairs (userdata, not a table)

    # Build-time template placeholders. Source files (e.g. plugin_info.lua)
    # may contain these tokens; build pipelines must substitute them with
    # real values before producing the .qplug artifact. If a placeholder
    # reaches QPDK validation, the build pipeline failed — emit QPDK039.
    # Covers the common substitution styles: __FOO__, @FOO@, ${FOO}, {{FOO}}.
    _BUILD_TEMPLATE_PATTERN = re.compile(
        r"^(?:"
        r"__[A-Za-z][A-Za-z0-9_]*__"          # __VERSION__
        r"|@[A-Za-z][A-Za-z0-9_]*@"            # @VERSION@
        r"|\$\{[A-Za-z][A-Za-z0-9_.]*\}"       # ${VERSION}
        r"|\{\{\s*[A-Za-z][A-Za-z0-9_.]*\s*\}\}"  # {{VERSION}}
        r")$"
    )

    # Library object constructors subject to GC when stored as local
    _LIBRARY_CONSTRUCTORS = [
        "Timer.New",
        "Timer.Create",
        "Ssh.New",
        "TcpSocket.New",
        "Component.New",
        "UdpSocket.New",
    ]

    # SSH mandatory lifecycle handlers (missing any = silent failure)
    _SSH_HANDLERS_MANDATORY = [
        "Connected", "Reconnect", "Closed", "Error", "Timeout", "LoginFailed"
    ]
    # SSH recommended handlers (missing = INFO)
    _SSH_HANDLERS_RECOMMENDED = ["Data"]

    # Reserved Q-SYS global names. A local with exactly one of these names
    # (Lua names are case-sensitive) shadows the Q-SYS API for its scope.
    _RESERVED_NAMES = [
        "Log", "Timer", "Controls", "Properties", "System", "Design",
        "Ssh", "TcpSocket", "UdpSocket", "HttpClient", "Component", "NamedControl",
        "Uci", "Snapshot", "Mixer", "Audio", "SerialPorts", "Gpio", "Notifications",
    ]

    def __init__(self, qsys_version: str = "9", ruleset: str = "full"):
        """Initialize the validator.

        Args:
            qsys_version: Target Q-SYS version ("7" or "9").
            ruleset: Which ruleset to use ("P0", "default", or "full").
                     Default is "full" for backwards compatibility (runs all rules).
        """
        self.qsys_version = qsys_version
        self.globals = QSYS_GLOBALS_V9 if qsys_version == "9" else QSYS_GLOBALS_V7
        self.active_rules: set[str] = get_ruleset(ruleset)
        # Cross-file state: maps plugin Id -> plugin_path for duplicate detection
        self._seen_plugin_ids: dict[str, str] = {}

    def _is_rule_active(self, code: str) -> bool:
        """Check if a rule is active in the current ruleset."""
        return code in self.active_rules

    @staticmethod
    def _strip_block_comments(content: str) -> str:
        """DEPRECATED / no longer used by the engine (F1, 2026-06-02).

        This was a string-UNAWARE block-comment stripper: a ``--[[`` appearing
        inside a string literal was misread as a comment opener, blanking real
        code until ``]]`` and producing an un-suppressible phantom QPDK001.
        Block-comment handling is now folded into the string-aware
        :meth:`_strip_noncode` single pass. Retained only so any external caller
        does not break; prefer ``_strip_noncode``.

        Handles ``--[[ ... ]]`` and ``--[=[ ... ]=]`` with any number of ``=``.
        Lines inside block comments are replaced with empty strings so that
        line numbers in all downstream validators remain correct.
        """
        result_lines = content.split("\n")
        in_block = False
        close_pattern = None

        for i, line in enumerate(result_lines):
            if in_block:
                if close_pattern and close_pattern.search(line):
                    # Close tag found — blank this line too (it's still comment)
                    result_lines[i] = ""
                    in_block = False
                    close_pattern = None
                else:
                    result_lines[i] = ""
            else:
                # Check for block comment open: --[[ or --[=[ or --[==[ etc.
                m = re.search(r'--\[(=*)\[', line)
                if m:
                    equals = m.group(1)
                    close_re = re.compile(r'\]' + re.escape(equals) + r'\]')
                    # Check if close is on same line
                    after_open = line[m.end():]
                    if close_re.search(after_open):
                        # Single-line block comment — blank entire line
                        result_lines[i] = ""
                    else:
                        # Multi-line block comment starts here
                        result_lines[i] = ""
                        in_block = True
                        close_pattern = close_re

        return "\n".join(result_lines)

    @staticmethod
    def _strip_noncode(content: str, keep_comments: bool = False) -> str:
        """Blank string-literal interiors AND all comments (line + block) in a
        single string-aware pass, preserving line and column layout, so
        code-pattern rules cannot match text inside strings or comments.

        Handles short strings (``"..."`` / ``'...'``) with backslash escapes,
        Lua long strings (``[[ ]]``, ``[=[ ]=]``), line comments (``-- ...``)
        and block comments (``--[[ ... ]]``, ``--[=[ ... ]=]``). Because the
        scanner tracks string state, a ``--[[`` *inside* a string literal is NOT
        treated as a block-comment opener (the F1 fix — the old string-unaware
        ``_strip_block_comments`` pre-pass blanked real code after such a
        ``--[[``, producing an un-suppressible phantom QPDK001). Characters are
        replaced with spaces (newlines kept) so all downstream line/column
        numbers stay correct.

        ``keep_comments=True`` blanks only string interiors and leaves comment
        text in place: suppression directives are read from that view, so a
        ``-- qpdk:ignore`` inside a string literal is never honored.

        Heuristic limit: a ``[[`` in an indexing context (e.g. ``t[[i]]``) is
        treated as a long-string open. This is rare in real plugin code.
        """
        out: list[str] = []
        i, n = 0, len(content)
        state = "code"   # code | short | long | line_comment | block_comment
        quote = ""       # active short-string delimiter
        long_level = 0   # number of '=' in the active long bracket
        block_level = 0  # number of '=' in the active block-comment bracket
        while i < n:
            ch = content[i]
            if state == "code":
                if ch == "\n":
                    out.append("\n")
                    i += 1
                elif ch == "-" and i + 1 < n and content[i + 1] == "-":
                    # Distinguish a block comment (--[[ / --[=[) from a line
                    # comment. The long-bracket check happens HERE, in code
                    # state, so a `--[[` inside a string never reaches this arm.
                    bm = re.match(r"--\[(=*)\[", content[i:])
                    if bm:
                        block_level = len(bm.group(1))
                        out.append(bm.group(0) if keep_comments else " " * len(bm.group(0)))
                        i += bm.end()
                        state = "block_comment"
                    else:
                        state = "line_comment"
                        out.append("--" if keep_comments else "  ")
                        i += 2
                elif ch in ('"', "'"):
                    state = "short"
                    quote = ch
                    out.append(ch)
                    i += 1
                elif ch == "[" and i + 1 < n and content[i + 1] in "[=":
                    m = re.match(r"\[(=*)\[", content[i:])
                    if m:
                        long_level = len(m.group(1))
                        out.append(m.group(0))
                        i += m.end()
                        state = "long"
                    else:
                        out.append(ch)
                        i += 1
                else:
                    out.append(ch)
                    i += 1
            elif state == "line_comment":
                if ch == "\n":
                    out.append("\n")
                    state = "code"
                else:
                    out.append(ch if keep_comments else " ")
                i += 1
            elif state == "block_comment":
                closer = "]" + ("=" * block_level) + "]"
                if content.startswith(closer, i):
                    out.append(closer if keep_comments else " " * len(closer))
                    i += len(closer)
                    state = "code"
                elif ch == "\n":
                    out.append("\n")
                    i += 1
                else:
                    out.append(ch if keep_comments else " ")
                    i += 1
            elif state == "short":
                if ch == "\\" and i + 1 < n:
                    out.append(" ")
                    out.append("\n" if content[i + 1] == "\n" else " ")
                    i += 2
                elif ch == quote:
                    out.append(ch)
                    state = "code"
                    i += 1
                elif ch == "\n":
                    # unterminated short string — Lua would error; recover at EOL
                    out.append("\n")
                    state = "code"
                    i += 1
                else:
                    out.append(" ")
                    i += 1
            else:  # long string
                closer = "]" + ("=" * long_level) + "]"
                if content.startswith(closer, i):
                    out.append(closer)
                    i += len(closer)
                    state = "code"
                elif ch == "\n":
                    out.append("\n")
                    i += 1
                else:
                    out.append(" ")
                    i += 1
        return "".join(out)

    @staticmethod
    def _is_suppressed(line: str, code: str) -> bool:
        """Check if a line has a qpdk:ignore suppression comment for the given rule code.

        Supports ``-- qpdk:ignore QPDK029`` and multi-code
        ``-- qpdk:ignore QPDK029, QPDK022`` forms, plus LUACHECK codes. Only
        code-shaped tokens are honored, so a free-text reason after the codes
        (``-- qpdk:ignore QPDK029 reconnect is guarded``) is ignored cleanly.
        """
        match = re.search(r'--\s*qpdk:ignore\s+(.+)', line)
        if not match:
            return False
        tokens = re.findall(r'(?:QPDK\d+|LUACHECK-[EW]?\d+)', match.group(1))
        return code in tokens

    @staticmethod
    def _is_unsuppressible(code: str) -> bool:
        """QPDK001 and luacheck E-codes are compile failures, never suppressible."""
        return code == "QPDK001" or code.startswith("LUACHECK-E")

    def _apply_suppressions(self, original_content: str, result: ValidationResult) -> None:
        """Drop issues whose source line carries a matching ``-- qpdk:ignore <CODE>``.

        Centralized so EVERY rule honors suppression uniformly — including rules
        that scan a string/comment-blanked view (where the directive is invisible)
        and rules that never self-checked. Line-scoped; file-level issues
        (``line is None``) are not suppressible this way. Validity is recomputed
        after filtering so suppressing the last error makes the plugin valid.
        """
        # Directives are read from a strings-blanked view, so only real Lua
        # comments can suppress. Syntax errors are never suppressible: code that
        # does not compile cannot be waved through by a comment.
        lines = self._strip_noncode(original_content, keep_comments=True).split("\n")
        result.issues = [
            issue for issue in result.issues
            if not (
                issue.line is not None
                and not self._is_unsuppressible(issue.code)
                and 1 <= issue.line <= len(lines)
                and self._is_suppressed(lines[issue.line - 1], issue.code)
            )
        ]
        result.valid = not result.errors

    def _run_all_validators(self, content: str, result: ValidationResult) -> None:
        """Run all content-based validators filtered by active ruleset.

        Each validator method is dispatched only if its corresponding rule code
        is in the active ruleset. Structural validators (QPDK001-003) always run
        because other rules depend on their output (e.g., PluginInfo parsing).
        """
        # F1: block-comment handling is now folded into the string-aware
        # _strip_noncode (single pass). The old string-unaware
        # _strip_block_comments pre-pass blanked real code after a `--[[`
        # appearing INSIDE a string literal, producing an un-suppressible
        # phantom QPDK001. We no longer pre-strip the raw content.
        #
        # Keep the raw content reachable for rules that legitimately need to see
        # string values (the _needs_raw_strings opt-out below) and for QPDK021's
        # require()-argument detection.
        self._raw_content = content
        self._control_ctx = None  # QPDK049/050 design-time evaluation, per run

        # "Code-only" view: line comments, block comments AND string-literal
        # interiors blanked (length/lines preserved). This is now the DEFAULT
        # view for pattern-scanning rules (F2 dispatch inversion) so code-shaped
        # text inside string literals or comments cannot produce false positives
        # (e.g. QPDK020 firing on "use Log.Warning()" in a help string, QPDK024
        # on "local Timer" in a help string, QPDK023 on "status.Value = 9" in a
        # string).
        code_content = self._strip_noncode(content)

        def _safe(method_name: str, view: str) -> None:
            """Run a validator method, turning any internal crash into a
            QPDK000 ERROR. One buggy rule must not abort the whole run, but a
            build gate must not pass a plugin a rule never finished checking."""
            try:
                getattr(self, method_name)(view, result)
            except Exception as e:
                result.add_error(
                    "QPDK000",
                    f"Internal validator error in {method_name}: {type(e).__name__}: {e} "
                    "(analysis incomplete; please report this as a QPDK bug)",
                )

        # F2 OPT-OUT: rules that read string-literal VALUES (not just code
        # patterns) must see the RAW content. Everything else defaults to the
        # string/comment-blanked code_content view.
        _needs_raw_strings = {
            "_validate_control_naming",        # QPDK010 — reads Name="..." values
            "_validate_duplicate_plugin_id",   # QPDK008 — locates the Id="..." declaration line
            "_validate_duplicate_property",    # QPDK027 — reads property Name="..." values
            "_validate_pinstyle_both_write",   # QPDK035 — reads Name/PinStyle="Both" values
            "_validate_dual_write_indicator",  # QPDK036 — reads Name/ControlType="Indicator" values
            "_validate_monitor_mode_guard",    # QPDK037 — reads sendData "xStatus..." arg prefix
            "_validate_trigger_input_pin",     # QPDK011 — reads ButtonType/PinStyle="..." values
            "_validate_control_layout_coupling",  # QPDK013 — reads control Name + layout["name"] keys
            "_validate_undeclared_controls",      # QPDK049 — reads Controls["name"] literals
            "_validate_scalar_as_array",          # QPDK050 — reads Controls["name"] literals
            # QPDK021 (_validate_rapidjson) is special: it scans the code view for
            # bare `rapidjson` usage (so a mention in a Description string does NOT
            # trigger) but checks require("rapidjson") against self._raw_content.
            # QPDK023 (_validate_status_value_range) is special the other way:
            # it scans the CODE view for `.Value = N` (so in-string assignments
            # are ignored) but checks the "status" context word against the raw
            # line, because the control name is itself a string value.
        }

        # Structural validators always run on RAW content (other rules depend on
        # their output; they read PluginInfo field/Name string values).
        _safe("_validate_lua_syntax", content)        # F1: lupa parses comments/strings itself
        _safe("_validate_plugin_info", content)
        _safe("_validate_plugin_structure", content)

        # Dispatch remaining validators based on active ruleset
        # Using a list of (code, method_name) to preserve execution order
        _optional_validators = [
            ("QPDK008", "_validate_duplicate_plugin_id"),
            ("QPDK009", "_validate_version_api"),
            ("QPDK005", "_validate_timer_scoping"),
            ("QPDK010", "_validate_control_naming"),
            ("QPDK011", "_validate_trigger_input_pin"),
            ("QPDK012", "_validate_design_time_runtime_global"),
            ("QPDK013", "_validate_control_layout_coupling"),
            ("QPDK020", "_validate_log_api"),
            ("QPDK021", "_validate_rapidjson"),
            ("QPDK025", "_validate_runtime_guard"),
            ("QPDK028", "_validate_ssh_handlers"),
            ("QPDK022", "_validate_tonumber_nil_guard"),
            ("QPDK023", "_validate_status_value_range"),
            ("QPDK024", "_validate_reserved_name_shadow"),
            ("QPDK026", "_validate_missing_control_layout"),
            ("QPDK027", "_validate_duplicate_property"),
            ("QPDK029", "_validate_ssh_write_pcall"),
            ("QPDK031", "_validate_timer_missing_stop"),
            ("QPDK032", "_validate_poll_connection_guard"),
            ("QPDK033", "_validate_xapi_injection"),
            ("QPDK034", "_validate_monolithic_function"),
            ("QPDK035", "_validate_pinstyle_both_write"),
            ("QPDK036", "_validate_dual_write_indicator"),
            ("QPDK037", "_validate_monitor_mode_guard"),
            ("QPDK049", "_validate_undeclared_controls"),
            ("QPDK050", "_validate_scalar_as_array"),
        ]
        for code, method_name in _optional_validators:
            if self._is_rule_active(code):
                view = content if method_name in _needs_raw_strings else code_content
                _safe(method_name, view)

    def validate_file(self, path: str | Path) -> ValidationResult:
        """Validate a plugin file."""
        path = Path(path)
        result = ValidationResult(plugin_path=str(path))

        if not path.exists():
            result.add_error(self.SYNTAX_ERROR, f"File not found: {path}")
            return result

        # Encrypted artifacts are not Lua — validate the envelope structure and
        # return. Reading a .qplugx as Lua text produced garbage Lua errors.
        if path.suffix in (".qplugx", ".qplugx2"):
            self._validate_qplugx_structure(path, result)
            return result

        if path.suffix != ".qplug":
            result.add_warning(self.INVALID_FIELD, f"Expected .qplug extension, got {path.suffix}")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = path.read_text(encoding="latin-1")
            except Exception as e:
                result.add_error(self.SYNTAX_ERROR, f"Cannot read file: {e}")
                return result

        # Run all validations
        self._run_all_validators(content, result)
        self._run_luacheck(path, result)

        # Centralized suppression pass (covers qpdk + luacheck issues)
        self._apply_suppressions(content, result)

        return result

    def validate_string(self, content: str, name: str = "<string>") -> ValidationResult:
        """Validate plugin content from a string."""
        result = ValidationResult(plugin_path=name)

        self._run_all_validators(content, result)
        self._apply_suppressions(content, result)

        return result

    def _validate_lua_syntax(self, content: str, result: ValidationResult) -> None:
        """Check for basic Lua syntax errors using lupa.

        Compiles under Lua 5.3 — the version Q-SYS Cores run — like the mock
        runtime does. lupa's DEFAULT runtime is whatever the newest bundled
        Lua is (5.4+), whose semantics diverge both ways: it rejects legal
        5.3 code (assigning a for-in control variable — false QPDK001 on the
        cisco plugin in CI, 2026-07-06) and accepts 5.4-only syntax like
        `<close>` that the Core cannot load (false negative).
        """
        try:
            try:
                from lupa.lua53 import LuaRuntime
            except ImportError:
                # Degrade loudly: a non-5.3 compile is version-skewed.
                from lupa import LuaRuntime
                result.add_info(
                    "QPDK000",
                    "lupa has no lua53 build; QPDK001 compiled under a "
                    "different Lua version than the Q-SYS Core (5.3) — "
                    "results may be version-skewed.",
                )
            lua = LuaRuntime(unpack_returned_tuples=True)

            # Try to compile (not execute) the code
            try:
                lua.compile(content)
            except Exception as e:
                error_msg = str(e)
                # Extract line number from Lua error
                line_match = re.search(r":(\d+):", error_msg)
                line = int(line_match.group(1)) if line_match else None
                result.add_error(self.SYNTAX_ERROR, f"Lua syntax error: {error_msg}", line=line)
        except ImportError:
            result.add_info("QPDK000", "lupa not installed, skipping Lua syntax check")
        except Exception as e:
            result.add_error(self.SYNTAX_ERROR, f"Unexpected error during syntax check: {e}")

    @staticmethod
    def _extract_table_body(content: str, name: str) -> str | None:
        """Return the inner text of ``name = { ... }`` using brace balancing that
        is aware of strings and comments.

        The previous ``name\\s*=\\s*\\{([^}]+)\\}`` regex stopped at the FIRST
        ``}``, so any ``}`` inside a string value (e.g. ``Version="${V}"``)
        truncated the table and produced phantom "missing field" errors. Here we
        balance braces on a string/comment-blanked copy, then slice the matching
        span out of the ORIGINAL content so real string values are preserved.
        Returns None if the table is not found.
        """
        masked = PluginValidator._strip_noncode(content)
        m = re.search(rf"\b{name}\s*=\s*\{{", masked)
        if not m:
            return None
        open_idx = m.end() - 1  # index of the opening brace
        depth = 0
        for j in range(open_idx, len(masked)):
            c = masked[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return content[open_idx + 1:j]
        return None  # unbalanced — treat as not found

    def _validate_plugin_info(self, content: str, result: ValidationResult) -> None:
        """Validate PluginInfo structure."""
        # Find PluginInfo table (brace-balanced, string/comment-aware)
        info_content = self._extract_table_body(content, "PluginInfo")

        if info_content is None:
            result.add_error(self.MISSING_PLUGININFO, "Missing PluginInfo table")
            return

        plugin_info: dict[str, Any] = {}

        # Extract fields (handles Name = "value" and Name = value patterns)
        field_pattern = r'(\w+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\d+(?:\.\d+)?)|(\w+))'
        for field_match in re.finditer(field_pattern, info_content):
            key = field_match.group(1)
            value = (
                field_match.group(2) or  # double-quoted string
                field_match.group(3) or  # single-quoted string
                field_match.group(4) or  # number
                field_match.group(5)     # unquoted identifier
            )
            if value is not None:
                # Convert boolean strings
                if value == "true":
                    value = True
                elif value == "false":
                    value = False
                elif field_match.group(4):  # was a number
                    try:
                        value = float(value) if "." in value else int(value)
                    except ValueError:
                        pass
                plugin_info[key] = value

        result.plugin_info = plugin_info

        # Check required fields
        for field, required in REQUIRED_PLUGIN_FIELDS.items():
            if required and field not in plugin_info:
                result.add_error(self.MISSING_FIELD, f"Missing required PluginInfo field: {field}")

        # QPDK039 — unsubstituted build-template placeholders. Run BEFORE the
        # field-shape checks so we don't follow up a "didn't substitute" error
        # with a noisy "version isn't semantic" complaint about the placeholder.
        # QPDK039 is always-active (phase 1, ERROR) — a placeholder reaching
        # validation means the build pipeline failed and the artifact is broken.
        if self._is_rule_active("QPDK039"):
            for field in ("Version", "BuildVersion", "Name", "Id", "Author", "Description"):
                if field in plugin_info:
                    raw = str(plugin_info[field])
                    if self._BUILD_TEMPLATE_PATTERN.match(raw):
                        result.add_error(
                            self.BUILD_TEMPLATE_UNSUBSTITUTED,
                            f"Unsubstituted build-template placeholder in PluginInfo.{field}: "
                            f"'{raw}' — build pipeline must replace this token before producing the .qplug",
                        )

        # Validate specific fields (QPDK004 — only emit if rule is active).
        # All field values are coerced to str() first: the PluginInfo parser
        # converts numeric/boolean literals to int/float/bool, and running
        # string ops (`in`, re.match) on those raised an unhandled TypeError
        # that crashed the entire validator (see internal notes).
        if self._is_rule_active("QPDK004"):
            if "Name" in plugin_info:
                name = str(plugin_info["Name"])
                if "~" not in name and not self._BUILD_TEMPLATE_PATTERN.match(name):
                    result.add_warning(
                        self.INVALID_FIELD,
                        f"Plugin Name should use ~ for category: '{name}' (e.g., 'Category~Name')"
                    )

            if "Id" in plugin_info:
                plugin_id = str(plugin_info["Id"])
                if not self._BUILD_TEMPLATE_PATTERN.match(plugin_id) and not re.match(r"^[a-z0-9._-]+$", plugin_id, re.IGNORECASE):
                    result.add_warning(
                        self.INVALID_FIELD,
                        f"Plugin Id should be alphanumeric with dots/dashes: '{plugin_id}'"
                    )

            if "Version" in plugin_info:
                version = str(plugin_info["Version"])
                # Skip semantic-format check on placeholders — QPDK039 already
                # surfaced the substitution failure as an ERROR.
                # F3: accept 2–4 numeric parts (\d+\.\d+(\.\d+){0,2}). Four-part
                # versions (e.g. "2.5.4.8") are standard for these plugins —
                # QSC's own examples use BuildVersion="0.0.0.1". The old 2–3
                # part regex warned on every 4-part Version (a false positive).
                if not self._BUILD_TEMPLATE_PATTERN.match(version) and not re.match(r"^\d+\.\d+(\.\d+){0,2}$", version):
                    result.add_warning(
                        self.INVALID_FIELD,
                        f"Version should be semantic (e.g., '1.0.0' or '1.0.0.1'): '{version}'"
                    )

        # QPDK047 — Manufacturer advisory (INFO, full ruleset only). QSC's own
        # evaluator warns when PluginInfo.Manufacturer is absent; it identifies
        # the vendor in Q-SYS Library / Asset Manager. The vendor corpus shows
        # 0/19 real plugins set it, so this is INFO, not WARNING.
        if self._is_rule_active("QPDK047"):
            if "Manufacturer" not in plugin_info:
                result.add_info(
                    self.PLUGININFO_MANUFACTURER,
                    "PluginInfo.Manufacturer not set — recommended for Q-SYS Library / "
                    "Asset Manager vendor identification (managed-submission convention)"
                )

    def _validate_duplicate_plugin_id(self, content: str, result: ValidationResult) -> None:
        """Check for duplicate plugin IDs across validations on the same validator instance.

        Tracks every PluginInfo.Id seen by this validator. When the same Id
        appears in a subsequent validation, a QPDK008 warning is emitted on
        the second (and any later) occurrence.
        """
        if not result.plugin_info or "Id" not in result.plugin_info:
            return

        plugin_id = str(result.plugin_info["Id"])

        # Find the line number where Id is declared. The Id is a string VALUE,
        # which is blanked in the code_content view this rule receives, so scan
        # the raw content for the declaration line (F2).
        line_num = None
        raw = getattr(self, "_raw_content", content)
        for i, line in enumerate(raw.split("\n"), 1):
            if re.search(r'\bId\s*=\s*["\']' + re.escape(plugin_id), line):
                line_num = i
                break

        if plugin_id in self._seen_plugin_ids:
            first_seen = self._seen_plugin_ids[plugin_id]
            result.add_warning(
                self.DUPLICATE_PLUGIN_ID,
                f"Duplicate plugin Id '{plugin_id}' — first seen in {first_seen}",
                line=line_num,
            )
        else:
            self._seen_plugin_ids[plugin_id] = result.plugin_path

    def _validate_version_api(self, content: str, result: ValidationResult) -> None:
        """Flag usage of globals that don't exist in the target Q-SYS version.

        Compares the full v9 globals set against the target version's globals.
        Any v9-only global referenced in the plugin code triggers QPDK009.
        """
        # Only gate globals that carry an explicit version annotation in their
        # description (e.g. "(v9.5+)"). The previous approach flagged the entire
        # set(V9) - set(V7) difference, which is an artifact of the hand-maintained
        # V7 list: it produced false positives for APIs that long predate v9
        # (json, Crypto, Audio, dir, ...). Gating on the annotation keeps the rule
        # honest and auditable. See internal notes
        target_globals = set(self.globals.keys())
        _version_marker = re.compile(r"\(v9[\d.]*\+?\)", re.IGNORECASE)
        v9_only = {
            name for name, desc in QSYS_GLOBALS_V9.items()
            if name not in target_globals and _version_marker.search(desc)
        }

        if not v9_only:
            return  # Target is v9 (or superset), or no version-annotated globals

        # Pre-compile patterns for each v9-only global
        patterns = {
            name: re.compile(r'\b' + re.escape(name) + r'\b')
            for name in v9_only
        }

        # Track which globals we've already flagged (report first occurrence only)
        flagged: set[str] = set()

        lines = content.split("\n")
        for line_num, line in enumerate(lines, 1):
            stripped = line.lstrip()
            # Skip full-line comments
            if stripped.startswith("--"):
                continue
            # Strip inline comments
            code_part = line.split("--")[0]

            for global_name, pattern in patterns.items():
                if global_name in flagged:
                    continue
                if pattern.search(code_part):
                    desc = QSYS_GLOBALS_V9.get(global_name, "")
                    result.add_warning(
                        self.VERSION_API,
                        f"'{global_name}' is annotated for a newer Q-SYS version and "
                        f"may not be available in v{self.qsys_version} — {desc}",
                        line=line_num,
                    )
                    flagged.add(global_name)

    def _validate_plugin_structure(self, content: str, result: ValidationResult) -> None:
        """Validate plugin structure functions."""
        # Check for required functions
        required_functions = ["GetProperties", "GetControls"]

        for func in required_functions:
            if not re.search(rf"function\s+{func}\s*\(", content):
                result.add_error(
                    self.MISSING_FIELD,
                    f"Missing required function: {func}()"
                )

        # Count properties, controls, pages
        props_match = re.search(r"function\s+GetProperties\s*\([^)]*\)(.*?)(?=\nfunction|\nend\s*$)",
                                content, re.DOTALL)
        if props_match:
            result.properties_count = len(re.findall(r'table\.insert\s*\(\s*props', props_match.group(1)))

        controls_match = re.search(r"function\s+GetControls\s*\([^)]*\)(.*?)(?=\nfunction|\nend\s*$)",
                                   content, re.DOTALL)
        if controls_match:
            result.controls_count = len(re.findall(r'table\.insert\s*\(\s*ctls', controls_match.group(1)))
            result.controls_count += len(re.findall(r'\{\s*Name\s*=', controls_match.group(1)))

        pages_match = re.search(r"function\s+GetPages\s*\([^)]*\)(.*?)(?=\nfunction|\nend\s*$)",
                                content, re.DOTALL)
        if pages_match:
            result.pages_count = len(re.findall(r'table\.insert\s*\(\s*pages', pages_match.group(1)))
            result.pages_count += len(re.findall(r'\{\s*name\s*=', pages_match.group(1), re.IGNORECASE))

    def _validate_timer_scoping(self, content: str, result: ValidationResult) -> None:
        """Check for local library object variables that may be garbage collected.

        Q-SYS has a semantic rule where library objects (Timer, Ssh, TcpSocket,
        Component, UdpSocket) stored as locals inside functions are subject to
        garbage collection. They must be stored at module-level scope.
        """
        # Build a combined regex for all library constructors
        # Escapes dots for regex: Timer.New -> Timer\.New, etc.
        constructor_patterns = "|".join(
            c.replace(".", r"\.") for c in self._LIBRARY_CONSTRUCTORS
        )
        local_obj_re = re.compile(
            rf"local\s+(\w+)\s*=\s*(?:{constructor_patterns})\s*\("
        )

        # Keyword-balanced function ranges (D3, Fable review 2026-07-06): the
        # old hand-rolled counter only registered an if/for/while opener when
        # then/do sat on the SAME line, so a multi-line condition over-popped
        # the stack and the scanner silently left the function — missing every
        # GC-risk local after it.
        lines = content.split("\n")
        func_ranges = self._find_function_ranges(lines)
        for idx, line in enumerate(lines):
            local_match = local_obj_re.search(line)
            if not local_match:
                continue
            if not self._pos_in_ranges(idx, local_match.start(), lines, func_ranges):
                continue
            result.add_warning(
                self.TIMER_SCOPING,
                f"Local variable '{local_match.group(1)}' holds a library object that may be "
                f"garbage collected. Use module-level variable instead.",
                line=idx + 1,
            )

    def _validate_control_naming(self, content: str, result: ValidationResult) -> None:
        """Check for control naming issues."""
        # Find control names in GetControls
        controls_match = re.search(r"function\s+GetControls\s*\([^)]*\)(.*?)(?=\nfunction|\Z)",
                                   content, re.DOTALL)
        if controls_match:
            control_names = re.findall(r'Name\s*=\s*["\']([^"\']+)["\']', controls_match.group(1))

            # Check for spaces in control names (can cause issues)
            for name in control_names:
                if " " in name:
                    result.add_info(
                        self.CONTROL_NAMING,
                        f"Control name contains spaces: '{name}' - consider using underscores"
                    )

    def _validate_log_api(self, content: str, result: ValidationResult) -> None:
        """Detect invalid Log API calls.

        Q-SYS only provides Log.Message() and Log.Error(). Calls to
        Log.Warning() or Log.Debug() will crash on real hardware because
        those functions do not exist in the Q-SYS runtime.
        """
        for line_num, line in enumerate(content.split("\n"), 1):
            # Skip Lua single-line comments
            stripped = line.lstrip()
            if stripped.startswith("--"):
                continue
            # Also handle inline comments: only check the part before --
            code_part = line.split("--")[0]
            if re.search(r'\bLog\s*\.\s*Warning\s*\(', code_part):
                result.add_error(
                    self.LOG_API,
                    "Log.Warning() does not exist in Q-SYS API. Use Log.Message() or Log.Error().",
                    line=line_num
                )
            if re.search(r'\bLog\s*\.\s*Debug\s*\(', code_part):
                result.add_error(
                    self.LOG_API,
                    "Log.Debug() does not exist in Q-SYS API. Use Log.Message() or Log.Error().",
                    line=line_num
                )
            if re.search(r'\bTimer\s*\.\s*Create\s*\(', code_part):
                # Sys/Timer.htm documents Timer.New/CallAfter/Now only.
                result.add_error(
                    self.LOG_API,
                    "Timer.Create() does not exist in Q-SYS API. Use Timer.New().",
                    line=line_num
                )

    def _control_context(self, content: str):
        """(declared, refs) for QPDK049/050, evaluated once per validation."""
        if self._control_ctx is None:
            from . import control_refs
            declared = control_refs.declared_controls(content)
            code_view = self._strip_noncode(content)
            refs = control_refs.find_refs(content, code_view) if declared else []
            safe = control_refs.guarded(refs, content, code_view) if declared else set()
            self._control_ctx = (declared, refs, safe)
        return self._control_ctx

    def _validate_undeclared_controls(self, content: str, result: ValidationResult) -> None:
        """QPDK049: literal Controls reference to a name GetControls never declares.

        On a Core, Controls holds only declared controls, so the lookup is nil
        and the next index crashes the script (Controls_IO.htm). Evaluated at
        default properties; see control_refs.py for limits.
        """
        from . import control_refs
        declared, refs, safe = self._control_context(content)
        if not declared:
            return
        for ref in control_refs.undeclared(refs, declared, safe):
            result.add_error(
                self.UNDECLARED_CONTROL,
                f"Controls[\"{ref.name}\"] is not declared by GetControls; on a Core "
                "this is nil and indexing it crashes the script.",
                line=ref.line)

    def _validate_scalar_as_array(self, content: str, result: ValidationResult) -> None:
        """QPDK050: a Count=1 control used as an array.

        A single control is userdata: #Controls.x, Controls.x[i] and
        ipairs(Controls.x) raise on a Core (confirmed on a Core).
        """
        from . import control_refs
        declared, refs, _checked = self._control_context(content)
        if not declared:
            return
        for ref, use, always_single in control_refs.scalar_as_array(content, refs, declared):
            if always_single:
                result.add_error(
                    self.SCALAR_AS_ARRAY,
                    f"Controls[\"{ref.name}\"] is a single control (Count=1 in every "
                    f"configuration) but is used with {use}; this raises on a Core.",
                    line=ref.line)
            else:
                result.add_warning(
                    self.SCALAR_AS_ARRAY,
                    f"Controls[\"{ref.name}\"] has Count=1 at default properties but is "
                    f"used with {use}; this raises on a Core at that configuration unless "
                    "the code only runs when a property makes Count > 1.",
                    line=ref.line)

    def _validate_rapidjson(self, content: str, result: ValidationResult) -> None:
        """Detect rapidjson usage without a require() call.

        rapidjson is a Lua C extension bundled with Q-SYS but it is NOT an
        automatic global. It must be loaded with require("rapidjson") before
        use, otherwise calls like rapidjson.decode() will produce a
        "attempt to index a nil value" error at runtime.
        """
        # F2: `content` here is the string/comment-blanked code view, so a bare
        # `rapidjson` mention inside a Description string no longer false-fires
        # the "uses_rapidjson" check. But the require() argument is itself a
        # string literal (blanked in the code view), so detect it against the
        # raw content.
        raw = getattr(self, "_raw_content", content)
        uses_rapidjson = bool(re.search(r'\brapidjson\b', content))
        requires_rapidjson = bool(re.search(
            r'require\s*[\(]?\s*["\']rapidjson["\']\s*[\)]?', raw
        ))
        if uses_rapidjson and not requires_rapidjson:
            result.add_warning(
                self.RAPIDJSON_REQUIRE,
                "rapidjson is used but require('rapidjson') not found. "
                "rapidjson is not a global — it must be explicitly required."
            )

    # Positional pass: APIs whose top-level execution at design time is the
    # guarded class. Controls indexing is the reproduced Designer crash
    # (Controls is nil at design time); the constructors are the established
    # QPDK025 set plus UdpSocket.New (same library family as TcpSocket.New).
    _TOPLEVEL_RUNTIME_API_RE = re.compile(
        r'\b(?:Timer\.New|Timer\.Create|Ssh\.New|TcpSocket\.New|UdpSocket\.New'
        r'|Controls\s*[.\[])'
    )
    # Early-return guard style: everything after it is design-time safe.
    _EARLY_RETURN_GUARD_RE = re.compile(
        r'\bif\s+(?:not\s+Controls|Controls\s*==\s*nil)\s+then\s+return\b'
    )

    def _validate_runtime_guard(self, content: str, result: ValidationResult) -> None:
        """Detect runtime API calls outside 'if Controls then' guard.

        Q-SYS plugin files execute in both design-time context (when Designer
        loads the plugin to read its structure) and runtime context (when the
        Core executes it). In design-time context, Controls is nil. Any runtime
        API call outside 'if Controls then' will crash Designer on load.

        Two passes (D2, Fable review 2026-07-06 — previously ONE guard anywhere
        in the file suppressed the ERROR even when the call executed outside
        every guard):
          1. Positional: a runtime API on a top-level line (outside every
             function body and every ``if Controls then`` block, and not after
             an early-return guard) executes at design time — line-anchored
             ERROR per occurrence.
          2. Legacy file-level fallback: runtime API present anywhere with NO
             guard structure at all (catches wiring hidden behind a top-level
             call into an unguarded function body).
        """
        lines = content.split("\n")
        func_ranges = self._find_function_ranges(lines)
        guard_ranges = self._find_controls_guard_ranges(lines)

        # First top-level early-return guard, if any.
        early_idx: int | None = None
        for idx, line in enumerate(lines):
            m = self._EARLY_RETURN_GUARD_RE.search(line)
            if m and not self._pos_in_ranges(idx, m.start(), lines, func_ranges):
                early_idx = idx
                break

        findings: list[tuple[int, str]] = []
        for idx, line in enumerate(lines):
            m = self._TOPLEVEL_RUNTIME_API_RE.search(line)
            if not m:
                continue
            if early_idx is not None and idx > early_idx:
                continue
            if self._pos_in_ranges(idx, m.start(), lines, func_ranges):
                continue
            if self._pos_in_ranges(idx, m.start(), lines, guard_ranges):
                continue
            findings.append((idx, m.group(0).strip()))

        for idx, api in findings:
            result.add_error(
                self.RUNTIME_GUARD,
                f"Runtime API '{api}' executes at design time: this top-level "
                "line is outside every 'if Controls then' guard. Designer "
                "evaluates the file with Controls nil, so move this inside "
                "the runtime guard.",
                line=idx + 1,
            )
        if findings:
            return

        has_guard = (
            bool(re.search(r'\bif\s+Controls\s+then\b', content))
            or early_idx is not None
        )
        has_runtime_api = bool(re.search(
            r'\b(Timer\.New|Timer\.Create|Ssh\.New|TcpSocket\.New|Controls\.\w+\.EventHandler)\b',
            content
        ))
        if has_runtime_api and not has_guard:
            result.add_error(
                self.RUNTIME_GUARD,
                "Runtime API calls found but no 'if Controls then' guard detected. "
                "Plugin runtime code must be inside 'if Controls then ... end' to prevent "
                "crashes in design-time context."
            )

    # Runtime-only globals: nil/unavailable while Designer evaluates the
    # design-time definition functions. ``Controls`` is the reproduced Designer
    # crash (ERROR); the others are runtime objects whose design-time-index
    # outcome is not KB-confirmed per global (WARNING).
    _DESIGN_TIME_FAULT_ERROR: tuple[str, ...] = ("Controls",)
    _DESIGN_TIME_FAULT_WARN: tuple[str, ...] = (
        "Component", "NamedControl", "Timer", "Ssh", "TcpSocket", "UdpSocket",
    )

    def _validate_design_time_runtime_global(self, content: str, result: ValidationResult) -> None:
        """Detect a runtime-only global indexed inside a design-time function.

        QPDK012: Q-SYS calls the definition functions (GetControls,
        GetControlLayout, GetProperties, GetPages, GetPrettyName, GetColor) at
        DESIGN TIME, when Designer loads the plugin to read its structure. The
        runtime objects (Controls, Component, NamedControl, Timer, Ssh,
        TcpSocket, UdpSocket) do not exist yet — ``Controls`` is nil at design
        time (the SAME mechanism QPDK025 guards; KB Building_a_Plugin.htm keeps
        the run-time part separate). Indexing one inside a GetX body crashes
        Designer on drag-in (reproduced: cisco-modular-plugin commit ce220a6,
        "GetControlLayout indexed nil Controls").

        Inverse of QPDK025: QPDK025 flags runtime API with NO ``if Controls
        then`` guard ANYWHERE (file-level); QPDK012 flags a runtime global
        INSIDE a design-time function, where a guard cannot help because the
        function itself runs at design time (so it fires even when a correct
        runtime guard exists elsewhere — exactly the ce220a6 bug).

        Severity: ERROR for ``Controls`` (crash reproduced + KB-grounded
        nil-at-design-time); WARNING for the other runtime objects (referencing
        them at design time is wrong, but the per-global crash-vs-no-op outcome
        is not KB-confirmed). Runs on the string/comment-blanked code view, so a
        control NAMED "Controls" (a string) or a comment mention never fires.
        FP guards: the legit top-level ``if Controls then`` runtime block is
        OUTSIDE every GetX body (never flagged); a ``local`` variable shadowing
        the name suppresses that name for that function; the function-body span
        comes from the robust _find_function_ranges (a greedy regex would
        over-capture into the runtime block and false-positive).
        """
        code_lines = content.split("\n")
        ranges = self._find_function_ranges(code_lines)
        if not ranges:
            return
        raw_lines = getattr(self, "_raw_content", content).split("\n")
        header_re = re.compile(r'(?:^|[^.\w])function\s+(\w+)\s*\(')

        for start, end in ranges:
            hdr = header_re.search(code_lines[start])
            if not hdr or hdr.group(1) not in self._FRAMEWORK_API_FUNCTIONS:
                continue
            fn_name = hdr.group(1)
            body_idx = range(start, end + 1)
            for name, is_error in (
                [(n, True) for n in self._DESIGN_TIME_FAULT_ERROR]
                + [(n, False) for n in self._DESIGN_TIME_FAULT_WARN]
            ):
                # FP guard: a `local <name>` declaration shadows the global.
                local_re = re.compile(r'\blocal\b[^=\n]*\b' + re.escape(name) + r'\b')
                if any(local_re.search(code_lines[i]) for i in body_idx):
                    continue
                index_re = re.compile(r'\b' + re.escape(name) + r'\s*[.\[]')
                for i in body_idx:
                    if not index_re.search(code_lines[i]):
                        continue
                    raw_line = raw_lines[i] if i < len(raw_lines) else ""
                    if self._is_suppressed(raw_line, self.DESIGN_TIME_RUNTIME_GLOBAL):
                        continue
                    if is_error:
                        result.add_error(
                            self.DESIGN_TIME_RUNTIME_GLOBAL,
                            f"Runtime global '{name}' is indexed inside the design-time "
                            f"function {fn_name}(). Designer evaluates {fn_name}() at design "
                            f"time when '{name}' does not exist (Controls is nil), which "
                            "crashes the plugin on drag-in. Move runtime access into the "
                            "'if Controls then ... end' block.",
                            line=i + 1,
                        )
                    else:
                        result.add_warning(
                            self.DESIGN_TIME_RUNTIME_GLOBAL,
                            f"Runtime object '{name}' is indexed inside the design-time "
                            f"function {fn_name}(). Designer evaluates {fn_name}() at design "
                            f"time, before '{name}' exists; runtime access belongs in the "
                            "'if Controls then ... end' block. (Per-global outcome unverified "
                            "on hardware; Controls in this position is a confirmed crash.)",
                            line=i + 1,
                        )
                    break  # one finding per (function, global) is enough

    def _validate_ssh_handlers(self, content: str, result: ValidationResult) -> None:
        """Check SSH handler completeness.

        When Ssh.New() is used, all 6 mandatory lifecycle handlers must be
        assigned: Connected, Reconnect, Closed, Error, Timeout, LoginFailed.
        Missing any one causes silent failure on the corresponding event.
        The Data handler is recommended but optional if using SSH:Read() polling.
        """
        if not re.search(r'\bSsh\.New\s*\(', content):
            return
        for handler in self._SSH_HANDLERS_MANDATORY:
            pattern = rf'\b\w+\s*\.\s*{handler}\s*='
            if not re.search(pattern, content):
                result.add_warning(
                    self.SSH_HANDLERS,
                    f"SSH handler '{handler}' not assigned. Missing handlers cause silent failures."
                )
        for handler in self._SSH_HANDLERS_RECOMMENDED:
            pattern = rf'\b\w+\s*\.\s*{handler}\s*='
            if not re.search(pattern, content):
                result.add_info(
                    self.SSH_HANDLERS,
                    f"SSH handler '{handler}' not assigned. Recommended for event-driven data handling. "
                    "Can be omitted if using SSH:Read() polling pattern."
                )

    def _validate_tonumber_nil_guard(self, content: str, result: ValidationResult) -> None:
        """Detect tonumber() results used in arithmetic without nil guard.

        QPDK022: tonumber() returns nil on invalid input. Any arithmetic on
        that nil crashes the plugin. Heuristic: detect tonumber() nested
        inside another function call (e.g., math.floor(tonumber(x))) without
        an ``or 0`` fallback.

        Known safe patterns that are excluded:
        - ``string.char(tonumber(x, 16))`` inside a gsub hex-escape callback
          where the capture pattern (e.g. ``%x%x``) guarantees valid hex input.
        """
        # Pattern: function_call(tonumber(x)) without "or" fallback
        unsafe_nested_re = re.compile(r'[\w.]+\s*\(\s*tonumber\s*\(')
        # Safe pattern: tonumber(x) or 0 (any fallback)
        safe_fallback_re = re.compile(r'tonumber\s*\([^)]*\)\s+or\s+')
        # Safe pattern: string.char(tonumber(x, 16)) — standard hex-escape idiom.
        # The gsub capture (e.g. %x%x) pre-validates input so tonumber cannot return nil.
        string_char_hex_re = re.compile(r'string\.char\s*\(\s*tonumber\s*\([^,)]+,\s*16\s*\)')

        for line_num, line in enumerate(content.split("\n"), 1):
            stripped = line.lstrip()
            if stripped.startswith("--"):
                continue
            code_part = line.split("--")[0]
            if self._is_suppressed(line, self.TONUMBER_NIL_GUARD):
                continue
            if unsafe_nested_re.search(code_part):
                # Check the full line does not also have an "or" fallback
                if not safe_fallback_re.search(code_part):
                    # Exclude string.char(tonumber(x, 16)) hex-escape idiom
                    if string_char_hex_re.search(code_part):
                        continue
                    result.add_warning(
                        self.TONUMBER_NIL_GUARD,
                        "tonumber() result used without nil guard. "
                        "tonumber() returns nil on invalid input — use 'tonumber(x) or 0' fallback.",
                        line=line_num
                    )

    def _validate_status_value_range(self, content: str, result: ValidationResult) -> None:
        """Detect status control .Value assignments outside the valid 0-5 range.

        QPDK023: Q-SYS StatusState uses values 0 (OK), 1 (Compromised),
        2 (Fault), 3 (NotPresent), 4 (Missing), 5 (Initializing).
        Values outside 0-5, negative values, or non-integer values cause
        unexpected UI behavior.
        """
        # Match patterns like: something.Value = N (where N is a number literal)
        # Only match lines where "status" appears (case-insensitive) to avoid
        # false positives on non-status .Value assignments (e.g., volume, level).
        #
        # F2: `content` is the string/comment-blanked code view, so a `.Value =
        # N` expression embedded inside a STRING literal (the reproduced
        # `doc = "set status.Value = 9 is wrong"` FP) no longer matches. But the
        # "status" context word is frequently the control NAME — a string value
        # (e.g. Controls.Outputs["status"]) that the code view blanks — so the
        # context check consults the RAW line while the assignment match stays on
        # the code view. This keeps real status writes detected without
        # re-introducing the in-string false positive.
        status_value_re = re.compile(
            r'\.Value\s*=\s*(-?\d+(?:\.\d+)?)\b'
        )
        status_context_re = re.compile(r'status', re.IGNORECASE)
        code_lines = content.split("\n")
        raw_lines = getattr(self, "_raw_content", content).split("\n")
        for line_num, line in enumerate(code_lines, 1):
            stripped = line.lstrip()
            if stripped.startswith("--"):
                continue
            code_part = line.split("--")[0]
            raw_line = raw_lines[line_num - 1] if line_num <= len(raw_lines) else line
            if self._is_suppressed(raw_line, self.STATUS_VALUE_RANGE):
                continue
            # Status context can live in the control-name string (blanked here),
            # so check the raw line for the "status" keyword.
            if not status_context_re.search(raw_line.split("--")[0]):
                continue
            for m in status_value_re.finditer(code_part):
                value_str = m.group(1)
                try:
                    value = float(value_str)
                except ValueError:
                    continue
                # Check for non-integer
                if value != int(value):
                    result.add_warning(
                        self.STATUS_VALUE_RANGE,
                        f"Status .Value set to non-integer {value_str}. "
                        "Q-SYS StatusState only uses integer values 0-5.",
                        line=line_num
                    )
                elif value < 0 or value > 5:
                    result.add_warning(
                        self.STATUS_VALUE_RANGE,
                        f"Status .Value set to {value_str}, outside valid range 0-5. "
                        "Valid: 0=OK, 1=Compromised, 2=Fault, 3=NotPresent, 4=Missing, 5=Initializing.",
                        line=line_num
                    )

    def _validate_reserved_name_shadow(self, content: str, result: ValidationResult) -> None:
        """Detect local variables that shadow Q-SYS reserved global names.

        QPDK024: exact-case check for local variable/function declarations
        that shadow Q-SYS API globals like Log, Timer, Controls, etc. Lua
        names are case-sensitive, so ``local controls`` or ``local function
        log`` shadow nothing and are not flagged.
        """
        reserved = set(self._RESERVED_NAMES)

        # Patterns for local variable/function declarations
        local_var_re = re.compile(r'\blocal\s+(?:function\s+)?(\w+)')

        for line_num, line in enumerate(content.split("\n"), 1):
            stripped = line.lstrip()
            if stripped.startswith("--"):
                continue
            code_part = line.split("--")[0]
            if self._is_suppressed(line, self.RESERVED_NAME_SHADOW):
                continue
            m = local_var_re.search(code_part)
            if m:
                var_name = m.group(1)
                if var_name in reserved:
                    result.add_warning(
                        self.RESERVED_NAME_SHADOW,
                        f"Local '{var_name}' shadows Q-SYS reserved global '{var_name}'. "
                        f"Consider renaming to avoid shadowing the Q-SYS API.",
                        line=line_num
                    )

    def _validate_missing_control_layout(self, content: str, result: ValidationResult) -> None:
        """Detect plugins with controls but no GetControlLayout function.

        QPDK026: Plugins with controls but no layout function produce blank
        UI panels. Technically valid but almost always a mistake.
        """
        # Check if GetControls defines any controls
        controls_match = re.search(
            r"function\s+GetControls\s*\([^)]*\)(.*?)(?=\nfunction|\Z)",
            content, re.DOTALL
        )
        if not controls_match:
            return

        controls_body = controls_match.group(1)
        has_controls = bool(
            re.search(r'table\.insert\s*\(\s*ctls', controls_body) or
            re.search(r'\{\s*Name\s*=', controls_body)
        )

        if not has_controls:
            return

        # Check if GetControlLayout exists
        has_layout = bool(re.search(r'function\s+GetControlLayout\s*\(', content))
        if not has_layout:
            result.add_warning(
                self.MISSING_CONTROL_LAYOUT,
                "GetControls defines controls but GetControlLayout() is missing. "
                "Plugins without a layout function produce blank UI panels."
            )

    def _validate_duplicate_property(self, content: str, result: ValidationResult) -> None:
        """Detect duplicate property names between PluginInfo and GetProperties.

        QPDK027: Defining the same property name in PluginInfo table and in
        GetProperties() return causes conflicts and unpredictable behavior.
        """
        # Extract property-like names from PluginInfo (brace-balanced)
        plugin_info_body = self._extract_table_body(content, "PluginInfo")
        if plugin_info_body is None:
            return

        info_fields = set(
            m.group(1) for m in re.finditer(r'(\w+)\s*=', plugin_info_body)
        )

        # Extract property names from GetProperties
        props_match = re.search(
            r"function\s+GetProperties\s*\([^)]*\)(.*?)(?=\nfunction|\Z)",
            content, re.DOTALL
        )
        if not props_match:
            return

        props_body = props_match.group(1)
        # Property names can be defined via Name = "..." in table inserts
        prop_names = set(
            re.findall(r'Name\s*=\s*["\']([^"\']+)["\']', props_body)
        )

        overlaps = info_fields & prop_names
        for name in sorted(overlaps):
            result.add_warning(
                self.DUPLICATE_PROPERTY,
                f"Property '{name}' defined in both PluginInfo and GetProperties(). "
                "Duplicate definitions cause conflicts and unpredictable behavior."
            )

    def _validate_ssh_write_pcall(self, content: str, result: ValidationResult) -> None:
        """Detect SSH:Write() calls without pcall wrapping.

        QPDK029: An SSH write failure without pcall wrapping terminates the
        EventHandler. On reconnect cycles this can permanently kill the
        plugin's ability to recover.

        Checks both same-line pcall and multi-line pcall(function() wrapper
        within a small lookback window (3 lines).
        """
        # Pattern: obj:Write( -- colon method call syntax
        write_call_re = re.compile(r'\w+:Write\s*\(')
        # Pattern: pcall(function() — anonymous-function wrapper on a nearby line
        pcall_func_re = re.compile(r'pcall\s*\(\s*function\s*\(')

        lines = content.split("\n")
        for line_num, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if stripped.startswith("--"):
                continue
            code_part = line.split("--")[0]
            if self._is_suppressed(line, self.SSH_WRITE_PCALL):
                continue
            if write_call_re.search(code_part):
                # Check if the same line contains pcall wrapping
                if 'pcall' in code_part:
                    continue
                # Check preceding lines (up to 3) for pcall(function() wrapper
                lookback = 3
                start = max(0, line_num - 1 - lookback)
                found_pcall_wrapper = False
                for prev_idx in range(line_num - 2, start - 1, -1):
                    prev_code = lines[prev_idx].split("--")[0]
                    if pcall_func_re.search(prev_code):
                        found_pcall_wrapper = True
                        break
                if not found_pcall_wrapper:
                    result.add_warning(
                        self.SSH_WRITE_PCALL,
                        "Unprotected :Write() call. SSH/TCP write failures without pcall "
                        "wrapping can terminate the EventHandler. Use pcall(obj.Write, obj, data).",
                        line=line_num
                    )

    def _validate_timer_missing_stop(self, content: str, result: ValidationResult) -> None:
        """Detect Timer EventHandlers that don't call :Stop().

        QPDK031: One-shot timer pattern requires :Stop() as first statement
        in the EventHandler. Without it the timer continues firing.
        Heuristic: Flag timers whose EventHandler bodies contain no :Stop() call.

        Exception: If :Stop() is called on the same timer variable elsewhere
        in the file (outside the EventHandler body), the timer is considered
        an externally-managed repeating timer and is NOT flagged.
        """
        # Find timer EventHandler blocks: varname.EventHandler = function(...) ... end
        # We use a heuristic approach: find EventHandler assignments and check
        # if :Stop() appears before the closing end
        handler_re = re.compile(
            r'(\w+)\.EventHandler\s*=\s*function\s*\([^)]*\)(.*?)\bend\b',
            re.DOTALL
        )
        for m in handler_re.finditer(content):
            timer_name = m.group(1)
            handler_body = m.group(2)
            handler_start = content[:m.start()].count("\n") + 1

            if self._is_suppressed(
                content.split("\n")[handler_start - 1] if handler_start <= len(content.split("\n")) else "",
                self.TIMER_MISSING_STOP
            ):
                continue

            # Check if handler body contains :Stop()
            if ':Stop()' not in handler_body and ':Stop( )' not in handler_body:
                # Only flag if the variable appears to be a timer
                # Heuristic: check if there's a Timer.New() or Timer.Create() assignment
                # for this variable name, or if :Start( is called on it
                timer_pattern = rf'\b{re.escape(timer_name)}\s*=\s*Timer\.(New|Create)\s*\('
                start_pattern = rf'\b{re.escape(timer_name)}\s*:\s*Start\s*\('
                if re.search(timer_pattern, content) or re.search(start_pattern, content):
                    # Check for external lifecycle management: if :Stop() is called
                    # on this timer anywhere OUTSIDE the EventHandler body, it's
                    # a repeating timer with external stop control — don't flag it.
                    external_stop_re = re.compile(
                        rf'\b{re.escape(timer_name)}\s*:\s*Stop\s*\(\s*\)'
                    )
                    # Remove the handler body region from the content to search
                    # only the rest of the file for external :Stop() calls
                    content_outside_handler = content[:m.start(2)] + content[m.end(2):]
                    if external_stop_re.search(content_outside_handler):
                        continue  # Externally-managed repeating timer — skip

                    result.add_warning(
                        self.TIMER_MISSING_STOP,
                        f"Timer '{timer_name}' EventHandler has no :Stop() call. "
                        "Single-shot timers should call :Stop() at the start of the "
                        "EventHandler to prevent repeated firing.",
                        line=handler_start
                    )

    # F4: xAPI command verbs. The string.format/table.concat injection arm is
    # gated on the format/concat target STARTING with one of these literals so
    # the rule only fires when a Cisco xAPI command is actually being built.
    # KB: cisco_search "xCommand parameter syntax" / "xCommand string parameter
    # double quote value" — xAPI uses `Key: Value` syntax with double-quoted
    # string params (e.g. xCommand.HttpClient.Allow.Hostname.Add.Expression:
    # "Expression", xCommand.Dial.Number: 123), so an unsanitized .String with a
    # space/newline/quote breaks out of the parameter.
    _XAPI_VERBS = ("xCommand", "xConfiguration")

    def _validate_xapi_injection(self, content: str, result: ValidationResult) -> None:
        """Detect unsanitized .String values in sendData calls.

        QPDK033: User-controlled strings interpolated into xAPI commands
        allow command injection. Three detection arms:
          1. ``..`` concatenation:  sendData("xCommand …" .. Controls.x.String)
          2. reverse concatenation: sendData(Controls.x.String .. "…")
          3. (F4) string.format / table.concat: sendData(string.format(
             "xCommand … %s", Controls.x.String)) and the table.concat({…})
             form — gated on the format/concat target starting with an xAPI
             verb literal so generic string.format calls are not flagged.
        """
        # Arms 1-2 run on the string/comment-blanked code view (the literal
        # command text is irrelevant; we only need the `..` + `.String` shape).
        # Pattern: sendData(... .. something.String ..)
        # or: sendData("..." .. Controls.xxx.String)
        xapi_injection_re = re.compile(
            r'sendData\s*\([^)]*\.\.\s*[^)]*\.String'
        )
        # Also catch the reverse: .String .. "..." passed to sendData
        xapi_injection_re2 = re.compile(
            r'sendData\s*\([^)]*\.String\s*\.\.'
        )
        # Arm 3 (F4) runs on the RAW line because it must see the xAPI verb
        # LITERAL inside the format/concat call (blanked in the code view).
        # sendData( ... string.format( "xCommand|xConfiguration ... ) ... .String
        # or table.concat({ "xCommand|xConfiguration ... , ... .String }).
        verbs = "|".join(self._XAPI_VERBS)
        xapi_format_re = re.compile(
            r'sendData\s*\([^)]*'
            r'(?:string\.format|table\.concat)\s*\(\s*[{]?\s*["\'](?:' + verbs + r')\b'
            r'[^)]*\.String'
        )

        raw_lines = getattr(self, "_raw_content", content).split("\n")
        for line_num, line in enumerate(content.split("\n"), 1):
            stripped = line.lstrip()
            if stripped.startswith("--"):
                continue
            code_part = line.split("--")[0]
            raw_line = raw_lines[line_num - 1] if line_num <= len(raw_lines) else line
            if self._is_suppressed(raw_line, self.XAPI_INJECTION):
                continue
            hit = (
                xapi_injection_re.search(code_part)
                or xapi_injection_re2.search(code_part)
                or xapi_format_re.search(raw_line.split("--")[0])
            )
            if hit:
                result.add_warning(
                    self.XAPI_INJECTION,
                    "Unsanitized .String value interpolated into sendData() xAPI command. "
                    "User-controlled strings should be sanitized before xAPI interpolation "
                    "to prevent command injection.",
                    line=line_num
                )

    def _validate_pinstyle_both_write(self, content: str, result: ValidationResult) -> None:
        """Detect controls with PinStyle='Both' that also have Lua value writes.

        QPDK035: A control declared ``PinStyle="Both"`` exposes BOTH an input
        and an output pin, so it can be wired to an external source AND written
        from Lua. Writing — from a Timer or EventHandler — a control the plugin
        also subscribes to risks a feedback loop / re-entrant EventHandler (the
        "room-mute-on-disconnect" bug class). This is a HEDGED warning, NOT an
        assertion of a hard framework rule: the exact runtime behavior is
        unverified on hardware (cf. the retired-QPDK038 fabrication lesson). It
        flags the pattern so the author can confirm the write is intentional
        (e.g. a write-once config field that is never written back — see the
        plugin's v2.6.0 split-control spec §3.9) or convert the control to a
        split input/output pair.

        PinStyle is harvested from BOTH places it can be declared:
          1. GetControls control blocks: ``{ Name="x", ..., PinStyle="Both" }``
             — the common case, previously MISSED entirely (the rule was a
             latent false-negative).
          2. GetControlLayout: ``layout["x"] = { PinStyle="Both" }`` or
             ``layout["x"].PinStyle = "Both"``.
        Runtime writes are matched in both bracket (``Controls["x"].Value=``)
        and dot (``Controls.x.Value=``) notation, with optional Inputs/Outputs.
        """
        pinstyle_both_controls: set[str] = set()

        # Source 1: GetControls control blocks declaring PinStyle="Both"
        controls_match = re.search(
            r"function\s+GetControls\s*\([^)]*\)(.*?)(?=\nfunction|\Z)",
            content, re.DOTALL
        )
        if controls_match:
            for block_m in re.finditer(r'\{([^{}]*)\}', controls_match.group(1), re.DOTALL):
                block = block_m.group(1)
                name_m = re.search(r'Name\s*=\s*["\']([^"\']+)["\']', block)
                if name_m and re.search(r'PinStyle\s*=\s*["\']Both["\']', block):
                    pinstyle_both_controls.add(name_m.group(1))

        # Source 2: GetControlLayout entries declaring PinStyle="Both"
        layout_match = re.search(
            r"function\s+GetControlLayout\s*\([^)]*\)(.*?)(?=\nfunction|\Z)",
            content, re.DOTALL
        )
        if layout_match:
            layout_body = layout_match.group(1)
            for m in re.finditer(
                r'layout\s*\[\s*["\']([^"\']+)["\']\s*\]\s*=\s*\{([^}]*)\}',
                layout_body, re.DOTALL,
            ):
                if re.search(r'PinStyle\s*=\s*["\']Both["\']', m.group(2)):
                    pinstyle_both_controls.add(m.group(1))
            for m in re.finditer(
                r'layout\s*\[\s*["\']([^"\']+)["\']\s*\]\s*\.\s*PinStyle\s*=\s*["\']Both["\']',
                layout_body,
            ):
                pinstyle_both_controls.add(m.group(1))

        if not pinstyle_both_controls:
            return

        # Find runtime value writes (bracket OR dot notation) in the `if Controls then` body
        runtime_match = re.search(r'\bif\s+Controls\s+then\b(.*)', content, re.DOTALL)
        if not runtime_match:
            return

        runtime_body = runtime_match.group(1)
        runtime_offset = content[:runtime_match.start(1)].count("\n")

        for ctrl_name in sorted(pinstyle_both_controls):
            escaped = re.escape(ctrl_name)
            # Controls[.Inputs|.Outputs]?  (["name"] | .name)  .(Value|String|Boolean|Position) =
            write_re = re.compile(
                rf'Controls\s*(?:\.\s*(?:Inputs|Outputs)\s*)?'
                rf'(?:\[\s*["\']{ escaped }["\']\s*\]|\.\s*{ escaped }\b)'
                rf'\s*\.\s*(?:Value|String|Boolean|Position)\s*='
            )
            for line_num, line in enumerate(runtime_body.split("\n"), runtime_offset + 1):
                stripped = line.lstrip()
                if stripped.startswith("--"):
                    continue
                code_part = line.split("--")[0]
                if write_re.search(code_part):
                    result.add_warning(
                        self.PINSTYLE_BOTH_WRITE,
                        f"Control '{ctrl_name}' has PinStyle=\"Both\" and a Lua value write. "
                        "Writing a Both-pin control the plugin also subscribes to can create "
                        "a feedback loop / re-entrant EventHandler; prefer a split "
                        "input/output control, or confirm this is a write-once field. "
                        "(Unverified on hardware.)",
                        line=line_num
                    )
                    break  # One warning per control is enough

    def _validate_poll_connection_guard(self, content: str, result: ValidationResult) -> None:
        """Detect Timer EventHandlers that call write functions without connection guard.

        QPDK032: Timer handlers that call SSH:Write, TcpSocket:Write, or
        sendData without checking connection state will error on every poll
        cycle during disconnected periods.
        """
        # Find timer EventHandler blocks (use \bend\b to avoid matching inside identifiers)
        handler_re = re.compile(
            r'(\w+)\.EventHandler\s*=\s*function\s*\([^)]*\)(.*?)\bend\b',
            re.DOTALL,
        )

        for m in handler_re.finditer(content):
            timer_name = m.group(1)
            handler_body = m.group(2)
            handler_start = content[:m.start()].count("\n") + 1

            # Only flag if the variable is a Timer
            timer_pattern = rf'\b{re.escape(timer_name)}\s*=\s*Timer\.(New|Create)\s*\('
            start_pattern = rf'\b{re.escape(timer_name)}\s*:\s*Start\s*\('
            if not (
                re.search(timer_pattern, content)
                or re.search(start_pattern, content)
            ):
                continue

            if self._is_suppressed(
                content.split("\n")[handler_start - 1]
                if handler_start <= len(content.split("\n"))
                else "",
                self.POLL_CONNECTION_GUARD,
            ):
                continue

            # Check if handler body calls write functions
            has_write = bool(
                re.search(
                    r'\b(sendData|ssh\s*:\s*Write|socket\s*:\s*Write|\w+\s*:\s*Write)\s*\(',
                    handler_body,
                    re.IGNORECASE,
                )
            )
            if not has_write:
                continue

            # Check if handler body has connection guard
            has_guard = bool(
                re.search(
                    r'\b(IsConnected|is_connected|isConnected|\.IsConnected)\b',
                    handler_body,
                    re.IGNORECASE,
                )
            )
            if not has_guard:
                result.add_warning(
                    self.POLL_CONNECTION_GUARD,
                    f"Timer '{timer_name}' EventHandler calls write functions without "
                    "an IsConnected check. Write calls during disconnected periods will "
                    "error on every poll cycle.",
                    line=handler_start,
                )

    # Q-SYS framework API functions get a higher threshold (500 lines)
    # because they contain pure declarative data (table entries), not
    # business logic.  Every complex plugin exceeds 100 lines in these
    # functions -- it's inherent to the pattern, not architectural debt.
    _FRAMEWORK_API_FUNCTIONS: set[str] = {
        "GetControls",
        "GetControlLayout",
        "GetProperties",
        "GetPages",
        "GetPrettyName",
        "GetColor",
    }
    _FRAMEWORK_THRESHOLD = 500
    _DEFAULT_THRESHOLD = 100

    def _validate_monolithic_function(self, content: str, result: ValidationResult) -> None:
        """Detect functions exceeding a line-count threshold.

        QPDK034: Functions over 100 lines are architectural debt. New code
        should not repeat the pattern of monolithic functions.

        Q-SYS framework API functions (GetControls, GetControlLayout,
        GetProperties, GetPages, GetPrettyName, GetColor) use a higher
        threshold of 500 lines because they contain pure declarative
        table data rather than business logic.
        """
        lines = content.split("\n")
        # Keyword-balanced ranges (the same scanner QPDK037 relies on). The old
        # per-line counter here popped the function frame on ANY `end`, so the
        # first inner `if ... end` truncated the measured span and the rule was
        # silent on any function containing a block — i.e. nearly all real code.
        for start, end in self._find_function_ranges(lines):
            line_count = end - start + 1
            name_match = re.search(r'\bfunction\s+(\w[\w.:]*)\s*\(', lines[start])
            func_name = name_match.group(1) if name_match else "<anonymous>"
            threshold = (
                self._FRAMEWORK_THRESHOLD
                if func_name in self._FRAMEWORK_API_FUNCTIONS
                else self._DEFAULT_THRESHOLD
            )
            if line_count > threshold:
                if not self._is_suppressed(lines[start], self.MONOLITHIC_FUNCTION):
                    result.add_info(
                        self.MONOLITHIC_FUNCTION,
                        f"Function '{func_name}' is {line_count} lines long "
                        f"(threshold: {threshold}). Consider refactoring into "
                        f"smaller functions.",
                        line=start + 1,
                    )

    def _validate_dual_write_indicator(self, content: str, result: ValidationResult) -> None:
        """Detect Indicator/Text controls missing either .Value or .String write.

        QPDK036: Knob controls auto-format .String from .Value. Indicator
        and Text controls do not -- both .Value and .String must be set
        explicitly for correct display.
        """
        # Step 1: Find Indicator/Text controls in GetControls
        controls_match = re.search(
            r"function\s+GetControls\s*\([^)]*\)(.*?)(?=\nfunction|\Z)",
            content,
            re.DOTALL,
        )
        if not controls_match:
            return

        controls_body = controls_match.group(1)

        # Find control names declared as Indicator or Text type
        indicator_text_controls: set[str] = set()
        control_blocks = re.finditer(r'\{([^}]*)\}', controls_body, re.DOTALL)
        for block_match in control_blocks:
            block = block_match.group(1)
            name_m = re.search(r'Name\s*=\s*["\']([^"\']+)["\']', block)
            type_m = re.search(r'ControlType\s*=\s*["\'](\w+)["\']', block)
            if name_m and type_m and type_m.group(1) in ("Indicator", "Text"):
                indicator_text_controls.add(name_m.group(1))

        if not indicator_text_controls:
            return

        # Step 2: Check runtime code for .Value and .String writes
        runtime_match = re.search(
            r'\bif\s+Controls\s+then\b(.*)', content, re.DOTALL
        )
        if not runtime_match:
            return

        runtime_body = runtime_match.group(1)

        for ctrl_name in sorted(indicator_text_controls):
            escaped = re.escape(ctrl_name)
            # Check for .Value write
            has_value_write = bool(
                re.search(
                    rf'["\']{ escaped }["\']\s*\]\s*\.\s*Value\s*=',
                    runtime_body,
                )
            )
            # Check for .String write
            has_string_write = bool(
                re.search(
                    rf'["\']{ escaped }["\']\s*\]\s*\.\s*String\s*=',
                    runtime_body,
                )
            )

            if has_value_write and not has_string_write:
                result.add_info(
                    self.DUAL_WRITE_INDICATOR,
                    f"Indicator/Text control '{ctrl_name}' has .Value write but no "
                    f".String write. Unlike Knob controls, Indicator and Text controls "
                    f"do not auto-format .String from .Value -- set both explicitly.",
                )
            elif has_string_write and not has_value_write:
                result.add_info(
                    self.DUAL_WRITE_INDICATOR,
                    f"Indicator/Text control '{ctrl_name}' has .String write but no "
                    f".Value write. Both .Value and .String should be set explicitly "
                    f"for Indicator/Text controls.",
                )

    @staticmethod
    def _iter_getcontrols_blocks(content: str):
        """Yield (block_text, name) for each ``{ ... Name="x" ... }`` control
        block declared inside GetControls. Shared by QPDK011/013/035/036.

        The block regex ``\\{([^{}]*)\\}`` matches innermost brace pairs, which is
        the shape of a single control table entry (it does not span nested
        tables — control entries are flat). Blocks without a Name are skipped.
        """
        controls_match = re.search(
            r"function\s+GetControls\s*\([^)]*\)(.*?)(?=\nfunction|\Z)",
            content, re.DOTALL,
        )
        if not controls_match:
            return
        for block_m in re.finditer(r'\{([^{}]*)\}', controls_match.group(1), re.DOTALL):
            block = block_m.group(1)
            name_m = re.search(r'Name\s*=\s*["\']([^"\']+)["\']', block)
            if name_m:
                yield block, name_m.group(1)

    def _validate_trigger_input_pin(self, content: str, result: ValidationResult) -> None:
        """Flag Button controls declared as ButtonType=Trigger with an input pin.

        QPDK011 (F5): A control with ``ControlType="Button"``,
        ``ButtonType="Trigger"`` and ``PinStyle`` in {Input, Both} is suspicious:
        Trigger buttons are typically driven by Lua, not by a wired input pin.

        Scope (KB-grounded, see internal notes):
          * EXCLUDES ButtonType="StateTrigger" — KB (State_Trigger.htm,
            "#### Input Pin") confirms StateTrigger DOES expose a functioning
            input pin, so flagging it would be wrong.
          * Does NOT flag PinStyle="Output" (legitimate pulse-out) — only the
            input-bearing pin styles Input/Both.

        HEDGED WARNING, never ERROR: the precise "Trigger+Input renders no pin"
        mechanism is NOT KB-confirmed, so the message says so explicitly. This
        deliberately avoids the retired-QPDK038 fabrication failure mode.
        """
        raw_lines = getattr(self, "_raw_content", content).split("\n")
        for block, name in self._iter_getcontrols_blocks(content):
            ctype_m = re.search(r'ControlType\s*=\s*["\']([^"\']+)["\']', block)
            btype_m = re.search(r'ButtonType\s*=\s*["\']([^"\']+)["\']', block)
            pstyle_m = re.search(r'PinStyle\s*=\s*["\']([^"\']+)["\']', block)
            if not (ctype_m and btype_m and pstyle_m):
                continue
            # EXACT match on Trigger — StateTrigger is intentionally excluded.
            if ctype_m.group(1) != "Button" or btype_m.group(1) != "Trigger":
                continue
            if pstyle_m.group(1) not in ("Input", "Both"):
                continue
            # Locate the control's declaration line (for suppression + report).
            line_num = None
            for i, rl in enumerate(raw_lines, 1):
                if re.search(r'Name\s*=\s*["\']' + re.escape(name) + r'["\']', rl):
                    line_num = i
                    break
            if line_num is not None and self._is_suppressed(
                raw_lines[line_num - 1], self.TRIGGER_INPUT_PIN
            ):
                continue
            result.add_warning(
                self.TRIGGER_INPUT_PIN,
                f"Button '{name}' uses ButtonType=Trigger with "
                f"PinStyle={pstyle_m.group(1)} — Trigger buttons are typically "
                "Lua-driven, not pin-driven; prefer ButtonType=Momentary for pin "
                "actions. (Unverified on hardware.)",
                line=line_num,
            )

    @staticmethod
    def _extract_inline_layout_keys(layout_body: str) -> tuple[set[str], set[str], bool]:
        """Harvest depth-1 keys from inline-literal layout constructors.

        Scans every ``layout = { ... }`` / ``return { ... }`` table literal in
        a GetControlLayout body and walks its braces (skipping Lua strings,
        long strings, and comments) to collect the constructor's OWN keys —
        nested style tables (``{ Style = "Knob" }``) are depth 2 and ignored.

        Returns ``(keys, dynamic_bases, opaque)``:
          * keys           — literal keys: ``["name"]=``, ``['name']=``, and
                             identifier ``name=`` fields at depth 1.
          * dynamic_bases  — string prefixes of concatenated keys
                             (``["chan " .. i]``), separators stripped, same
                             treatment as assignment-form FP guard 3.
          * opaque         — True when a computed non-string key ([var]=...)
                             was seen: literal coverage is then statically
                             unknowable.

        Consumers must use these to SUPPRESS advisories only (QPDK013 FP
        guard 5): this is a heuristic scanner, and its failure mode is
        required to be a missed advisory, never an invented finding.
        """
        keys: set[str] = set()
        dynamic_bases: set[str] = set()
        opaque = False
        n = len(layout_body)

        for start in re.finditer(r'(?:\blayout\s*=\s*|\breturn\s*)\{', layout_body):
            i = start.end() - 1  # index of the opening '{'
            depth = 0
            expecting_key = False
            while i < n:
                ch = layout_body[i]
                # -- comments (line and long-bracket) --------------------
                if layout_body.startswith("--", i):
                    lm = re.match(r'--\[(=*)\[', layout_body[i:])
                    if lm:
                        close = "]" + lm.group(1) + "]"
                        j = layout_body.find(close, i + lm.end())
                        i = n if j < 0 else j + len(close)
                    else:
                        j = layout_body.find("\n", i)
                        i = n if j < 0 else j + 1
                    continue
                # -- quoted strings --------------------------------------
                if ch in ('"', "'"):
                    j = i + 1
                    while j < n:
                        if layout_body[j] == "\\":
                            j += 2
                            continue
                        if layout_body[j] == ch:
                            break
                        j += 1
                    i = j + 1
                    continue
                # -- long strings [[...]] / [=[...]=] --------------------
                lsm = re.match(r'\[(=*)\[', layout_body[i:]) if ch == "[" else None
                if lsm:
                    close = "]" + lsm.group(1) + "]"
                    j = layout_body.find(close, i + lsm.end())
                    i = n if j < 0 else j + len(close)
                    continue
                # -- brace tracking --------------------------------------
                if ch == "{":
                    depth += 1
                    expecting_key = depth == 1
                    i += 1
                    continue
                if ch == "}":
                    depth -= 1
                    if depth <= 0:
                        break  # constructor closed
                    expecting_key = False
                    i += 1
                    continue
                # -- depth-1 field boundaries and keys -------------------
                if depth == 1:
                    if ch in ",;":
                        expecting_key = True
                        i += 1
                        continue
                    if expecting_key and not ch.isspace():
                        ident = re.match(r'([A-Za-z_]\w*)\s*=(?!=)', layout_body[i:])
                        if ident:
                            keys.add(ident.group(1))
                            i += ident.end()
                            expecting_key = False
                            continue
                        bracket = re.match(
                            r'\[\s*(["\'])((?:[^\\]|\\.)*?)\1\s*(\]|\.\.)',
                            layout_body[i:],
                        )
                        if bracket:
                            if bracket.group(3) == "]":
                                keys.add(bracket.group(2))
                            else:  # ["prefix" .. expr] — dynamic-suffix key
                                dynamic_bases.add(bracket.group(2).rstrip(" _-"))
                                j = layout_body.find("]", i + bracket.end())
                                i = n if j < 0 else j  # land on ']' (no key add)
                            i += bracket.end() if bracket.group(3) == "]" else 1
                            expecting_key = False
                            continue
                        if ch == "[":
                            # Computed non-string key: coverage unknowable.
                            opaque = True
                            j = layout_body.find("]", i)
                            i = n if j < 0 else j + 1
                            expecting_key = False
                            continue
                        expecting_key = False  # positional value, not a key
                        i += 1
                        continue
                i += 1

        return keys, dynamic_bases, opaque

    def _validate_control_layout_coupling(self, content: str, result: ValidationResult) -> None:
        """Cross-check GetControls control Names against GetControlLayout keys.

        QPDK013 (F6): The layout table is keyed by control Name
        (``layout["Name"]={...}``) — KB-confirmed (Reserved_Functions.htm,
        Building_a_Plugin.htm: ``layout["code"]={PrettyName="code",Style="None"}``).

          * WARNING — a ``layout["name"]`` key with NO matching GetControls Name
            (likely a typo; the layout entry will not bind to a control).
          * INFO — a GetControls Name with no layout entry (often legitimate:
            pin-only/backend controls omit a layout — hence INFO, not WARNING).

        Never ERROR: whether an orphaned key is load-breaking is KB-unverified.

        FP guards (all KB-grounded):
          1. Count ONLY ``layout["name"]={...}`` (and ``layout["name"].x=``)
             assignments — never ``table.insert(graphics, …)`` (graphics are
             NOT name-keyed).
          2. Strip multi-instance controls: a control with ``Count`` set to
             anything other than the literal ``1`` (a variable OR a literal >1)
             expands to ``Name 1``..``Name N`` (a space + numeric suffix), so
             the bare Name legitimately has no exact-match layout key.
          3. Recognize dynamic-suffix layout keys ``layout["base " .. i]``: the
             literal prefix is the control base Name, so a control laid out only
             via such keys IS laid out (covers no-Count controls with dynamic
             keys that guard 2 misses).
          4. Skip the INFO for ``PinStyle="Output"``/``"None"`` controls: an
             Output pin wires to the schematic and "None" is a pure backend
             value, so a missing panel layout is by design, not an omission.
          5. (0.14.0, memory e1fdb1cf) Recognize INLINE-LITERAL layout tables —
             ``local layout = { ["x"]={...}, y={...} }`` and
             ``return { x={...} }, {}`` — by brace-walking the constructor and
             harvesting its depth-1 keys. Literal keys SUPPRESS the INFO
             direction only, never feed the WARNING direction: the walker is a
             static heuristic, so its failure mode must be a missed advisory,
             not an invented finding. A computed non-string key ([var]=...)
             makes coverage unknowable → the INFO direction goes quiet for the
             whole file rather than mislead.
        """
        # --- Collect GetControls Names, tracking multi-instance controls. ---
        # A control with Count != 1 expands to "Name 1".."Name N" (KB:
        # Reserved_Functions.htm — "control name, followed by a space, and then
        # a numerical suffix"), and its layout entries use the dynamic-suffix
        # key form ``layout["Name " .. i]`` which the literal-key regex below
        # deliberately does NOT capture. So the bare Name legitimately has no
        # exact-match layout key and must NOT draw an INFO.
        #
        # Count is most often a VARIABLE (``Count = sourceCount``), not a
        # literal — the real-world Cisco plugin uses ``Count = hdmiInputCount``
        # etc. Treating only numeric ``Count = N>1`` as multi-instance produced
        # 17 false INFO advisories on that plugin. We therefore treat ANY Count
        # whose value is not the literal ``1`` (a variable OR a literal >1) as
        # multi-instance; only an explicit ``Count = 1`` keeps the bare name.
        control_names: set[str] = set()
        multi_count_names: set[str] = set()
        # FP guard 4: controls declared PinStyle="Output" or "None" are
        # deliberately panel-less — an Output pin wires to the schematic, and
        # "None" is a pure backend value; neither is expected to have a layout
        # entry. A missing layout for such a control is by-design, not an
        # omission, so it must NOT draw an INFO. (The high-value WARNING
        # direction — a layout key with no control — is unaffected.)
        panel_less_pin_names: set[str] = set()
        for block, name in self._iter_getcontrols_blocks(content):
            control_names.add(name)
            count_m = re.search(r'\bCount\s*=\s*([^,}\n]+)', block)
            if count_m:
                count_val = count_m.group(1).strip()
                if count_val.isdigit():
                    if int(count_val) != 1:
                        multi_count_names.add(name)  # literal Count > 1 (or 0)
                else:
                    # Non-literal Count (e.g. a property-driven variable) almost
                    # always means N instances → suffixed names + dynamic layout.
                    multi_count_names.add(name)
            pin_m = re.search(r'PinStyle\s*=\s*["\']([^"\']+)["\']', block)
            if pin_m and pin_m.group(1) in ("Output", "None"):
                panel_less_pin_names.add(name)

        if not control_names:
            return

        # --- Collect layout["name"] keys (FP guard 1: name-keyed only). ---
        layout_match = re.search(
            r"function\s+GetControlLayout\s*\([^)]*\)(.*?)(?=\nfunction|\Z)",
            content, re.DOTALL,
        )
        if not layout_match:
            return  # QPDK026 already covers a missing layout function entirely.
        layout_body = layout_match.group(1)
        # layout["name"] = {...}  OR  layout["name"].field = ...
        layout_keys = set(
            re.findall(r'layout\s*\[\s*["\']([^"\']+)["\']\s*\]', layout_body)
        )
        # FP guard 3 (dynamic-suffix keys): controls laid out via a concatenated
        # key — ``layout["base " .. i]`` / ``layout["base_" .. i]`` — are NOT
        # captured by the literal-key regex above (the ``]`` does not follow the
        # quote). The literal prefix, minus trailing separators, is the control
        # base Name, so such a control IS laid out and must not draw an INFO.
        # (Peer-reported on widget arrays; closes the audit's noted blind spot
        # for no-Count controls with dynamic-only keys.)
        dynamic_bases = {
            m.group(1).rstrip(" _-")
            for m in re.finditer(
                r'layout\s*\[\s*["\']([^"\']+)["\']\s*\.\.', layout_body
            )
        }

        # FP guard 5 (inline-literal constructors): harvest depth-1 keys from
        # ``layout = { ... }`` / ``return { ... }`` table literals. INFO-side
        # suppression only — see the docstring for the asymmetry rationale.
        literal_keys, literal_dyn_bases, literal_opaque = (
            self._extract_inline_layout_keys(layout_body)
        )
        dynamic_bases |= literal_dyn_bases

        raw_lines = getattr(self, "_raw_content", content).split("\n")

        def _line_of(pattern: str) -> int | None:
            for i, rl in enumerate(raw_lines, 1):
                if re.search(pattern, rl):
                    return i
            return None

        # --- WARNING: layout key with no matching control Name (typo). ---
        for key in sorted(layout_keys - control_names):
            ln = _line_of(r'layout\s*\[\s*["\']' + re.escape(key) + r'["\']\s*\]')
            if ln is not None and self._is_suppressed(
                raw_lines[ln - 1], self.CONTROL_LAYOUT_COUPLING
            ):
                continue
            result.add_warning(
                self.CONTROL_LAYOUT_COUPLING,
                f"GetControlLayout key '{key}' has no matching control Name in "
                "GetControls — likely a typo (the layout entry will not bind to a "
                "control).",
                line=ln,
            )

        # --- INFO: control Name with no layout entry (blank panel slot). ---
        # FP guard 2: skip Count>1 controls (they expand to numbered names).
        # FP guard 3: skip controls laid out via a dynamic-suffix key.
        # FP guard 4: skip PinStyle Output/None controls (panel-less by design).
        # FP guard 5: skip controls covered by inline-literal constructor keys;
        # a computed key makes literal coverage unknowable → no INFOs at all.
        if literal_opaque:
            return
        for name in sorted(
            control_names - layout_keys - literal_keys - multi_count_names
            - dynamic_bases - panel_less_pin_names
        ):
            ln = _line_of(r'Name\s*=\s*["\']' + re.escape(name) + r'["\']')
            if ln is not None and self._is_suppressed(
                raw_lines[ln - 1], self.CONTROL_LAYOUT_COUPLING
            ):
                continue
            result.add_info(
                self.CONTROL_LAYOUT_COUPLING,
                f"Control '{name}' has no GetControlLayout entry — it will not "
                "appear on the plugin panel (this is fine for pin-only/backend "
                "controls).",
                line=ln,
            )

    # Read-only xAPI command prefixes that are safe in monitor mode.
    # Status queries, feedback subscriptions, echo config, and cleanup
    # before re-registration are all harmless read-only operations.
    _MONITOR_SAFE_PREFIXES = (
        '"xStatus',
        "'xStatus",
        '"xFeedback register',
        "'xFeedback register",
        '"xFeedback Register',
        "'xFeedback Register",
        '"echo ',
        "'echo ",
        '"xFeedback deregisterall',
        "'xFeedback deregisterall",
        '"xFeedback Deregisterall',
        "'xFeedback Deregisterall",
    )

    def _validate_monitor_mode_guard(self, content: str, result: ValidationResult) -> None:
        """Detect sendData calls without monitor_mode guard.

        QPDK037: Every sendData call path must check the monitor_mode flag.
        Failing to do so causes command injection in monitoring-only
        deployments.  Only triggers if monitor_mode variable exists in file.

        Two improvements over the original 5-line lookback:
        1. Read-only commands (xStatus, xFeedback register, echo) are
           whitelisted — they are safe and required in monitor mode.
        2. Function-scope guard detection — if the enclosing function
           contains a monitor_mode conditional anywhere, the call is
           considered guarded.
        """
        # Only relevant if the file defines/uses a monitor_mode variable
        if not re.search(r'\bmonitor_mode\b', content):
            return

        lines = content.split("\n")

        # Pre-compute function boundaries using a heuristic depth counter.
        # Each entry is (start_line_0idx, end_line_0idx) for a function block.
        func_ranges = self._find_function_ranges(lines)

        # Pre-compute which function ranges contain a monitor_mode guard.
        guard_pat = re.compile(
            r'\b(?:if|elseif)\s+(?:not\s+)?(?:Controls\.)?monitor_mode\b'
        )
        guarded_ranges = set()
        for start, end in func_ranges:
            for i in range(start, end + 1):
                line_code = lines[i].split("--")[0]
                if guard_pat.search(line_code):
                    guarded_ranges.add((start, end))
                    break

        for line_num, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if stripped.startswith("--"):
                continue
            code_part = line.split("--")[0]
            if self._is_suppressed(line, self.MONITOR_MODE_GUARD):
                continue

            if re.search(r'\bsendData\s*\(', code_part) and \
               not re.search(r'\bfunction\s+sendData\s*\(', code_part):
                # Improvement 1: Whitelist read-only commands.
                # Extract the string argument from the sendData call.
                arg_match = re.search(r'\bsendData\s*\(\s*(["\'])', code_part)
                if arg_match:
                    # Find the string content after the opening quote
                    after_quote = code_part[arg_match.end() - 1:]  # include quote
                    if any(after_quote.startswith(prefix)
                           for prefix in self._MONITOR_SAFE_PREFIXES):
                        continue

                # Improvement 2: Function-scope guard detection.
                # Find which function this sendData is inside (0-indexed).
                line_idx = line_num - 1
                enclosing = None
                for fstart, fend in func_ranges:
                    if fstart <= line_idx <= fend:
                        # Pick the tightest (innermost) enclosing function
                        if enclosing is None or fstart > enclosing[0]:
                            enclosing = (fstart, fend)

                if enclosing is not None and enclosing in guarded_ranges:
                    continue

                result.add_warning(
                    self.MONITOR_MODE_GUARD,
                    "sendData() call without nearby monitor_mode guard. "
                    "In monitoring-only deployments, sendData() should only "
                    "execute when monitor_mode is not active.",
                    line=line_num,
                )

    @staticmethod
    def _find_function_ranges(lines: list[str]) -> list[tuple[int, int]]:
        """Find function start/end boundaries via a keyword-balanced block scan.

        Returns a list of (start_idx, end_idx) tuples (0-based line indices), one
        per ``function`` block (including nested ones). Strings and comments are
        blanked first (length/lines preserved) so block keywords inside a string
        literal or comment cannot corrupt the depth count.

        Why a keyword STACK instead of the old per-line open/close regex counter:
        the previous heuristic only registered an ``if``/``for``/``while`` opener
        when ``then``/``do`` appeared on the SAME line. A multi-line ``if``
        condition therefore had its opener missed while its ``end`` still counted
        as a closer — over-popping the stack and dropping an enclosing function's
        frame. A ``sendData`` inside the lost frame then looked un-enclosed, so
        its function-scope ``monitor_mode`` guard was invisible and QPDK037 fired
        falsely (reproduced on CiscoRoomOS-Enhanced-v2.5.4.8 L4980, inside
        ``parseData`` which is monitor_mode-guarded).

        Lua block grammar handled:
          * openers needing ``end``: ``function``, ``if``, ``for``, ``while``,
            and a *standalone* ``do`` block.
          * ``repeat`` is closed by ``until`` (not ``end``).
          * the ``do`` in ``for ... do`` / ``while ... do`` is NOT a new block —
            it belongs to the for/while already on the stack (tracked via
            ``await_do`` on that frame), so it is consumed rather than pushed.
          * ``then``/``else``/``elseif``/``in`` are neutral.
        Reserved words cannot be identifiers in Lua, so word-boundary matching is
        unambiguous (``backend``/``weekend`` never match ``end``).
        """
        # Blank strings/comments so keywords inside them don't skew the count
        # (line/column layout is preserved, so indices still map to the caller's
        # original lines).
        code_lines = PluginValidator._strip_noncode("\n".join(lines)).split("\n")
        kw_re = re.compile(r'\b(function|if|for|while|repeat|do|end|until)\b')

        # Each frame: {"is_func": bool, "start": int, "await_do": bool}
        stack: list[dict] = []
        ranges: list[tuple[int, int]] = []

        for idx, code in enumerate(code_lines):
            for tok in kw_re.findall(code):
                if tok == "function":
                    stack.append({"is_func": True, "start": idx, "await_do": False})
                elif tok in ("if", "repeat"):
                    stack.append({"is_func": False, "start": idx, "await_do": False})
                elif tok in ("for", "while"):
                    # Opens a block now; its `do` is a marker, not a new block.
                    stack.append({"is_func": False, "start": idx, "await_do": True})
                elif tok == "do":
                    if stack and stack[-1]["await_do"]:
                        stack[-1]["await_do"] = False  # the for/while's own `do`
                    else:
                        stack.append({"is_func": False, "start": idx, "await_do": False})
                else:  # end | until — close the innermost open block
                    if stack:
                        frame = stack.pop()
                        if frame["is_func"]:
                            ranges.append((frame["start"], idx))

        return ranges

    @staticmethod
    def _find_controls_guard_ranges(lines: list[str]) -> list[tuple[int, int]]:
        """Ranges (0-based, inclusive) of ``if Controls then ... end`` blocks.

        Same keyword-balanced scan as ``_find_function_ranges``, but records
        the blocks opened by the design-time guard. The guard match runs
        anchored against the joined stripped source, so a line-wrapped
        ``if Controls\\n then`` still counts as a guard.
        """
        joined = PluginValidator._strip_noncode("\n".join(lines))
        code_lines = joined.split("\n")
        offsets: list[int] = []
        pos = 0
        for code in code_lines:
            offsets.append(pos)
            pos += len(code) + 1
        kw_re = re.compile(r'\b(function|if|for|while|repeat|do|end|until)\b')
        guard_re = re.compile(r'if\s+Controls\s+then\b')

        stack: list[dict] = []
        ranges: list[tuple[int, int]] = []
        for idx, code in enumerate(code_lines):
            for tok in kw_re.finditer(code):
                word = tok.group(1)
                if word == "function":
                    stack.append({"guard": False, "start": idx, "await_do": False})
                elif word == "if":
                    is_guard = bool(guard_re.match(joined, offsets[idx] + tok.start()))
                    stack.append({"guard": is_guard, "start": idx, "await_do": False})
                elif word == "repeat":
                    stack.append({"guard": False, "start": idx, "await_do": False})
                elif word in ("for", "while"):
                    stack.append({"guard": False, "start": idx, "await_do": True})
                elif word == "do":
                    if stack and stack[-1]["await_do"]:
                        stack[-1]["await_do"] = False
                    else:
                        stack.append({"guard": False, "start": idx, "await_do": False})
                else:  # end | until
                    if stack:
                        frame = stack.pop()
                        if frame["guard"]:
                            ranges.append((frame["start"], idx))
        return ranges

    @staticmethod
    def _pos_in_ranges(idx: int, col: int, lines: list[str],
                       ranges: list[tuple[int, int]]) -> bool:
        """True if (line idx, column col) falls inside any (start, end) range.

        On a range's START line, only positions at/after the opener keyword
        count as inside — so ``Controls.led.EventHandler = function() end``
        has its LHS (which executes at design time) correctly treated as
        OUTSIDE the anonymous function that starts on the same line.
        """
        for start, end in ranges:
            if start <= idx <= end:
                if idx == start:
                    opener = re.search(
                        r'\b(?:function|if|for|while|repeat|do)\b', lines[start])
                    if opener and col < opener.start():
                        continue
                return True
        return False

    @staticmethod
    def _load_plugin_globals(plugin_path: Path) -> list[str]:
        """Load extra globals from a .luacheckrc in the plugin's directory.

        Parses the 'globals = { ... }' table from a sibling .luacheckrc file
        and returns the names as a list. QPDK framework globals always take
        precedence; these are additive plugin-specific declarations.
        """
        rc_path = plugin_path.parent / ".luacheckrc"
        if not rc_path.exists():
            return []
        try:
            text = rc_path.read_text(encoding="utf-8")
            # Extract quoted strings from globals = { "Name", "Name2", ... }
            # Handles multi-line tables
            match = re.search(r'globals\s*=\s*\{(.*?)\}', text, re.DOTALL)
            if not match:
                return []
            block = match.group(1)
            return re.findall(r'"(\w+)"', block)
        except Exception:
            return []

    @staticmethod
    def _harvest_global_defs(content: str) -> list[str]:
        """Harvest the plugin's own top-level GLOBAL names so luacheck doesn't
        report them as non-standard (W111) or undefined (W113).

        A Q-SYS plugin legitimately defines cross-section helper functions as
        globals (no ``local``) so GetControls/GetControlLayout/the runtime block
        can all call them. luacheck, given ``--no-config``, has no way to know
        these are intentional and flags both the definition (W111) and every use
        (W113). Declaring them via ``--globals`` removes that noise without
        weakening any other check. Two definition forms are recognized, scanned
        on a string/comment-blanked view so matches inside literals/comments are
        ignored:
          * ``function <name>(...)``   — global function definition
          * ``<name> = function(...)`` — global assigned a function literal
        ``local function``/``local x = function`` are excluded (locals are
        scoped, and luacheck handles them correctly). Dotted/colon method names
        (``M.f``/``M:f``) are skipped — the table is the global, not the method.

        FP-risk (accepted, low): a name the author *meant* to make ``local`` but
        forgot will be silently accepted as a global. That is the same tradeoff
        a hand-written ``.luacheckrc globals`` list makes.
        """
        code = PluginValidator._strip_noncode(content)
        names: set[str] = set()
        # function <name>(   — bare global function (name immediately before "(")
        for m in re.finditer(r'(?m)^\s*function\s+([A-Za-z_]\w*)\s*\(', code):
            names.add(m.group(1))
        # <name> = function   — global assigned a function (exclude `local`)
        for m in re.finditer(r'(?m)^\s*(?!local\b)([A-Za-z_]\w*)\s*=\s*function\b', code):
            names.add(m.group(1))
        return sorted(names)

    def _run_luacheck(self, path: Path, result: ValidationResult) -> None:
        """Run luacheck if available.

        QPDK owns the definitive globals list and passes --no-config so
        behavior is deterministic regardless of working directory.
        Plugin-level .luacheckrc globals are merged additively.
        """
        try:
            # Build luacheck command with Q-SYS globals + plugin-level extras.
            # Use the comprehensive v9 globals regardless of target version: an
            # undefined-global check should use the most complete known list;
            # version AVAILABILITY is QPDK009's concern. The legacy V7 list is an
            # incomplete subset, so using it here produced spurious W113
            # "undefined global" for APIs that predate v9 (json, Crypto, ...).
            # --globals takes space-separated names as varargs, not comma-separated
            base_globals = QSYS_GLOBALS_V9
            globals_list = list(base_globals.keys())
            seen = set(globals_list)
            # Plugin-declared globals from a sibling .luacheckrc (explicit).
            for g in self._load_plugin_globals(path):
                if g not in seen:
                    globals_list.append(g)
                    seen.add(g)
            # Plugin-defined top-level globals harvested from the source itself
            # (implicit) — cuts W111/W113 noise on the plugin's own cross-section
            # helper functions. Use the already-loaded raw content when present.
            raw = getattr(self, "_raw_content", None)
            if raw is None:
                try:
                    raw = path.read_text(encoding="utf-8")
                except Exception:
                    raw = ""
            for g in self._harvest_global_defs(raw):
                if g not in seen:
                    globals_list.append(g)
                    seen.add(g)

            cmd = [
                "luacheck",
                "--std", "lua53",         # Q-SYS Core runs Lua 5.3 (help.qsys.com Lua 5.3 Reference Manual)
                "--no-config",            # QPDK owns the config, not the plugin directory
                "--globals", *globals_list,
                "--read-globals", "Properties",  # Properties should be read-only
                "--ignore", "211", "212", "213", "542",  # Suppress noisy warnings:
                # 211 = unused variable, 212 = unused argument,
                # 213 = unused loop variable, 542 = empty body
                "--no-max-line-length",   # Q-SYS plugins commonly exceed 120 chars
                "--formatter", "plain",
                "--codes",
                "--no-color",
                str(path)
            ]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            # luacheck exit codes: 0 clean, 1 warnings, 2 syntax errors,
            # 3 file could not be checked, 4 critical (bad args/config/cache).
            # 3 and 4 mean NO analysis happened; an empty stdout then must not
            # read as a clean pass, so report it as an ERROR.
            if proc.returncode >= 3:
                detail = (proc.stderr or proc.stdout or "").strip().splitlines()
                result.add_error(
                    "QPDK000",
                    f"luacheck failed to run (exit {proc.returncode}), analysis incomplete"
                    + (f": {detail[0]}" if detail else ""))
                return

            # Parse luacheck output
            parsed = []
            for line in proc.stdout.strip().split("\n"):
                if not line or line.startswith("Checking"):
                    continue
                # Format: path:line:col: (Wxxx) message
                match = re.match(r"[^:]+:(\d+):(\d+):\s+\(([EW]\d+)\)\s+(.+)", line)
                if match:
                    parsed.append((int(match.group(1)), int(match.group(2)),
                                   match.group(3), match.group(4)))

            # Names the plugin assigns as globals somewhere (W111 sites).
            # A W113 read of one of these is the single-file .qplug idiom of
            # a global shared across sections, not a typo, so it is INFO.
            # A W113 read of a name assigned NOWHERE stays a WARNING: that is
            # the likely-typo case the check exists for.
            assigned = {
                m.group(1) for _l, _c, code, message in parsed if code == "W111"
                for m in [re.search(r"'([^']+)'", message)] if m
            }

            for line_num, col, code, message in parsed:
                if code.startswith("E"):
                    result.add_error(f"LUACHECK-{code}", message,
                                     line=line_num, column=col, source="luacheck")
                elif code in ("W111", "W112"):
                    # Setting/mutating a non-standard global. In the single-file
                    # .qplug format, authors legitimately declare top-level
                    # globals to bridge design-time and runtime sections; the
                    # def-harvest (_harvest_global_defs) suppresses the ones it
                    # can see, but globals defined inside guarded scopes leak
                    # through. This is a style choice, not a defect, so it is
                    # INFO, not WARNING (vendor corpus: ~1,889 of these across
                    # 19 real plugins swamped genuine warnings).
                    result.add_info(f"LUACHECK-{code}", message,
                                    line=line_num, column=col, source="luacheck")
                elif code == "W113":
                    m = re.search(r"'([^']+)'", message)
                    if m and m.group(1) in assigned:
                        result.add_info(
                            f"LUACHECK-{code}",
                            message + " (assigned as a global elsewhere in this plugin)",
                            line=line_num, column=col, source="luacheck")
                    else:
                        result.add_warning(f"LUACHECK-{code}", message,
                                           line=line_num, column=col, source="luacheck")
                else:
                    result.add_warning(f"LUACHECK-{code}", message,
                                       line=line_num, column=col, source="luacheck")

        except FileNotFoundError:
            result.add_info("QPDK000", "luacheck not found, skipping static analysis")
        except subprocess.TimeoutExpired:
            result.add_error("QPDK000", "luacheck timed out, analysis incomplete")
        except Exception as e:
            result.add_error("QPDK000", f"luacheck failed to run, analysis incomplete: {e}")

    def _validate_qplugx_structure(self, path: Path, result: ValidationResult) -> None:
        """Structural check of an encrypted .qplugx/.qplugx2 artifact (QPDK048).

        Validates the envelope shape only — no decryption, no key material. The
        two known formats (internal notes +
        internal notes):

          v1 (.qplugx):  {"key": b64(256), "iv": b64(16), "data": b64(16*N)}
          v2 (.qplugx2): {"mode","version":2,"language",
                          "designtime": {key,iv,data}, "runtime": {key,iv,data},
                          "signature": b64(256)}

        A malformed envelope means the encryption step produced a broken
        artifact — ERROR. A clean envelope emits one INFO confirming the
        detected version. This runs INSTEAD of Lua/luacheck validation.
        """
        import base64
        import json as _json

        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as e:
            result.add_error(self.QPLUGX_STRUCTURE, f"Cannot read {path.suffix} artifact: {e}")
            return
        try:
            doc = _json.loads(raw)
        except Exception as e:
            result.add_error(self.QPLUGX_STRUCTURE,
                             f"{path.suffix} is not valid JSON (expected an encryption envelope): {e}")
            return
        if not isinstance(doc, dict):
            result.add_error(self.QPLUGX_STRUCTURE, f"{path.suffix} top level must be a JSON object")
            return

        def _b64len(field: str, val, expect=None, block=None) -> int | None:
            if not isinstance(val, str):
                result.add_error(self.QPLUGX_STRUCTURE, f"'{field}' must be a base64 string")
                return None
            try:
                n = len(base64.b64decode(val, validate=True))
            except Exception:
                result.add_error(self.QPLUGX_STRUCTURE, f"'{field}' is not valid base64")
                return None
            if expect is not None and n != expect:
                result.add_error(self.QPLUGX_STRUCTURE,
                                 f"'{field}' decodes to {n} bytes, expected {expect}")
            if block is not None and (n == 0 or n % block != 0):
                result.add_error(self.QPLUGX_STRUCTURE,
                                 f"'{field}' decodes to {n} bytes, not a positive multiple of {block} (AES block)")
            return n

        def _check_envelope(obj, prefix: str) -> None:
            if not isinstance(obj, dict):
                result.add_error(self.QPLUGX_STRUCTURE, f"'{prefix}' must be an object with key/iv/data")
                return
            missing = [k for k in ("key", "iv", "data") if k not in obj]
            if missing:
                result.add_error(self.QPLUGX_STRUCTURE, f"'{prefix}' missing {missing}")
            if "key" in obj:  _b64len(f"{prefix}.key", obj["key"], expect=256)   # RSA-2048 wrapped AES key
            if "iv" in obj:   _b64len(f"{prefix}.iv", obj["iv"], expect=16)      # AES IV
            if "data" in obj: _b64len(f"{prefix}.data", obj["data"], block=16)   # AES-CBC ciphertext

        if doc.get("version") == 2 or path.suffix == ".qplugx2" or "designtime" in doc:
            # v2 format
            for req in ("mode", "version", "designtime", "runtime", "signature"):
                if req not in doc:
                    result.add_error(self.QPLUGX_STRUCTURE, f"v2 envelope missing '{req}'")
            _check_envelope(doc.get("designtime"), "designtime")
            _check_envelope(doc.get("runtime"), "runtime")
            if "signature" in doc:
                _b64len("signature", doc["signature"], expect=256)  # RSA-2048 signature
            if not result.errors:
                result.add_info(self.QPLUGX_STRUCTURE, "valid v2 (.qplugx2) encryption envelope")
        else:
            # v1 format
            extra = [k for k in doc if k not in ("key", "iv", "data")]
            for req in ("key", "iv", "data"):
                if req not in doc:
                    result.add_error(self.QPLUGX_STRUCTURE, f"v1 envelope missing '{req}'")
            if extra:
                result.add_error(self.QPLUGX_STRUCTURE, f"v1 envelope has unexpected keys {extra}")
            _b64len("key", doc.get("key"), expect=256)
            _b64len("iv", doc.get("iv"), expect=16)
            _b64len("data", doc.get("data"), block=16)
            if not result.errors:
                result.add_info(self.QPLUGX_STRUCTURE, "valid v1 (.qplugx) encryption envelope")

    # QPDK006 (_validate_undefined_globals) removed — luacheck owns globals
    # checking via LUACHECK-W111/W112/W113 directly. No duplicate re-emission.
