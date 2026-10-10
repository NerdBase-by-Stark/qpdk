"""Run QSC's official Plugin-Evaluation-Script against a .qplug.

Wraps the vendored ``evaluateDesignTime.lua`` (see vendor/VENDOR.md) in the
same lupa Lua 5.3 runtime the mock runtime uses — the upstream repo bundles
lua53 because that is what Q-SYS Designer runs at design time. The upstream
bash driver's jq-layer checks are reimplemented in Python here, semantics
preserved (see _bash_layer_checks), so the whole evaluation is portable.

Severity mapping into qpdk terms: QSC Errors -> ERROR, Warnings -> WARNING,
Notices -> INFO. All findings carry rule id "QSC-EVAL".
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

VENDOR_DIR = Path(__file__).parent / "vendor"


@dataclass
class QscEvalResult:
    plugin_path: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    export: dict | None = None          # full executed-surface JSON (Controls/Layout/...)
    crash: str | None = None            # Lua error while executing design-time code

    @property
    def ok(self) -> bool:
        return self.crash is None and not self.errors

    def to_dict(self) -> dict:
        return {
            "plugin": self.plugin_path,
            "ok": self.ok,
            "crash": self.crash,
            "errors": self.errors,
            "warnings": self.warnings,
            "notices": self.notices,
        }


def _load_exceptions(exceptions_path: str | None) -> dict:
    """Upstream PluginEvalConfig.json 'Exceptions' shape (see vendor README)."""
    if not exceptions_path:
        return {}
    with open(exceptions_path, encoding="utf-8") as fh:
        return json.load(fh).get("Exceptions", {})


def run_qsc_eval(
    plugin_path: str,
    prop_overrides: dict[str, str] | None = None,
    maxprops: bool = False,
    exceptions_path: str | None = None,
) -> QscEvalResult:
    import lupa.lua53 as lua53

    plugin_path = os.path.abspath(plugin_path)
    result = QscEvalResult(plugin_path=plugin_path)

    L = lua53.LuaRuntime()
    L.execute(
        'package.path = "{0}/?.lua;" .. package.path'.format(
            str(VENDOR_DIR).replace("\\", "/")
        )
    )
    # Capture the script's stdout (it prints one JSON document at the end).
    captured: list[str] = []
    L.globals()["_py_collect"] = lambda s: captured.append(str(s))
    L.execute("print = function(...) local t={...} "
              "for i=1,#t do _py_collect(tostring(t[i])) end end")

    args: list[str] = [plugin_path]
    if maxprops:
        args.append("-maxprops")
    for name, value in (prop_overrides or {}).items():
        args.append(f"{name.replace(' ', '%20')}={value}")

    entry = str(VENDOR_DIR / "evaluateDesignTime.lua").replace("\\", "/")
    try:
        loader = L.eval(f'loadfile("{entry}")')
        loader(*args)
    except Exception as exc:  # Lua error inside plugin design-time code
        result.crash = _clean_crash(str(exc))
        return result

    if not captured:
        result.crash = "evaluator produced no output"
        return result

    doc = json.loads(captured[-1])
    result.export = doc
    result.errors = list(doc.get("Errors") or [])
    result.warnings = list(doc.get("Warnings") or [])
    result.notices = list(doc.get("Notices") or [])

    _bash_layer_checks(doc, result, _load_exceptions(exceptions_path))
    return result


# An error raised at a line of the vendored evaluator itself (not inside the
# plugin's code). Lua shortens long chunk names with a leading "...", so match
# on the file name only.
_EVALUATOR_LOC = re.compile(r"\S*evaluateDesignTime\.lua:(\d+):\s*")
_MISSING_GLOBAL = re.compile(r"attempt to call a nil value \(global '(\w+)'\)")


def _clean_crash(msg: str) -> str:
    """Rewrite evaluator-originated errors so they do not blame the plugin
    or print an install path."""
    first = _EVALUATOR_LOC.match(msg)
    missing = _MISSING_GLOBAL.match(msg, first.end()) if first else None
    if missing is not None:
        return (f"the QSC evaluator requires a global {missing.group(1)}() "
                "function; the plugin does not define one")
    return _EVALUATOR_LOC.sub(
        lambda m: f"QSC evaluator (evaluateDesignTime.lua:{m.group(1)}): ", msg)


def _bash_layer_checks(doc: dict, result: QscEvalResult, exceptions: dict) -> None:
    """Reimplementation of EvaluatePlugin.sh's jq checks (upstream lines 45-101).

    Semantics preserved from upstream; each block cites its upstream origin.
    """
    controls = doc.get("Controls") or {}
    layout = doc.get("Layout") or {}
    pinless_ok = set(exceptions.get("PinlessControls") or [])
    prettyless_ok = set(exceptions.get("PrettyNamelessControls") or [])
    allow_spaces = exceptions.get("AllowSpacesInControlNames") is True

    # Controls without PrettyName (upstream: Notices; keyed on Layout entries)
    for name in sorted(layout):
        item = layout[name]
        if isinstance(item, dict) and item.get("PrettyName") is None \
                and name not in prettyless_ok:
            result.notices.append(f"Control without PrettyName: {name}")

    for name in sorted(controls):
        ctrl = controls[name]
        pin_style = ctrl.get("PinStyle")
        if pin_style is None and name not in pinless_ok:
            result.errors.append(f"Control without PinStyle: {name}")
        elif pin_style == "None" and name not in pinless_ok:
            result.notices.append(f"Control PinStyle set to None: {name}")
        if "." in name:
            result.warnings.append(f"Control with period in Name: {name}")
        if " " in name and not allow_spaces:
            result.warnings.append(f"Control with space in Name: {name}")

    # Declared controls with no layout entry (upstream: Errors; null layout slots)
    for name in sorted(layout):
        if layout[name] is None:
            result.errors.append(f"Control without layout definition: {name}")
