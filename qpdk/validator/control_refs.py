"""QPDK049 / QPDK050: control references checked against evaluated GetControls.

Control names and counts only exist after GetControls runs (names are often
built in loops, counts often come from properties), so these rules evaluate
the plugin the way Designer does (mock design-time mode, default property
values) and compare the result with the literal Controls references in the
runtime code.

- QPDK049: a literal Controls.<name> / Controls["name"] that GetControls never
  declares. On a Core that lookup is nil and the next index crashes
  (Controls_IO.htm: "attempt to index ... (a nil value)").
- QPDK050: a Count=1 control used as an array (#Controls.x, Controls.x[i],
  ipairs(Controls.x)). A single control is userdata, not a table; this was
  confirmed on a Core.

Limits (by design): QPDK049 accepts a name declared under the defaults or any
single-property variant (plus an all-max run); QPDK050 judges Count at the
default configuration. Only literal names are checked. Names built at run time are not seen. If
the plugin's own GetControls fails or declares no controls, both rules stay
silent (the crash itself is design-check's job).
"""

import re
from dataclasses import dataclass

_IDENT_REF = re.compile(r"\bControls\s*\.\s*([A-Za-z_]\w*)")
_STRING_REF = re.compile(r"""\bControls\s*\[\s*(["'])((?:(?!\1)[^\\\n])*)\1\s*\]""")
_RESERVED = {"Inputs", "Outputs"}


@dataclass
class ControlRef:
    name: str
    line: int
    start: int   # offset of "Controls"
    end: int     # offset just past the reference


def _evaluate(source: str, overrides: dict | None = None):
    """Run GetControls in design-time mode.

    Plugin Lua errors are collected in the report, not raised. Any exception
    that escapes is a QPDK bug, so it propagates: the validator reports it as
    a QPDK000 ERROR rather than letting QPDK049/050 go silently quiet.
    """
    from qpdk.mock_runtime.runtime import QSYSRuntime
    return QSYSRuntime(design_time=True).run_design_time(source, prop_overrides=overrides)


def _controls_of(report) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in (report.controls or {}).values():
        name = row["Name"]
        if not name:
            continue
        try:
            out[str(name)] = int(row["Count"] or 1)
        except (TypeError, ValueError):
            out[str(name)] = 1
    return out


def _property_variants(report) -> list[dict]:
    """Alternative property configurations a user could set in Designer.

    One run with every numeric property at its Max and every boolean true,
    then one run per single-property change: each enum choice, the boolean
    flipped, a numeric at Min and at Max. Enough to surface controls a
    property switches on (e.g. optional widget controls) without a full
    combinatorial sweep.
    """
    variants: list[dict] = []
    all_max: dict = {}
    for entry in (report.properties.values() if report.properties is not None else []):
        try:
            name, ptype, value = entry["Name"], entry["Type"], entry["Value"]
        except Exception:
            continue
        if name is None:
            continue
        if ptype in ("integer", "double"):
            for bound in ("Min", "Max"):
                b = entry[bound]
                if isinstance(b, (int, float)) and b != value:
                    variants.append({name: b})
            if isinstance(entry["Max"], (int, float)):
                all_max[name] = entry["Max"]
        elif ptype == "boolean":
            variants.append({name: not bool(value)})
            all_max[name] = True
        elif ptype == "enum" and entry["Choices"] is not None:
            for choice in entry["Choices"].values():
                if choice != value:
                    variants.append({name: choice})
    if all_max:
        variants.insert(0, all_max)
    return variants


@dataclass
class DeclaredControls:
    default: dict[str, int]   # {name: count} at default properties
    any_config: set[str]      # names declared under ANY evaluated configuration
    max_count: dict[str, int]  # {name: largest Count seen in any configuration}


def declared_controls(source: str) -> DeclaredControls | None:
    """GetControls evaluated at defaults and across property variants.

    None when the default evaluation fails or declares nothing (unknown).
    """
    base = _evaluate(source)
    if base is None or base.errors or not base.controls:
        return None
    default = _controls_of(base)
    names = set(default)
    max_count = dict(default)
    for overrides in _property_variants(base):
        rep = _evaluate(source, overrides)
        if rep is not None and not rep.errors and rep.controls:
            for name, count in _controls_of(rep).items():
                names.add(name)
                max_count[name] = max(count, max_count.get(name, 0))
    return DeclaredControls(default=default, any_config=names, max_count=max_count)


