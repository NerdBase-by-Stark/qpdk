"""UI-LINT (QPDK040-046): geometry lint over EVALUATED GetControlLayout.

Positions are computed by Lua loops and arithmetic — they only exist after
executing GetControlLayout, so these rules run on a DesignTimeReport (the
design-time path), not inside the static PluginValidator.

Every threshold is corpus-mined and locked by tests/test_ui_lint.py:
- 9-plugin corpus (2 QSC-shipped + 6 user production + 1 known-buggy):
  zero findings on every working plugin; MXA920 page 1 yields exactly the
  5 confirmed QPDK042 right-edge overflows.
- Rules with no corpus true-positive are invariants ("never occurs in a
  working plugin") locked by mutation tests.
Provenance, fragility notes, and the DO-NOT-BUILD list live in
internal notes Part 1.

Limitations (by design): rules see the DEFAULT property configuration's
layout only; evaluated tables carry no source line numbers (findings carry
control name + page index instead); severity ceiling is WARNING — no
geometry defect in this family was shown to break plugin load.
"""

from typing import Any

# Corpus-observed Style strings (build spec §1.4). "None" is the shipped
# pin-only idiom (1,159 production occurrences) — REQUIRED member; Led/LED
# both ship. APPEND when the corpus grows; never treat as closed/authoritative.
UI_LINT_VOCABULARY = frozenset({
    "None", "Button", "Text", "Led", "LED", "Fader", "Knob",
    "ComboBox", "Meter", "Slider", "Text Box", "Textbox",
})

# QPDK042: max observed production overflow is 6px (vitec, 1 control);
# MXA920's defect is 10px. Two-point boundary — widen, never tighten.
_EDGE_TOLERANCE_PX = 6

# QPDK046: smallest on-canvas control dimension shipped is 14px.
_MIN_DIMENSION_PX = 14

_BACKGROUND_TYPES = ("Header", "GroupBox")


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _rect(entry) -> tuple[float, float, float, float] | None:
    """(x, y, w, h) if the entry carries usable Position+Size, else None."""
    try:
        pos, size = entry["Position"], entry["Size"]
        if pos is None or size is None:
            return None
        x, y = _num(pos[1]), _num(pos[2])
        w, h = _num(size[1]), _num(size[2])
    except Exception:
        return None
    if None in (x, y, w, h):
        return None
    return (x, y, w, h)


def _is_parked(x, y, w, h) -> bool:
    """Fully off-canvas 'parking' idiom: exempt from 040/042/043/046."""
    return x + w <= 0 or y + h <= 0


def _iter_layout_entries(layout):
    if layout is None:
        return
    try:
        items = list(layout.items())
    except Exception:
        return
    for name, entry in items:
        if entry is None:
            continue
        yield str(name), entry


def _iter_graphics(graphics):
    if graphics is None:
        return
    try:
        values = list(graphics.values())
    except Exception:
        return
    for entry in values:
        if entry is not None:
            yield entry


