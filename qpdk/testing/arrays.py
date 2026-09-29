"""Array-control derivation and Lua-side proxies.

The truth about which controls are arrays lives in the plugin's own
GetControls Count fields — hand-maintained maps rot silently (the ani22
harness died for a month when a UI overhaul added stepper arrays its
conftest list didn't know about). Derivation reads the plugin the way
Designer does, so the map can never drift from the source.
"""

from qpdk.mock_runtime.runtime import QSYSRuntime


def derive_with_runtime(
    source: str,
    prop_overrides: dict | None = None,
) -> tuple[dict[str, int], QSYSRuntime, list[dict]]:
    """Derive the array map AND the control schema from GetControls.

    Returns (arrays, runtime, schema): schema is a list of plain-dict rows
    {Name, Count, PinStyle, ButtonType, UserPin} — the plugin's own GetControls
    truth, ready for QSYSRuntime.apply_control_schema (Both-handler mode).

    Callers that keep making runtimes afterwards (PluginHarness) must PIN the
    returned runtime for their lifetime: letting it die mid-session is the
    dead-runtime cyclic-GC scenario behind the lupa address-reuse hazard
    (see runtime.py _wrapped_controls note / KNOWN-ISSUE-lupa-flake).
    """
    rt = QSYSRuntime(design_time=True)
    # prop_overrides: evaluate GetControls at the configuration under test,
    # not only the plugin's declared defaults (array counts and conditional
    # controls usually come from properties).
    report = rt.run_design_time(source, prop_overrides=prop_overrides)
    if report.errors:
        raise RuntimeError(
            "design-time evaluation failed (plugin would crash at Designer "
            f"drag-in): {report.errors}"
        )
    arrays: dict[str, int] = {}
    schema: list[dict] = []
    for _, row in (report.controls or {}).items():
        count = row["Count"] or 1
        if count > 1:
            arrays[row["Name"]] = int(count)
        schema.append({
            "Name": row["Name"],
            "Count": int(count),
            "PinStyle": row["PinStyle"],
            "ButtonType": row["ButtonType"],
            "UserPin": bool(row["UserPin"]),
        })
    return arrays, rt, schema


def derive_array_controls(source: str) -> dict[str, int]:
    """Derive {control_name: count} for every Count>1 control in GetControls.

    Strict: raises RuntimeError if design-time evaluation reports ANY error —
    a plugin that crashes at Designer drag-in has no trustworthy control
    table, and masking that here would hide a real defect (the MXA920
    PageNames class).
    """
    arrays, _rt, _schema = derive_with_runtime(source)
    return arrays


# Lua proxy from the two proven conftests (ani22 == vitec):
# Controls.name[i] delegates to the flat runtime namespace Controls["name i"].
#
# COUNT IS ENFORCED (V5 fix, 2026-07-12): the original proxy accepted its
# count arg but never used it — combined with the mock auto-creating any
# control it is asked for, EVERY numeric index resolved, so
# ipairs(Controls[arr]) never hit nil and never terminated (hung the first
# probe-kit smoke run, internal notes gap 4). Out-of-
# range indices now return nil, bounding ipairs at count exactly like the
# real Controls array, and __len reports count so #Controls[arr] works too.
_PROXY_LUA = """
function(ctrl_name, count)
    local proxy = setmetatable({}, {
        __index = function(t, i)
            if type(i) == 'number' then
                if i < 1 or i > count then return nil end
                return Controls[ctrl_name .. ' ' .. i]
            end
            return nil
        end,
        __newindex = function(t, i, v) end,
        __len = function() return count end
    })
    rawset(Controls, ctrl_name, proxy)
end
"""


def inject_array_proxies(runtime: QSYSRuntime, arrays: dict[str, int]) -> None:
    """Install Controls.<name>[i] proxies for every derived array control.

    MUST run BEFORE load_plugin: plugins legitimately index array controls
    in their main chunk (ani22 does), and post-load injection cannot rescue
    a load that already crashed. Proxies are a testing concern — they stay
    here, not in the mock.
    """
    if not arrays:
        return
    proxy_lua = runtime._lua.eval(_PROXY_LUA)
    for name, count in arrays.items():
        proxy_lua(name, count)
