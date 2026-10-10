"""
Mock Controls API

Simulates Q-SYS Controls table for plugin testing.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


def handler_arg(handler: Any, py_obj: Any) -> Any:
    """The object an EventHandler receives: the Lua wrapper for a Lua
    function (what plugin code compares against), the Python mock for a
    Python callable (what Python-side test handlers expect)."""
    lua_self = getattr(py_obj, "lua_self", None)
    if lua_self is not None and type(handler).__name__ == "_LuaFunction":
        return lua_self
    return py_obj


@dataclass
class MockControl:
    """A mock Q-SYS control."""
    name: str = ""
    value: float = 0.0
    string: str = ""
    position: float = 0.0
    ramp_time: float = 0.0
    is_invisible: bool = False
    is_disabled: bool = False
    is_indeterminate: bool = False
    legend: str = ""
    color: str = ""
    css_class: str = ""
    choices: list[str] = field(default_factory=list)

    # Optional min/max clamping (MR-P2-B)
    # Real Q-SYS enforces Min/Max on Knob controls.
    # Set these to enable value clamping in the mock.
    min_value: float | None = None
    max_value: float | None = None

    # Control-schema metadata (from GetControls). Stored for fidelity/future
    # modeling. NOTE: no pin-exposure behavior is asserted from these — the
    # Trigger+Input/PinStyle=Both runtime semantics are unverified and must not
    # be fabricated in the mock (see internal notes).
    button_type: str | None = None
    pin_style: str | None = None
    user_pin: bool = False

    # Opt-in Both-handler mode (field-proven 2026-07-07, Designer 10.3 + Core
    # Nano cascade-matrix probe): Lua writes to a PinStyle="Both" control DO
    # fire its EventHandler on a real Core. Default False keeps the historical
    # mock behavior (no fire on Lua writes) so existing suites are unchanged;
    # QSYSRuntime(writes_fire_both_handlers=True) + apply_control_schema()
    # set this per Both-pin control. Non-Both controls stay unverified — the
    # mode never fires them.
    writes_fire_handlers: bool = False
    # Re-entrancy depth for handler-triggered writes: a handler that writes
    # a firing control re-fires synchronously. Real Core dispatch depth is
    # unverified; the cap only converts a runaway write cascade (an echo
    # loop the write-guard pattern exists to prevent) into a recorded error
    # instead of a Python RecursionError across the lupa boundary.
    _write_fire_depth: int = 0
    WRITE_FIRE_DEPTH_CAP = 16

    # Event handlers
    _event_handlers: list[Callable] = field(default_factory=list)
    # Optional sink for async EventHandler errors (wired by the runtime). When
    # set, handler exceptions are recorded here so a runner can detect them
    # instead of them being silently swallowed to stdout.
    error_sink: list | None = None
    # The Lua-side wrapper the runtime hands to plugin code for this control
    # (set by QSYSRuntime._wrap_control). Lua EventHandlers receive it, so
    # identity dispatch (`if ctl == Controls.a`) and ctl.Index work as in the
    # Controls_IO.htm examples.
    lua_self: Any = field(default=None, repr=False, compare=False)
    # .Index aspect: element index for array members, nil for single controls
    # (Control_Aspects_Metadata.htm: "Only applicable when control is part of
    # an array of controls").
    index: int | None = None

    @property
    def Boolean(self) -> bool:
        """Get boolean value (value != 0)."""
        return self.value != 0

    @Boolean.setter
    def Boolean(self, val: bool) -> None:
        """Set boolean value.

        Fires EventHandlers only when writes_fire_handlers is set (Both-pin
        controls under the opt-in mode — real Cores DO fire them, probe-proven
        2026-07-07). Otherwise silent, the historical mock behavior. Use
        simulate_external_write() to simulate external stimuli in tests.
        """
        self.value = 1.0 if val else 0.0
        self._fire_lua_write()

    @property
    def Value(self) -> float:
        return self.value

    @Value.setter
    def Value(self, val: float) -> None:
        val = float(val)
        # MR-P2-B: Clamp to Min/Max if set (real Q-SYS enforces this on Knob controls)
        if self.min_value is not None and val < self.min_value:
            val = self.min_value
        if self.max_value is not None and val > self.max_value:
            val = self.max_value
        self.value = val
        self.position = self.value
        # Fires only under the opt-in Both-handler mode (see Boolean setter).
        self._fire_lua_write()

    @property
    def String(self) -> str:
        return self.string

    @String.setter
    def String(self, val: str) -> None:
        self.string = str(val)
        # Fires only under the opt-in Both-handler mode (see Boolean setter).
        self._fire_lua_write()

    @property
    def Position(self) -> float:
        return self.position

    @Position.setter
    def Position(self, val: float) -> None:
        self.position = float(val)
        # Fires only under the opt-in Both-handler mode (see Boolean setter).
        self._fire_lua_write()

    @property
    def RampTime(self) -> float:
        return self.ramp_time

    @RampTime.setter
    def RampTime(self, val: float) -> None:
        self.ramp_time = float(val)

    @property
    def IsInvisible(self) -> bool:
        return self.is_invisible

    @IsInvisible.setter
    def IsInvisible(self, val: bool) -> None:
        self.is_invisible = bool(val)

    @property
    def IsDisabled(self) -> bool:
        return self.is_disabled

    @IsDisabled.setter
    def IsDisabled(self, val: bool) -> None:
        self.is_disabled = bool(val)

    @property
    def IsIndeterminate(self) -> bool:
        return self.is_indeterminate

    @IsIndeterminate.setter
    def IsIndeterminate(self, val: bool) -> None:
        self.is_indeterminate = bool(val)

    @property
    def Legend(self) -> str:
        return self.legend

    @Legend.setter
    def Legend(self, val: str) -> None:
        self.legend = str(val)

    @property
    def Color(self) -> str:
        return self.color

    @Color.setter
    def Color(self, val: str) -> None:
        self.color = str(val)

    @property
    def Choices(self) -> list[str]:
        return self.choices

    @Choices.setter
    def Choices(self, val: list[str]) -> None:
        self.choices = list(val)

    @property
    def EventHandler(self) -> Callable | None:
        return self._event_handlers[0] if self._event_handlers else None

    @EventHandler.setter
    def EventHandler(self, handler: Callable | None) -> None:
        self._event_handlers = [handler] if handler else []

    def Trigger(self) -> None:
        """Trigger the control (for buttons).

        In real Q-SYS, calling ctrl:Trigger() from Lua does NOT fire the
        control's own EventHandler. This is a no-op in the mock to match
        hardware behavior. Use simulate_trigger() in tests to simulate
        an external button press.
        """
        # No-op: real Q-SYS Trigger() from Lua does not fire EventHandlers
        pass

    def simulate_external_write(self, value: float) -> None:
        """Simulate an external stimulus (UI interaction, pin signal).

        Sets the value AND fires EventHandlers, matching what happens when
        a user interacts with a control or an upstream component writes to it.
        Use this in tests instead of setting .Value directly when you want
        to test EventHandler behavior.
        """
        val = float(value)
        # MR-P2-B: Clamp to Min/Max if set
        if self.min_value is not None and val < self.min_value:
            val = self.min_value
        if self.max_value is not None and val > self.max_value:
            val = self.max_value
        self.value = val
        self.position = self.value
        self._trigger_events()

    def simulate_trigger(self) -> None:
        """Simulate an external trigger (button press from UI/schematic).

        Fires EventHandlers, matching what happens when a user presses a
        button or an external component triggers the control.
        Use this in tests instead of calling .Trigger() when you want to
        test EventHandler behavior.
        """
        self._trigger_events()

    def _fire_lua_write(self) -> None:
        """Fire EventHandlers for a Lua-originated write, when enabled.

        No-op unless writes_fire_handlers is set (opt-in Both-handler mode).
        Depth-capped: a handler that keeps writing firing controls (missing
        write-guard = the echo-loop defect class this mode exists to expose)
        is recorded to the error sink instead of blowing the Python stack
        under lupa.
        """
        if not self.writes_fire_handlers:
            return
        if self._write_fire_depth >= self.WRITE_FIRE_DEPTH_CAP:
            msg = (f"write-cascade depth cap ({self.WRITE_FIRE_DEPTH_CAP}) on "
                   f"{self.name}: a handler keeps re-writing firing controls "
                   "— unguarded Both-pin write (echo loop)")
            if self.error_sink is not None:
                self.error_sink.append(msg)
            print(msg)
            return
        self._write_fire_depth += 1
        try:
            self._trigger_events()
        finally:
            self._write_fire_depth -= 1

    def _trigger_events(self) -> None:
        """Call all registered event handlers."""
        for handler in self._event_handlers:
            if handler:
                try:
                    handler(handler_arg(handler, self))
                except Exception as e:
                    msg = f"EventHandler error on {self.name}: {e}"
                    if self.error_sink is not None:
                        self.error_sink.append(msg)
                    print(msg)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "Name": self.name,
            "Value": self.value,
            "String": self.string,
            "Position": self.position,
            "RampTime": self.ramp_time,
            "IsInvisible": self.is_invisible,
            "IsDisabled": self.is_disabled,
            "IsIndeterminate": self.is_indeterminate,
            "Legend": self.legend,
            "Color": self.color,
            "Choices": self.choices,
            "Min": self.min_value,
            "Max": self.max_value,
        }


class MockControlGroup:
    """A group of controls (Inputs, Outputs, etc.)."""

    def __init__(self, error_sink: list | None = None):
        self._controls: dict[str, MockControl] = {}
        self.error_sink = error_sink

    def __getitem__(self, key: str | int) -> MockControl:
        """Get control by name or index."""
        if isinstance(key, int):
            keys = list(self._controls.keys())
            if 0 <= key < len(keys):
                return self._controls[keys[key]]
            raise IndexError(f"Control index {key} out of range")
        if key not in self._controls:
            # Create on demand (Q-SYS behavior)
            self._controls[key] = MockControl(name=key, error_sink=self.error_sink)
        return self._controls[key]

    def __setitem__(self, key: str, value: MockControl) -> None:
        """Set a control."""
        self._controls[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._controls

    def __len__(self) -> int:
        return len(self._controls)

    def __iter__(self):
        return iter(self._controls.values())

    def keys(self):
        return self._controls.keys()

    def values(self):
        return self._controls.values()

    def items(self):
        return self._controls.items()

    def add(self, name: str, **kwargs) -> MockControl:
        """Add a control."""
        kwargs.setdefault("error_sink", self.error_sink)
        ctrl = MockControl(name=name, **kwargs)
        self._controls[name] = ctrl
        return ctrl


class MockControls:
    """
    Mock implementation of the Q-SYS Controls table.

    Supports both legacy sub-table access and production flat access:
        controls.Inputs["MyButton"].Boolean = True   # Legacy/test harness
        controls["MyButton"].Boolean = True           # Production flat access
        controls.MyButton.Boolean = True              # Production attribute access

    Flat access (bracket and attribute) uses get_or_create(), which creates
    controls on demand for test convenience. That is NOT Core behavior: once
    declare() has been called (qpdk.testing does it from GetControls), plugin
    lookups of undeclared names return nil, as on a Core.
    """

    # Reserved attribute names that should NOT be treated as control names
    _RESERVED = frozenset({"Inputs", "Outputs"})

    def __init__(self, error_sink: list | None = None):
        self.error_sink = error_sink
        self.Inputs = MockControlGroup(error_sink=error_sink)
        self.Outputs = MockControlGroup(error_sink=error_sink)
        # Names GetControls declared, or None when unknown. When set, plugin
        # (Lua) lookups of any other name return nil, as on a Core, where
        # Controls holds only declared controls (Controls_IO.htm). Python-side
        # access from tests stays permissive.
        self.declared: set[str] | None = None

    def declare(self, names) -> None:
        """Restrict plugin-side lookups to these control names."""
        self.declared = set(names)

    def is_declared(self, name) -> bool:
        return self.declared is None or name in self.declared

    def get_or_create(self, name: str) -> MockControl:
        """Get a control by name from any group, creating in Inputs if not found.

        Searches Inputs first, then Outputs. If not found in either,
        auto-creates in Inputs (matching Q-SYS behavior where Controls
        is a flat namespace).
        """
        if name in self.Inputs._controls:
            return self.Inputs._controls[name]
        if name in self.Outputs._controls:
            return self.Outputs._controls[name]
        # Auto-create in Inputs (Q-SYS auto-creates controls on demand)
        return self.Inputs[name]

    def __getitem__(self, key: str) -> MockControl:
        """Bracket access: Controls['name'] — flat namespace."""
        return self.get_or_create(key)

    def __getattr__(self, name: str) -> MockControl:
        """Attribute access: Controls.name — flat namespace.

        Only intercepts names that are not reserved (Inputs, Outputs)
        and not private/dunder attributes.
        """
        if name.startswith("_") or name in MockControls._RESERVED:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return self.get_or_create(name)

    def add_input(self, name: str, **kwargs) -> MockControl:
        """Add an input control."""
        return self.Inputs.add(name, **kwargs)

    def add_output(self, name: str, **kwargs) -> MockControl:
        """Add an output control."""
        return self.Outputs.add(name, **kwargs)

    def reset(self) -> None:
        """Reset all controls to default values."""
        for ctrl in self.Inputs:
            ctrl.value = 0.0
            ctrl.string = ""
            ctrl.position = 0.0
        for ctrl in self.Outputs:
            ctrl.value = 0.0
            ctrl.string = ""
            ctrl.position = 0.0
