"""
Ruleset definitions for QPDK validator.

Defines which rules run under each ruleset (P0, default, full) and
manages the phased severity level configuration for gradual rollout.

Phases:
  Phase 1: crash-causing rules (all rulesets)
  Phase 2: quality rules (default and full)
  Phase 3: advisory rules (full only)

Rulesets:
  P0:      Only crash-preventing rules (phase 1)
  default: Crash-preventing and quality rules (phases 1 and 2); INFO rows
           from those rules are listed
  full:    Every rule, INFO advisories listed

With no --ruleset the CLI runs every rule (as full) and hides INFO rows,
printing a count instead.
"""

from .result import IssueSeverity

# --- Rule metadata ---
# Maps each rule code to its category, default severity, and phase.
# "phase" controls which rulesets include the rule:
#   1 = P0, default and full
#   2 = default and full
#   3 = full only

RULE_METADATA: dict[str, dict] = {
    # --- Internal (always active) ---
    # QPDK000 reports that analysis did not complete: a rule crashed, or
    # luacheck failed or timed out (ERROR, so the plugin is not VALID). The
    # "tool not installed" notices use the same code at INFO.
    "QPDK000": {"severity": IssueSeverity.ERROR,   "phase": 1, "category": "internal",   "description": "Validator could not complete a check (rule crash, luacheck failure); a missing optional tool is INFO"},

    # --- Structural / Syntax (always active) ---
    "QPDK001": {"severity": IssueSeverity.ERROR,   "phase": 1, "category": "syntax",    "description": "Lua syntax error"},
    "QPDK002": {"severity": IssueSeverity.ERROR,   "phase": 1, "category": "structure",  "description": "Missing PluginInfo table"},
    "QPDK003": {"severity": IssueSeverity.ERROR,   "phase": 1, "category": "structure",  "description": "Missing required PluginInfo field"},
    "QPDK004": {"severity": IssueSeverity.WARNING, "phase": 2, "category": "structure",  "description": "Invalid PluginInfo field value"},
    "QPDK010": {"severity": IssueSeverity.INFO,    "phase": 3, "category": "naming",     "description": "Control naming suggestion"},
    # Advisory INFO, full ruleset only: the "Trigger+Input renders no pin"
    # mechanism is not in the Q-SYS docs and QSC's own managed plugins ship
    # the pattern. StateTrigger is excluded (State_Trigger.htm: it DOES expose
    # an input pin). Promote only after a bench probe confirms it.
    "QPDK011": {"severity": IssueSeverity.INFO,    "phase": 3, "category": "framework",  "description": "Button ButtonType=Trigger with input PinStyle (Input/Both)"},
    # Emits WARNING (orphaned layout key / likely typo) + INFO (control with
    # no layout entry). Phase 3 so the advisory INFO direction is full-only.
    # Never ERROR (load-breaking severity of an orphaned key is KB-unverified).
    "QPDK013": {"severity": IssueSeverity.WARNING, "phase": 3, "category": "structure",  "description": "GetControls and GetControlLayout names do not match: WARNING for a layout key with no control, INFO for a control with no layout entry"},

    # --- P0 crash-preventing ---
    "QPDK005": {"severity": IssueSeverity.WARNING, "phase": 1, "category": "scoping",    "description": "Timer, socket or component object held only in a local variable may be garbage collected"},
    "QPDK020": {"severity": IssueSeverity.ERROR,   "phase": 1, "category": "api",        "description": "Call to a non-existent Q-SYS API function (Log.Warning/Log.Debug, Timer.Create)"},
    # Inverse of QPDK025: a runtime-only global (Controls/Component/Timer/...)
    # indexed INSIDE a design-time GetX body. ERROR for Controls (a reproduced
    # Designer crash; Controls is nil at design time, the same mechanism
    # QPDK025 asserts); WARNING for the other runtime objects (emit
    # softer than the ERROR metadata, which the severity-SSOT test allows).
    "QPDK012": {"severity": IssueSeverity.ERROR,   "phase": 1, "category": "guard",      "description": "Runtime-only global indexed in a design-time function body"},
    "QPDK025": {"severity": IssueSeverity.ERROR,   "phase": 1, "category": "guard",      "description": "Missing runtime guard"},

    # --- P1 quality rules ---
    "QPDK008": {"severity": IssueSeverity.WARNING, "phase": 2, "category": "structure",  "description": "Duplicate plugin ID"},
    "QPDK009": {"severity": IssueSeverity.WARNING, "phase": 2, "category": "api",        "description": "Version-specific API usage"},
    "QPDK021": {"severity": IssueSeverity.WARNING, "phase": 2, "category": "api",        "description": "rapidjson without require()"},
    "QPDK022": {"severity": IssueSeverity.WARNING, "phase": 2, "category": "safety",     "description": "tonumber() without nil guard"},
    "QPDK023": {"severity": IssueSeverity.WARNING, "phase": 2, "category": "safety",     "description": "Status value out of range"},
    "QPDK024": {"severity": IssueSeverity.WARNING, "phase": 2, "category": "naming",     "description": "Reserved name shadowing"},
    "QPDK028": {"severity": IssueSeverity.WARNING, "phase": 2, "category": "lifecycle",  "description": "SSH handler incompleteness"},

    # --- P2 quality/safety rules ---
    "QPDK026": {"severity": IssueSeverity.WARNING, "phase": 3, "category": "structure",  "description": "Missing GetControlLayout"},
    "QPDK027": {"severity": IssueSeverity.WARNING, "phase": 3, "category": "structure",  "description": "Duplicate property name"},
    "QPDK029": {"severity": IssueSeverity.WARNING, "phase": 3, "category": "safety",     "description": "SSH/TCP Write without pcall"},
    # NOTE: severity here must match what the rule actually emits in validator.py
    # (the emit-time severity is authoritative).
    # QPDK031 is INFO: a Timer repeats by default (Timer.htm), so a timer that
    # is never stopped is correct for polling and only wrong for a one-shot.
    "QPDK031": {"severity": IssueSeverity.INFO,    "phase": 3, "category": "timer",      "description": "Timer never stopped (fine for polling, wrong for a one-shot)"},
    "QPDK032": {"severity": IssueSeverity.WARNING, "phase": 3, "category": "safety",     "description": "Poll without connection guard"},
    "QPDK033": {"severity": IssueSeverity.WARNING, "phase": 3, "category": "security",   "description": "xAPI string injection risk (checks calls to sendData, as Cisco xAPI plugins use)"},
    "QPDK034": {"severity": IssueSeverity.INFO,    "phase": 3, "category": "complexity",  "description": "Monolithic function (>100 lines; >500 for framework API functions)"},
    # QPDK035 is INFO: the docs are silent on whether a script write re-fires
    # the handler, and vendor plugins write feedback to Both-pin controls.
    "QPDK035": {"severity": IssueSeverity.INFO,    "phase": 3, "category": "framework",  "description": "PinStyle Both written from Lua (possible feedback loop)"},
    "QPDK036": {"severity": IssueSeverity.INFO,    "phase": 3, "category": "framework",  "description": "Dual-context write to indicator"},
    "QPDK037": {"severity": IssueSeverity.INFO,    "phase": 3, "category": "framework",  "description": "Missing monitor mode guard (only checks plugins that use sendData and a monitor_mode variable, as Cisco xAPI plugins do)"},
    # QPDK038 (Reflect/StatusGP "clamps Value 4") intentionally NOT shipped:
    # the Value-4->3 clamp claim is unverified, and the pattern ships in
    # field-deployed plugins. Re-introduce only once confirmed on real hardware.
    #
    # DO-NOT-BUILD non-goals (recorded here so nobody repeats the QPDK038
    # pattern of an ungrounded rule; the Q-SYS docs cannot ground any of these):
    #   1. xAPI command-EXISTENCE / known-command allowlist. The catalog is
    #      doc-parse noise and version-merged across RoomOS releases, so a
    #      "this xCommand does not exist" rule would false-flag valid commands.
    #   2. xAPI DEPRECATION / RoomOS-version-gating rule. The KB carries no
    #      per-command version provenance, so any "deprecated/added in vX"
    #      assertion would be invented.
    #   3. IndicatorType / ControlType / ButtonType ENUM-validation. Casing such
    #      as `Led` vs `LED` is unverified in Designer (QSC's evaluator accepts
    #      only `Led`, while field-working plugins ship `LED`), so an
    #      enum-membership rule would flag working plugins. (QPDK011 deliberately matches ButtonType="Trigger"
    #      EXACTLY rather than validating the whole enum.)
    "QPDK039": {"severity": IssueSeverity.ERROR,   "phase": 1, "category": "build",      "description": "Placeholder text such as {{VERSION}} or __VERSION__ left in a PluginInfo field"},

    # --- UI-LINT family (QPDK040+): geometry lint over the EVALUATED
    # GetControlLayout. Runs in the design-time path (qpdk design-check /
    # qpdk.validator.ui_lint), NOT inside the static PluginValidator, because
    # positions only exist after executing the layout function. Thresholds
    # are mined from a corpus of shipped plugins. Severity ceiling
    # WARNING: no geometry defect in this family breaks plugin load.
    "QPDK040": {"severity": IssueSeverity.WARNING, "phase": 2, "category": "ui-geometry", "description": "design-check only: Controls overlap on the same page"},
    "QPDK041": {"severity": IssueSeverity.WARNING, "phase": 2, "category": "ui-geometry", "description": "design-check only: Zero or negative control/graphic dimension"},
    "QPDK042": {"severity": IssueSeverity.WARNING, "phase": 2, "category": "ui-geometry", "description": "design-check only: Control extends more than 6 px past the page background"},
    "QPDK043": {"severity": IssueSeverity.INFO,    "phase": 3, "category": "ui-geometry", "description": "design-check only: Control rect straddles an origin axis (partially off-canvas)"},
    "QPDK044": {"severity": IssueSeverity.INFO,    "phase": 3, "category": "ui-geometry", "description": "design-check only: Position or Size is not a whole number"},
    "QPDK045": {"severity": IssueSeverity.INFO,    "phase": 3, "category": "ui-typo",     "description": "design-check only: Style string is not a known Q-SYS style name (possible typo)"},
    "QPDK046": {"severity": IssueSeverity.INFO,    "phase": 3, "category": "ui-geometry", "description": "design-check only: Control is smaller than 14 px, the smallest control seen in shipped plugins"},
    # QPDK047: advisory only (phase 3 / full ruleset). PluginInfo.Manufacturer is
    # a managed-submission convention QSC's own evaluator warns on; the vendor
    # corpus shows 0/19 real plugins set it, so INFO (not WARNING) is the honest
    # calibration: surfaced in `full`, silent in `default`.
    "QPDK047": {"severity": IssueSeverity.INFO,    "phase": 3, "category": "structure",  "description": "PluginInfo.Manufacturer not set (QSC's evaluator warns when it is missing)"},
    # QPDK048: structural validation of an encrypted .qplugx/.qplugx2 artifact
    # (envelope JSON shape, base64 + length invariants; NO decryption). Phase 1
    # so it runs in every ruleset: a malformed encrypted artifact is broken
    # regardless of level. Only fires for .qplugx/.qplugx2 files.
    "QPDK048": {"severity": IssueSeverity.ERROR,   "phase": 1, "category": "structure",  "description": "Malformed .qplugx/.qplugx2 encryption envelope"},
    # QPDK049/050: control references checked against the EVALUATED GetControls
    # (design-time mode, default properties). Both are load/run crashes on a
    # Core, so ERROR in every ruleset. Literal names only; see control_refs.py.
    "QPDK049": {"severity": IssueSeverity.ERROR,   "phase": 1, "category": "api",        "description": "Controls reference to a name GetControls does not declare (nil on a Core)"},
    "QPDK050": {"severity": IssueSeverity.ERROR,   "phase": 1, "category": "api",        "description": "Count=1 control used as an array (indexed with #, [i] or ipairs): ERROR if single in every configuration, WARNING if a property can make it an array"},
}