def find_refs(raw: str, code_view: str) -> list[ControlRef]:
    """Literal Controls references that sit in real code (not strings/comments).

    code_view is the validator's string/comment-blanked copy of raw with the
    same layout, so a match is real code exactly when "Controls" survives at
    the same offset in code_view.
    """
    refs: list[ControlRef] = []
    for rx, group in ((_IDENT_REF, 1), (_STRING_REF, 2)):
        for m in rx.finditer(raw):
            if code_view[m.start():m.start() + 8] != "Controls":
                continue
            name = m.group(group)
            if name in _RESERVED:
                continue
            line = raw.count("\n", 0, m.start()) + 1
            refs.append(ControlRef(name, line, m.start(), m.end()))
    refs.sort(key=lambda r: r.start)
    return refs


def _is_declared(name: str, declared: DeclaredControls) -> bool:
    if name in declared.any_config:
        return True
    # Flat element name of an array control: "gain 3" is valid only while
    # 3 is within the largest Count any configuration gives "gain".
    m = re.fullmatch(r"(.+) (\d+)", name)
    return bool(m and 1 <= int(m.group(2)) <= declared.max_count.get(m.group(1), 0))


_BLOCK_WORD = re.compile(r"\b(function|if|do|repeat|end|until)\b")


def _block_end(code_view: str, start: int, depth: int) -> int:
    """Offset where the block open at `start` closes (end of file if never)."""
    for m in _BLOCK_WORD.finditer(code_view, start):
        if m.group(1) in ("end", "until"):
            depth -= 1
            if depth <= 0:
                return m.end()
        else:
            depth += 1
    return len(code_view)


def guarded(refs: list[ControlRef], raw: str, code_view: str) -> set[int]:
    """Offsets of references that are nil tests or sit behind one.

    A nil test (Controls.X followed by then/and/or/do, or preceded by not) is
    itself safe on a Core. A positive test guards later references to the same
    name inside its if/elseif/while block, or to the end of the line for
    `Controls.X and ...`. References outside those spans stay checked, so an
    unguarded access elsewhere is still reported.
    """
    safe: set[int] = set()
    spans: list[tuple[str, int, int]] = []
    for r in refs:
        before = raw[max(0, r.start - 64):r.start]
        after = raw[r.end:r.end + 64]
        follow = re.match(r"\s*(then|and|or|do|~=\s*nil|==\s*nil)\b", after)
        negated = re.search(r"\bnot\s*$", before)
        if not (follow or negated):
            continue
        safe.add(r.start)
        if negated:
            continue
        kw = re.search(r"\b(if|elseif|while)\s*$", before)
        # follow is set here: negated was False, so the guard above needed follow
        if kw and follow is not None and follow.group(1) in ("then", "and", "do"):
            kw_pos = r.start - (len(before) - kw.start())
            depth = 1 if kw.group(1) == "elseif" else 0
            spans.append((r.name, r.end, _block_end(code_view, kw_pos if depth == 0 else r.end, depth)))
        elif follow is not None and follow.group(1) == "and":
            eol = raw.find("\n", r.end)
            spans.append((r.name, r.end, len(raw) if eol < 0 else eol))
    for r in refs:
        if any(name == r.name and lo <= r.start < hi for name, lo, hi in spans):
            safe.add(r.start)
    return safe


def undeclared(refs: list[ControlRef], declared: DeclaredControls,
               safe: set[int] | None = None) -> list[ControlRef]:
    safe = safe or set()
    return [r for r in refs
            if not _is_declared(r.name, declared) and r.start not in safe]


def scalar_as_array(raw: str, refs: list[ControlRef],
                    declared: DeclaredControls) -> list[tuple[ControlRef, str, bool]]:
    """References to controls that are Count=1 at default properties, used
    with #, [index] or ipairs (the configuration Designer drags in with).

    Each hit is (ref, use, always_single). always_single is True when no
    evaluated configuration makes the control an array: that use can never
    work. When a property can raise the Count, the code may only run in that
    configuration, so the caller should report it less severely.
    """
    hits: list[tuple[ControlRef, str, bool]] = []
    for r in refs:
        if declared.default.get(r.name) != 1:
            continue
        # A short window each side is enough to see #, [ and ipairs( ... ),
        # and keeps this linear in file size (slicing the whole prefix per
        # reference was quadratic: 1 s on a plugin with 540 references).
        before = raw[max(0, r.start - 64):r.start].rstrip()
        after = raw[r.end:r.end + 64].lstrip()
        always = declared.max_count.get(r.name, 1) <= 1
        # "#Controls.x.String" measures the text, not the control: only a bare
        # reference (nothing indexed or called after it) is the crash.
        if before.endswith("#") and not re.match(r"[.:\[]", after):
            hits.append((r, "#", always))
        elif re.match(r"\[\s*[^\]\"']", after):
            hits.append((r, "index", always))
        elif re.search(r"\bi?pairs\s*\(\s*$", before) and after.startswith(")"):
            use = "ipairs" if re.search(r"\bipairs\s*\(\s*$", before) else "pairs"
            hits.append((r, use, always))
    return hits