def lint_page(layout, graphics, page: int) -> list[dict[str, Any]]:
    """Run QPDK040-046 over one evaluated page. Returns finding dicts:
    {code, severity, name, page, message}."""
    findings: list[dict[str, Any]] = []

    def add(code, severity, name, message):
        findings.append({"code": code, "severity": severity, "name": name,
                         "page": page, "message": message})

    controls = []       # (name, x, y, w, h) — positive-size, positioned
    all_rects = []      # (name, kind, x, y, w, h) incl. graphics, for 041/044
    for name, entry in _iter_layout_entries(layout):
        style = entry["Style"]
        rect = _rect(entry)
        if rect is None:
            # Positionless pin-only idiom (1,159 shipped) — skipped everywhere.
            continue
        x, y, w, h = rect
        all_rects.append((name, "control", x, y, w, h))
        # QPDK045 — vocabulary typo check (positioned entries with a Style key)
        if style is not None and str(style) not in UI_LINT_VOCABULARY:
            add("QPDK045", "info", name,
                f"Style {str(style)!r} is not in the corpus-verified "
                f"vocabulary — possible typo (the set is corpus-bounded, "
                f"not authoritative)")
        if w <= 0 or h <= 0:
            # QPDK041; degenerate rects excluded from the other geometry rules.
            add("QPDK041", "warning", name,
                f"degenerate size {w:g}x{h:g} (zero or negative dimension)")
            continue
        controls.append((name, x, y, w, h))

    graphic_rects = []
    for i, entry in enumerate(_iter_graphics(graphics), start=1):
        rect = _rect(entry)
        if rect is None:
            continue
        x, y, w, h = rect
        gtype = entry["Type"]
        name = f"<graphic {i}: {gtype}>"
        all_rects.append((name, "graphic", x, y, w, h))
        if w <= 0 or h <= 0:
            add("QPDK041", "warning", name,
                f"degenerate size {w:g}x{h:g} (zero or negative dimension)")
            continue
        graphic_rects.append((str(gtype) if gtype is not None else "", x, y, w, h))

    # QPDK044 — non-integral coordinates (value check, not type: 39.0 ships)
    for name, _kind, x, y, w, h in all_rects:
        if any(v != int(v) for v in (x, y, w, h)):
            add("QPDK044", "info", name,
                f"non-integral Position/Size component "
                f"({x:g},{y:g} {w:g}x{h:g}) — un-floored arithmetic?")

    on_canvas = [(n, x, y, w, h) for (n, x, y, w, h) in controls
                 if not _is_parked(x, y, w, h)]

    # QPDK040 — control/control overlap (same page, positive area)
    for i in range(len(on_canvas)):
        for j in range(i + 1, len(on_canvas)):
            n1, x1, y1, w1, h1 = on_canvas[i]
            n2, x2, y2, w2, h2 = on_canvas[j]
            ox = min(x1 + w1, x2 + w2) - max(x1, x2)
            oy = min(y1 + h1, y2 + h2) - max(y1, y2)
            if ox > 0 and oy > 0:
                add("QPDK040", "warning", n1,
                    f"overlaps control {n2!r} by {ox:g}x{oy:g}px "
                    f"(zero overlaps in all 9 corpus plugins)")

    # QPDK042 — X overflow past the background span (Header/GroupBox proxy)
    backgrounds = [(x, x + w) for (t, x, y, w, h) in graphic_rects
                   if t in _BACKGROUND_TYPES]
    if backgrounds:
        bg_min = min(b[0] for b in backgrounds)
        bg_max = max(b[1] for b in backgrounds)
        for n, x, y, w, h in on_canvas:
            if x + w > bg_max + _EDGE_TOLERANCE_PX:
                add("QPDK042", "warning", n,
                    f"right edge {x + w:g} exceeds the background span "
                    f"({bg_max:g}) by more than {_EDGE_TOLERANCE_PX}px")
            elif x < bg_min - _EDGE_TOLERANCE_PX:
                add("QPDK042", "warning", n,
                    f"left edge {x:g} is more than {_EDGE_TOLERANCE_PX}px "
                    f"left of the background span ({bg_min:g})")

    for n, x, y, w, h in controls:
        if _is_parked(x, y, w, h):
            continue
        # QPDK043 — origin-axis straddle (partially off-canvas)
        if x < 0 < x + w or y < 0 < y + h:
            add("QPDK043", "info", n,
                f"rect ({x:g},{y:g} {w:g}x{h:g}) straddles an origin axis — "
                f"sign/offset arithmetic bug?")
        # QPDK046 — sliver control
        if min(w, h) < _MIN_DIMENSION_PX:
            add("QPDK046", "info", n,
                f"dimension {min(w, h):g}px is below the smallest shipped "
                f"on-canvas control ({_MIN_DIMENSION_PX}px)")

    return findings


def lint_report(report) -> list[dict[str, Any]]:
    """Run UI-LINT over every successfully evaluated page of a
    DesignTimeReport. Pages whose GetControlLayout crashed are SKIPPED —
    the crash is design-check's finding; rules never run on partial tables.
    """
    findings: list[dict[str, Any]] = []
    pages = report.pages or []
    if not pages and report.layout is not None:
        pages = [{"index": 1, "layout": report.layout,
                  "graphics": report.graphics}]
    for page in pages:
        if page["layout"] is None:
            continue
        findings.extend(
            lint_page(page["layout"], page["graphics"], page["index"]))
    return findings