# Retired codes: never emitted and never reused. Kept out of RULE_METADATA so
# `qpdk rules` lists only live rules.
RETIRED_RULES: dict[str, str] = {
    "QPDK006": "undefined global; luacheck W111/W112/W113 own this check",
    "QPDK007": "unused variable; luacheck W211/W311 own this check",
}

# Rules that run under `qpdk design-check` (the evaluated layout), not under
# `qpdk validate`. Their descriptions start with "design-check only:".
DESIGN_CHECK_RULES: frozenset[str] = frozenset(
    code for code, meta in RULE_METADATA.items() if meta["category"].startswith("ui-")
)


# --- Ruleset definitions ---
# Each ruleset defines which rules are included.

def _rules_for_phases(phases: set[int]) -> set[str]:
    """Return rule codes active in the given phases."""
    return {code for code, meta in RULE_METADATA.items() if meta["phase"] in phases}


# P0: Only crash-preventing rules (phase 1)
RULESET_P0: set[str] = _rules_for_phases({1})

# Default: All ERROR + WARNING rules (phases 1 and 2)
RULESET_DEFAULT: set[str] = _rules_for_phases({1, 2})

# Full: All rules including INFO-level advisories (all phases)
RULESET_FULL: set[str] = _rules_for_phases({1, 2, 3})


RULESETS: dict[str, set[str]] = {
    "P0": RULESET_P0,
    "default": RULESET_DEFAULT,
    "full": RULESET_FULL,
}


def get_ruleset(name: str) -> set[str]:
    """Get the set of rule codes for a named ruleset.

    Args:
        name: Ruleset name ("P0", "default", or "full").

    Returns:
        Set of QPDK rule codes active in that ruleset.

    Raises:
        ValueError: If the ruleset name is not recognized.
    """
    key = next((k for k in RULESETS if k.lower() == name.lower()), None)
    if key is None:
        valid = ", ".join(sorted(RULESETS.keys()))
        raise ValueError(f"Unknown ruleset '{name}'. Valid rulesets: {valid}")
    return RULESETS[key]


def get_rule_severity(code: str) -> IssueSeverity | None:
    """Get the configured severity for a rule code.

    Returns None if the code is not in RULE_METADATA (e.g. LUACHECK codes).
    """
    meta = RULE_METADATA.get(code)
    return meta["severity"] if meta else None


def get_rule_phase(code: str) -> int | None:
    """Get the rollout phase for a rule code."""
    meta = RULE_METADATA.get(code)
    return meta["phase"] if meta else None
