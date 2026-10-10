"""
Q-SYS Mock Runtime

A Lua sandbox that simulates Q-SYS APIs for offline plugin testing.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .controls import MockControl, MockControlGroup, MockControls
from .ssh import MockSsh, SshAPI
from .tcp_socket import MockTcpSocket, TcpSocketAPI
from .timer import TimerAPI, get_global_scheduler, reset_global_scheduler


@dataclass
class DesignTimeReport:
    """Result of a design-time evaluation (QSYSRuntime.run_design_time).

    errors is the deliverable: empty means the plugin survived Designer-style
    evaluation with Controls nil. properties/controls/layout hold the raw Lua
    return values of GetProperties/GetControls/GetControlLayout when those
    calls succeeded (lupa table objects — index like Lua: controls[1]["Name"]).
    """
    errors: list[str] = field(default_factory=list)
    properties: Any = None
    controls: Any = None
    layout: Any = None    # page 1 layout (back-compat)
    graphics: Any = None  # page 1 graphics (2nd GetControlLayout return)
    # One entry per evaluated page: {"index": n, "layout": ..., "graphics": ...}
    # (layout/graphics None for a page whose GetControlLayout call crashed).
    pages: list = field(default_factory=list)


class MockLog:
    """Mock Log API with capture."""

    def __init__(self):
        self.messages: list[dict[str, Any]] = []
        self.capture = True

    def Message(self, message: str) -> None:
        if self.capture:
            self.messages.append({"level": "message", "text": message})
        print(f"[LOG] {message}")

    def Error(self, message: str) -> None:
        if self.capture:
            self.messages.append({"level": "error", "text": message})
        print(f"[ERROR] {message}", file=sys.stderr)

    def Warning(self, message: str) -> None:
        raise RuntimeError(
            "Log.Warning() does not exist in Q-SYS API. "
            "Use Log.Message() or Log.Error(). "
            "This would crash on hardware."
        )

    def Debug(self, message: str) -> None:
        raise RuntimeError(
            "Log.Debug() does not exist in Q-SYS API. "
            "Use Log.Message() or Log.Error()."
        )

    def clear(self) -> None:
        self.messages.clear()


class MockProperties:
    """Mock Properties table."""

    def __init__(self):
        self._props: dict[str, Any] = {}

    def __getitem__(self, key: str) -> Any:
        return self._props.get(key, {"Value": None})

    def __setitem__(self, key: str, value: Any) -> None:
        self._props[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._props

    def set(self, key: str, value: Any) -> None:
        """Set a property value."""
        self._props[key] = {"Value": value}

    def get(self, key: str, default: Any = None) -> Any:
        """Get a property value."""
        prop = self._props.get(key)
        if prop is None:
            return default
        if isinstance(prop, dict):
            return prop.get("Value", default)
        return prop


class MockSystem:
    """Mock System API."""

    def __init__(self):
        self.IsEmulating = True
        self.MajorVersion = 9
        self.MinorVersion = 10
        self.BuildVersion = 0
        self.LockingId = ""

    def ListenPort(self, port: int) -> None:
        """Mock listen port (no-op)."""
        pass


class MockDesign:
    """Mock Design API: the values Design.GetStatus() returns (Design.htm)."""

    def __init__(self):
        self.DesignName = "Mock Design"
        self.DesignCode = "MOCK001"
        self.Platform = "Core 110f"
        self.IsRedundant = False


# Process-lifetime pin for every Python callable handed into any Lua table.
# See QSYSRuntime.__init__ (_closure_refs) for the hazard analysis.
_PROCESS_CLOSURE_PIN: list = []


class QSYSRuntime:
    """
    Q-SYS Mock Runtime

    Creates a sandboxed Lua environment with Q-SYS APIs mocked.

    Usage:
        runtime = QSYSRuntime()
        runtime.load_plugin("plugin.qplug")
        runtime.controls.Inputs["Button"].Boolean = True
        runtime.tick(1.0)  # Advance time by 1 second
    """

    def __init__(self, design_time: bool = False,
                 writes_fire_both_handlers: bool = False):
        # Opt-in Both-handler mode (observed on a Core with Designer 10.3):
        # on a real Core, Lua writes to PinStyle="Both"
        # controls DO fire their EventHandlers — the mock historically never
        # fired on Lua writes, so mock-green tests could not catch echo loops
        # a real Core executes. With this True, apply_control_schema() arms
        # Both-pin controls to fire on Lua writes. Default False: existing
        # suites keep the historical (silent) behavior.
        self.writes_fire_both_handlers = writes_fire_both_handlers
        # Design-time mode: Designer evaluates the plugin file with
        # `Controls` nil — the one verified design-time difference (a plugin
        # whose GetControlLayout indexed Controls crashed on drag-in; the
        # same mechanism QPDK012/025 guard). With design_time=True the Controls global is NOT injected,
        # so `if Controls then` correctly evaluates false and unguarded
        # Controls indexing raises exactly as Designer would crash. Other
        # runtime globals stay injected: their design-time availability is
        # not KB-confirmed, so removing them would assert an unverified nil.
        self.design_time = design_time
        # Async error sink: EventHandler / timer-callback crashes are recorded
        # here (not just printed) so a test runner can detect them via
        # had_errors(). Load-time errors still raise from load_string().
        self.errors: list[str] = []
        # Cache of wrapped Lua control tables, keyed by MockControl id().
        # Ensures each MockControl gets exactly ONE Lua wrapper for its lifetime,
        # preventing Lua GC + Python address-reuse from confusing stale metatable
        # Python-callable references with newly-allocated closures at the same
        # address (the "wrap_control GC hazard").
        self._wrapped_controls: dict[int, Any] = {}
        # Caches of wrapped Lua tables for timers, TCP sockets, and SSH objects,
        # keyed by the Python id() of the underlying mock object.  Same hazard
        # family as _wrapped_controls: without these, the index/newindex Python
        # closures stored as Lua metatable upvalues participate in cyclic-garbage
        # graphs (runtime → _lua → Lua metatable → idx_fn → closure → tcp_obj →
        # _LuaFunction.Data → back to _lua).  When cyclic GC bulk-frees 20+ dead
        # runtimes, their closures' addresses are reused by newly-allocated objects
        # in the live runtime, and lupa's stale Lua metatable upvalue pointers land
        # on the wrong Python callable.  Retaining a strong Python-side reference
        # here breaks the hazard: the closures stay alive as long as `self` does.
        self._wrapped_timers: dict[int, Any] = {}
        self._wrapped_tcp_sockets: dict[int, Any] = {}
        self._wrapped_ssh_objs: dict[int, Any] = {}
        # Flat list of all index/newindex/sub-closures passed into Lua metatables
        # for timer/tcp/ssh wrappers.  Holds strong Python refs so CPython address-
        # reuse cannot confuse lupa even if Lua were to temporarily drop a wrapper.
        # PROCESS-LIFETIME pin (module-level list, shared by all runtimes):
        # per-runtime pinning proved insufficient — when a dead runtime's
        # closures are cyclically collected while another runtime is live,
        # lupa's stale upvalue pointers can land on whatever Python object
        # reuses the address ("'MockTimer' object is not callable",
        # reproduced deterministically in a downstream suite). Never freeing any
        # Lua-bound callable makes address reuse impossible. Cost: ~40 small
        # objects leaked per runtime, bounded by runtimes-per-process (test
        # sessions build a few hundred at most).
        self._closure_refs: list[Any] = _PROCESS_CLOSURE_PIN
        # Initialize mocks
        self.controls = MockControls(error_sink=self.errors)
        self.properties = MockProperties()
        self.log = MockLog()
        self.system = MockSystem()
        self.design = MockDesign()

        # Timer system
        reset_global_scheduler()
        self.timer_api = TimerAPI()
        self._scheduler = get_global_scheduler()

        # SSH and TcpSocket APIs
        self.ssh_api = SshAPI()
        self.tcp_api = TcpSocketAPI()

        # Plugin info extracted from loaded plugin
        self.plugin_info: dict[str, Any] = {}

        # Captured HttpClient verb calls: (VERB, lua request table). Tests
        # assert the request and drive the response via the captured table's
        # EventHandler.
        self.http_requests: list[tuple[str, Any]] = []

        # Lua runtime (lazy init)
        self._lua = None
        self._lua_globals = None

        # Print capture
        self._print_output: list[str] = []

    def had_errors(self) -> bool:
        """True if any async EventHandler or timer callback crashed during the
        run. These paths are otherwise only printed; load-time errors raise
        directly from load_string()."""
        return bool(self.errors) or bool(getattr(self._scheduler, "errors", []))

    @property
    def error_messages(self) -> list[str]:
        """All recorded async errors (control handlers + timer callbacks)."""
        return list(self.errors) + list(getattr(self._scheduler, "errors", []))

    def _ref(self, fn):
        """Hold a strong Python-side ref for any callable handed into a Lua
        table. Same lupa GC address-reuse hazard as _wrapped_controls: without
        this, cyclic GC can free the callable and a stale Lua pointer lands on
        whatever Python object reuses the address (observed as nondeterministic
        "'MockTcpSocket' object is not callable" in downstream suites)."""
        self._closure_refs.append(fn)
        return fn

    def _init_lua(self) -> None:
        """Initialize Lua runtime with Q-SYS globals."""
        try:
            # Q-SYS Core runs Lua 5.3 (help.qsys.com Lua 5.3 Reference Manual).
            # Pin lupa to the 5.3 build so 5.4-only syntax (e.g. <close>) doesn't
            # pass the mock yet fail on a real Core.
            try:
                from lupa.lua53 import LuaRuntime
            except ImportError:
                from lupa import LuaRuntime  # fallback to default lupa build
        except ImportError:
            raise ImportError(
                "lupa is required for mock runtime. Install with: pip install lupa"
            ) from None

        self._lua = LuaRuntime(unpack_returned_tuples=True)
        g = self._lua.globals()
        self._lua_globals = g

        # Override print
        def mock_print(*args):
            text = " ".join(str(a) for a in args)
            self._print_output.append(text)
            print(f"[PRINT] {text}")

        g.print = mock_print

        # Inject Q-SYS globals. In design-time mode Controls stays nil (see
        # __init__): that is what makes the drag-in crash class reproducible.
        if not self.design_time:
            self._inject_controls_api(g)
        self._inject_timer_api(g)
        self._inject_log_api(g)
        self._inject_properties_api(g)
        self._inject_system_api(g)
        self._inject_design_api(g)
        self._inject_ssh_api(g)
        self._inject_tcp_socket_api(g)
        self._inject_network_api(g)
        self._inject_rapidjson_shim(g)
        self._inject_reflect_global(g)
        self._inject_stub_apis(g)

    def _inject_controls_api(self, g) -> None:
        """Inject Controls global.

        Supports both flat access (Controls.name / Controls["name"]) and
        legacy sub-table access (Controls.Inputs["name"] / Controls.Outputs["name"]).
        Flat access is the standard Q-SYS production pattern.
        """
        controls = self._lua.table()

        # Create Inputs proxy (legacy/test harness access)
        inputs_table = self._lua.table()
        self._setup_control_group_proxy(inputs_table, self.controls.Inputs)

        # Create Outputs proxy (legacy/test harness access)
        outputs_table = self._lua.table()
        self._setup_control_group_proxy(outputs_table, self.controls.Outputs)

        # Set up metatable for flat access (Controls.name / Controls["name"])
        # Must use Lua-native metatable with Python callback wrapped in Lua function
        # because lupa doesn't recognize Python functions set directly on a Lua table
        # as metamethods.
        def index(t, key):
            # Check if it's a known sub-table first
            if key == "Inputs":
                return inputs_table
            elif key == "Outputs":
                return outputs_table
            # Flat access: Controls.control_name / Controls["name with spaces"]
            if not self.controls.is_declared(key):
                return None  # undeclared: nil on a Core too
            ctrl = self.controls.get_or_create(key)
            return self._wrap_control(ctrl)

        def newindex(t, key, value):
            # Allow setting sub-tables via rawset
            if key in ("Inputs", "Outputs"):
                self._lua.execute("rawset(..., ..., ...)", t, key, value)

        # Use Lua-side metatable setup to ensure metamethods work correctly
        setup_mt = self._lua.eval("""
        function(t, idx_fn, ni_fn, inputs, outputs)
            rawset(t, 'Inputs', inputs)
            rawset(t, 'Outputs', outputs)
            setmetatable(t, {
                __index = function(self, key)
                    -- Check rawget first for Inputs/Outputs
                    local raw = rawget(self, key)
                    if raw ~= nil then return raw end
                    return idx_fn(self, key)
                end,
                __newindex = function(self, key, value)
                    return ni_fn(self, key, value)
                end
            })
            return t
        end
        """)
        setup_mt(controls, index, newindex, inputs_table, outputs_table)
        g.Controls = controls

    def _setup_control_group_proxy(self, lua_table, control_group: MockControlGroup) -> None:
        """Set up a Lua table that proxies to a control group."""

        def index_handler(table, key):
            ctrl = control_group[key]
            return self._wrap_control(ctrl)

        # Use Lua-side metatable setup for proper metamethod handling
        setup_mt = self._lua.eval("""
        function(t, idx_fn)
            setmetatable(t, {
                __index = function(self, key)
                    return idx_fn(self, key)
                end
            })
        end
        """)
        setup_mt(lua_table, index_handler)

    def _wrap_control(self, ctrl: MockControl):
        """Wrap a MockControl for Lua access.

        Returns a cached Lua table for the given MockControl. The cache
        (self._wrapped_controls, keyed by id(ctrl)) guarantees each control
        gets exactly one Lua wrapper for its lifetime, avoiding the Lua GC +
        Python address-reuse hazard where short-lived wrappers cause metatable
        Python-callable references to point at newly-allocated closures that
        happen to share the same address as the now-freed original closures.
        """
        ctrl_id = id(ctrl)
        if ctrl_id in self._wrapped_controls:
            return self._wrapped_controls[ctrl_id]
        wrapped = self._lua.table()
        ctrl.lua_self = wrapped

        def index(t, key):
            key_lower = key.lower() if isinstance(key, str) else key
            if key == "Value" or key_lower == "value":
                return ctrl.value
            elif key == "String" or key_lower == "string":
                return ctrl.string
            elif key == "Boolean" or key_lower == "boolean":
                return ctrl.Boolean
            elif key == "Position" or key_lower == "position":
                return ctrl.position
            elif key == "RampTime":
                return ctrl.ramp_time
            elif key == "IsInvisible":
                return ctrl.is_invisible
            elif key == "IsDisabled":
                return ctrl.is_disabled
            elif key == "Legend":
                return ctrl.legend
            elif key == "Color":
                return ctrl.color
            elif key == "Choices":
                return self._lua.table(*ctrl.choices)
            elif key == "Index":
                return ctrl.index
            elif key == "IsIndeterminate":
                return ctrl.is_indeterminate
            elif key == "CssClass":
                return ctrl.css_class
            elif key == "EventHandler":
                return ctrl._event_handlers[0] if ctrl._event_handlers else None
            elif key == "Trigger":
                # No-op in the mock. Script writes do not fire the control's
                # own handler (Control_Aspects_Metadata.htm); whether :Trigger
                # does anything else on a Core is KB-silent.
                return self._lua.eval("function() end")
            return None

        def newindex(t, key, value):
            key_lower = key.lower() if isinstance(key, str) else key
            if key == "Value" or key_lower == "value":
                ctrl.Value = value
            elif key == "String" or key_lower == "string":
                ctrl.String = value
            elif key == "Boolean" or key_lower == "boolean":
                ctrl.Boolean = value
            elif key == "Position" or key_lower == "position":
                ctrl.Position = value
            elif key == "RampTime":
                ctrl.RampTime = value
            elif key == "IsInvisible":
                ctrl.IsInvisible = value
            elif key == "IsDisabled":
                ctrl.IsDisabled = value
            elif key == "Legend":
                ctrl.Legend = value
            elif key == "Color":
                ctrl.Color = value
            # Metadata aspects (Control_Aspects_Metadata.htm).
            elif key == "Choices":
                ctrl.Choices = list(value.values()) if hasattr(value, "values") else list(value)
            elif key == "IsIndeterminate":
                ctrl.IsIndeterminate = value
            elif key == "CssClass":
                ctrl.css_class = str(value)
            elif key == "EventHandler":
                ctrl.EventHandler = value

        # Use Lua-side metatable setup for proper metamethod handling.
        # lupa doesn't recognize Python functions set directly on a lua.table()
        # as metamethods -- must use Lua-native metatable construction.
        setup_mt = self._lua.eval("""
        function(t, idx_fn, ni_fn)
            setmetatable(t, {
                __index = function(self, key)
                    -- A single control is userdata on a Core, not an array:
                    -- confirmed on a Core, Controls.x[1] raises this exact error.
                    if type(key) == 'number' then
                        error("Property '" .. key .. "' does not exist on Control", 2)
                    end
                    return idx_fn(self, key)
                end,
                __newindex = function(self, key, value)
                    return ni_fn(self, key, value)
                end,
                -- #Controls.x on a single control raises on a Core too.
                __len = function()
                    error("attempt to get length of a userdata value", 2)
                end
            })
            return t
        end
        """)
        setup_mt(wrapped, index, newindex)

        # Process-lifetime pin: closures AND the mock object itself. Dead
        # runtimes' unpinned mock instances free, and their addresses get
        # reused by objects that later marshal through lupa — the stale-cache
        # misroute behind "'MockTimer' object is not callable".
        self._closure_refs.extend([index, newindex, ctrl])

        # Cache so subsequent accesses reuse the same Lua table and its
        # metatable closures, preventing the GC address-reuse hazard.
        self._wrapped_controls[ctrl_id] = wrapped
        return wrapped

    def _inject_timer_api(self, g) -> None:
        """Inject Timer global."""
        timer_table = self._lua.table()

        def create():
            timer = self.timer_api.New()
            return self._wrap_timer(timer)

        def call_after(fn, delay):
            return self.timer_api.CallAfter(fn, delay)

        def now(*_args):
            # Timer.htm: Timer.Now() returns seconds since the epoch. The mock
            # returns virtual scheduler time starting at 0 instead, so
            # elapsed-time maths stays deterministic under tick(); values are
            # not comparable with os.time() or usable with os.date().
            return float(self.current_time)

        timer_table.New = create      # Timer.New() is the documented constructor (Sys/Timer.htm)
        timer_table.CallAfter = call_after
        timer_table.Now = now
        # Timer.Create does not exist on a Core (KB-contradicted, Sys/Timer.htm
        # lists New/CallAfter/Now only). Calling it must fail here too, or a
        # plugin written against the old mock passes and then nil-calls at load.
        self._lua.execute("""
            local t = ...
            t.Create = function()
                error("Timer.Create() does not exist in the Q-SYS API; use Timer.New()", 2)
            end
        """, timer_table)
        self._closure_refs.extend([create, call_after, now])

        g.Timer = timer_table

    def _wrap_timer(self, timer):
        """Wrap a MockTimer for Lua access.

        Returns a cached wrapper if one already exists for this timer object.
        Caching (self._wrapped_timers keyed by id(timer)) prevents the GC
        address-reuse hazard: the Python closures held as Lua metatable
        upvalues remain alive as long as the QSYSRuntime is alive.
        """
        timer_id = id(timer)
        if timer_id in self._wrapped_timers:
            return self._wrapped_timers[timer_id]

        wrapped = self._lua.table()
        timer.lua_self = wrapped

        def start(*args):
            # Support both t.Start(interval) and t:Start(interval).
            # Lua colon-call passes self as first arg: t:Start(1.0) -> start(t, 1.0)
            if len(args) == 2:
                interval = args[1]  # colon-call: (self, interval)
            elif len(args) == 1:
                interval = args[0]  # dot-call: (interval)
            else:
                raise TypeError(f"Start() expects 1 argument (interval), got {len(args)}")
            timer.Start(interval)

        def stop(*args):
            # Support both t.Stop() and t:Stop()
            timer.Stop()

        def is_running(*args):
            # Support both t.IsRunning() and t:IsRunning().
            # Timer.htm: ":IsRunning() Returns true if the timer is running".
            return timer.IsRunning()

        def get_handler(*_args):
            return timer.EventHandler

        def set_handler(*args):
            # (value) from the Lua closure below
            timer.EventHandler = args[-1]

        # Retain strong Python references to all closures + the mock object so
        # CPython's cyclic GC cannot free them (address-reuse hazard family).
        self._closure_refs.extend([start, stop, is_running, get_handler,
                                   set_handler, timer])

        # Method dispatch happens in LUA: Python callables cross the lupa
        # boundary exactly ONCE (as setup upvalues) and __index returns
        # LUA closures. Returning a Python callable from __index re-marshals
        # it on every access, and that per-call marshal is where lupa
        # misroutes under multi-runtime GC churn ("'MockTimer' object is not
        # callable" — the returned Start resolved to the timer itself).
        setup_mt = self._lua.eval("""
        function(t, start_fn, stop_fn, is_running_fn, get_eh, set_eh)
            local methods = {
                Start = function(self_or_interval, maybe_interval)
                    -- support t:Start(x) and t.Start(x)
                    if maybe_interval ~= nil then
                        return start_fn(maybe_interval)
                    end
                    return start_fn(self_or_interval)
                end,
                Stop = function(_self)
                    return stop_fn()
                end,
                IsRunning = function(_self)
                    return is_running_fn()
                end,
            }
            setmetatable(t, {
                __index = function(self, key)
                    if key == 'EventHandler' then return get_eh() end
                    return methods[key]
                end,
                __newindex = function(self, key, value)
                    if key == 'EventHandler' then set_eh(value) end
                end
            })
            return t
        end
        """)
        setup_mt(wrapped, start, stop, is_running, get_handler, set_handler)

        self._wrapped_timers[timer_id] = wrapped
        return wrapped

    def _inject_log_api(self, g) -> None:
        """Inject Log global."""
        log_table = self._lua.table()
        log_table.Message = self._ref(self.log.Message)
        log_table.Error = self._ref(self.log.Error)
        log_table.Warning = self._ref(self.log.Warning)
        log_table.Debug = self._ref(self.log.Debug)
        g.Log = log_table

    def _inject_properties_api(self, g) -> None:
        """Inject Properties global.

        The __index metamethod is installed FROM LUA (the same pattern
        Controls uses above): assigning it as a lupa table attribute
        (`mt.__index = fn`) never registers the metamethod, which left
        Properties inert in runtime mode — every Lua read returned nil,
        and seeded test values were silently ignored.

        Missing-key semantics (KB-undocumented): an UNDECLARED
        property reads as nil from Lua, matching plain-Lua-table behavior
        (inference — on hardware Properties is a table built from the
        declared GetProperties entries; not hardware-verified). The
        Python-side MockProperties.__getitem__ keeps its legacy
        {"Value": None} shape for back-compat.
        """
        props_table = self._lua.table()

        def index(t, key):
            if key not in self.properties:
                return None  # nil in Lua for an undeclared key
            val = self.properties[key]
            if isinstance(val, dict):
                # Return as Lua table
                lua_val = self._lua.table()
                for k, v in val.items():
                    lua_val[k] = v
                return lua_val
            return val

        setup_mt = self._lua.eval("""
        function(t, idx_fn)
            setmetatable(t, {
                __index = function(self, key) return idx_fn(self, key) end
            })
            return t
        end
        """)
        setup_mt(props_table, index)
        # Strong Python-side ref: same lupa GC address-reuse hazard as the
        # control/timer/socket wrapper closures.
        self._closure_refs.append(index)
        g.Properties = props_table

    def _inject_system_api(self, g) -> None:
        """Inject System global."""
        system_table = self._lua.table()
        system_table.IsEmulating = self.system.IsEmulating
        system_table.MajorVersion = self.system.MajorVersion
        system_table.MinorVersion = self.system.MinorVersion
        system_table.BuildVersion = self.system.BuildVersion
        system_table.LockingId = self.system.LockingId
        system_table.ListenPort = self._ref(lambda port: None)
        g.System = system_table

    def _inject_design_api(self, g) -> None:
        """Inject Design global: Design.GetStatus() returns a table with
        DesignName, Platform, IsRedundant and DesignCode; Design.GetInventory()
        returns a table of inventory items (Design.htm). The mock design has
        no inventory, so GetInventory() returns an empty table."""
        design_table = self._lua.table()

        def get_status(*_args):
            return self._lua.table_from({
                "DesignName": self.design.DesignName,
                "Platform": self.design.Platform,
                "IsRedundant": self.design.IsRedundant,
                "DesignCode": self.design.DesignCode,
            })

        design_table.GetStatus = self._ref(get_status)
        design_table.GetInventory = self._ref(lambda *_a: self._lua.table())
        g.Design = design_table

    def _inject_ssh_api(self, g) -> None:
        """Inject Ssh global.

        Provides Ssh.New() which returns a mock SSH object with all 7 callbacks,
        Connect/Disconnect/Write/Read methods, and test harness simulate_* methods.
        """
        ssh_table = self._lua.table()
        ssh_api = self.ssh_api

        def ssh_new():
            ssh_obj = ssh_api.New()
            return self._wrap_ssh(ssh_obj)

        ssh_table.New = ssh_new
        g.Ssh = ssh_table

    def _wrap_ssh(self, ssh_obj: MockSsh):
        """Wrap a MockSsh for Lua access.

        Returns a cached wrapper if one already exists for this SSH object.
        Caching (self._wrapped_ssh_objs keyed by id(ssh_obj)) prevents the GC
        address-reuse hazard: same family as _wrap_timer / _wrap_tcp_socket.
        """
        ssh_id = id(ssh_obj)
        if ssh_id in self._wrapped_ssh_objs:
            return self._wrapped_ssh_objs[ssh_id]

        wrapped = self._lua.table()

        def index(t, key):
            if key == "IsConnected":
                return ssh_obj.IsConnected
            elif key == "BufferLength":
                return ssh_obj.BufferLength
            elif key == "Connected":
                return ssh_obj.Connected
            elif key == "Reconnect":
                return ssh_obj.Reconnect
            elif key == "Data":
                return ssh_obj.Data
            elif key == "Closed":
                return ssh_obj.Closed
            elif key == "Error":
                return ssh_obj.Error
            elif key == "Timeout":
                return ssh_obj.Timeout
            elif key == "LoginFailed":
                return ssh_obj.LoginFailed
            elif key in ("ReadTimeout", "WriteTimeout", "ReconnectTimeout", "IsInteractive"):
                return getattr(ssh_obj, key)
            return None

        # Method callables defined ONCE, marshaled ONCE at setup (see the
        # tcp wrapper note on the lupa misroute).
        def connect(*args):
            host = args[0] if len(args) > 0 else None
            port = args[1] if len(args) > 1 else None
            user = args[2] if len(args) > 2 else None
            password = args[3] if len(args) > 3 else None
            ssh_obj.Connect(host, port, user, password)

        def disconnect(*args):
            ssh_obj.Disconnect()

        def write(*args):
            if not args:
                raise TypeError("Write() requires data argument")
            ssh_obj.Write(args[0])

        def read(*args):
            n = int(args[0]) if args else -1
            return ssh_obj.Read(n)

        def readline(*args):
            eol = args[0] if len(args) > 0 else None
            custom = args[1] if len(args) > 1 else None
            return ssh_obj.ReadLine(eol, custom)

        def newindex(t, key, value):
            if key == "Connected":
                ssh_obj.Connected = value
            elif key == "Reconnect":
                ssh_obj.Reconnect = value
            elif key == "Data":
                ssh_obj.Data = value
            elif key == "Closed":
                ssh_obj.Closed = value
            elif key == "Error":
                ssh_obj.Error = value
            elif key == "Timeout":
                ssh_obj.Timeout = value
            elif key == "LoginFailed":
                ssh_obj.LoginFailed = value
            # Timeout config must persist: falling through here silently
            # dropped ReadTimeout/WriteTimeout/ReconnectTimeout assignments.
            # Mirrors TcpSocket.
            elif key in ("ReadTimeout", "WriteTimeout", "ReconnectTimeout", "IsInteractive"):
                setattr(ssh_obj, key, value)

        # Retain strong Python references to all closures so CPython's cyclic GC
        # cannot free them (and make their addresses available for reuse) while
        # this runtime is still alive.
        self._closure_refs.extend([index, newindex, connect, disconnect, write, read, readline, ssh_obj])

        # Use Lua-side metatable setup for proper metamethod handling
        setup_mt = self._lua.eval("""
        function(t, idx_fn, ni_fn, connect_fn, disconnect_fn, write_fn, read_fn, readline_fn)
            local methods = {
                Connect = function(self, host, port, user, password)
                    if type(self) ~= 'table' then
                        host, port, user, password = self, host, port, user
                    end
                    return connect_fn(host, port, user, password)
                end,
                Disconnect = function(_self) return disconnect_fn() end,
                Write = function(self, data)
                    if data == nil and type(self) ~= 'table' then data = self end
                    return write_fn(data)
                end,
                Read = function(self, n)
                    if n == nil and type(self) ~= 'table' then n = self end
                    return read_fn(n)
                end,
                ReadLine = function(self, eol, custom)
                    if type(self) ~= 'table' then eol, custom = self, eol end
                    return readline_fn(eol, custom)
                end,
            }
            setmetatable(t, {
                __index = function(self, key)
                    local m = methods[key]
                    if m ~= nil then return m end
                    return idx_fn(self, key)
                end,
                __newindex = function(self, key, value)
                    return ni_fn(self, key, value)
                end
            })
            return t
        end
        """)
        setup_mt(wrapped, index, newindex, connect, disconnect, write, read, readline)

        self._wrapped_ssh_objs[ssh_id] = wrapped
        return wrapped

    def _inject_tcp_socket_api(self, g) -> None:
        """Inject TcpSocket global.

        Similar structure to SSH but with 6 callbacks (no LoginFailed).
        """
        tcp_table = self._lua.table()
        tcp_api = self.tcp_api

        def tcp_new():
            tcp_obj = tcp_api.New()
            return self._wrap_tcp_socket(tcp_obj)

        tcp_table.New = tcp_new
        # TcpSocket.EOL keyword table (official six keywords; values are
        # opaque tokens on real Q-SYS — plugins only pass them through to
        # ReadLine, so string tokens suffice; read_line() keys off them).
        eol_table = self._lua.table()
        from .tcp_socket import EOL_KEYWORDS
        for kw in EOL_KEYWORDS:
            eol_table[kw] = kw
        tcp_table.EOL = eol_table
        g.TcpSocket = tcp_table

    def _wrap_tcp_socket(self, tcp_obj: MockTcpSocket):
        """Wrap a MockTcpSocket for Lua access.

        Returns a cached wrapper if one already exists for this socket object.
        Caching (self._wrapped_tcp_sockets keyed by id(tcp_obj)) prevents the GC
        address-reuse hazard: the Python closures held as Lua metatable upvalues
        remain alive (via self._closure_refs) as long as the QSYSRuntime is alive.
        """
        tcp_id = id(tcp_obj)
        if tcp_id in self._wrapped_tcp_sockets:
            return self._wrapped_tcp_sockets[tcp_id]

        wrapped = self._lua.table()

        def index(t, key):
            if key == "IsConnected":
                return tcp_obj.IsConnected
            elif key == "BufferLength":
                return tcp_obj.BufferLength
            elif key == "ReadTimeout":
                return tcp_obj.ReadTimeout
            elif key == "WriteTimeout":
                return tcp_obj.WriteTimeout
            elif key == "ReconnectTimeout":
                return tcp_obj.ReconnectTimeout
            elif key == "Connected":
                return tcp_obj.Connected
            elif key == "Reconnect":
                return tcp_obj.Reconnect
            elif key == "Data":
                return tcp_obj.Data
            elif key == "Closed":
                return tcp_obj.Closed
            elif key == "Error":
                return tcp_obj.Error
            elif key == "Timeout":
                return tcp_obj.Timeout
            return None

        # Method callables are defined ONCE and cross the lupa boundary ONCE
        # (as setup upvalues). Returning a Python callable from __index
        # re-marshals it per access — the misroute point behind
        # "'MockTcpSocket' object is not callable".
        def connect(*args):
            # args already stripped of Lua self by the Lua-side closure
            host = args[0] if len(args) > 0 else None
            port = args[1] if len(args) > 1 else None
            tcp_obj.Connect(host, port)

        def disconnect(*args):
            tcp_obj.Disconnect()

        def write(*args):
            if not args:
                raise TypeError("Write() requires data argument")
            tcp_obj.Write(args[0])

        def read(*args):
            n = int(args[0]) if args else -1
            return tcp_obj.Read(n)

        def readline(*args):
            eol = args[0] if len(args) > 0 else None
            custom = args[1] if len(args) > 1 else None
            return tcp_obj.ReadLine(eol, custom)

        def search(*args):
            needle = args[0] if len(args) > 0 else None
            start = args[1] if len(args) > 1 else None
            return tcp_obj.Search(needle, start if start is not None else 1)

        def newindex(t, key, value):
            if key == "Connected":
                tcp_obj.Connected = value
            elif key == "Reconnect":
                tcp_obj.Reconnect = value
            elif key == "Data":
                tcp_obj.Data = value
            elif key == "Closed":
                tcp_obj.Closed = value
            elif key == "Error":
                tcp_obj.Error = value
            elif key == "Timeout":
                tcp_obj.Timeout = value
            elif key == "ReadTimeout":
                tcp_obj.ReadTimeout = value
            elif key == "WriteTimeout":
                tcp_obj.WriteTimeout = value
            elif key == "ReconnectTimeout":
                tcp_obj.ReconnectTimeout = value

        # Retain strong Python references to all closures so CPython's cyclic GC
        # cannot free them (and make their addresses available for reuse) while
        # this runtime is still alive.
        self._closure_refs.extend([index, newindex, connect, disconnect, write, read, readline,
                                   search, tcp_obj])

        # Use Lua-side metatable setup for proper metamethod handling
        setup_mt = self._lua.eval("""
        function(t, idx_fn, ni_fn, connect_fn, disconnect_fn, write_fn, read_fn, readline_fn, search_fn)
            local methods = {
                Search = function(self, str, start_pos)
                    if type(self) ~= 'table' then str, start_pos = self, str end
                    return search_fn(str, start_pos)
                end,
                Connect = function(self, host, port)
                    if type(self) ~= 'table' then host, port = self, host end
                    return connect_fn(host, port)
                end,
                Disconnect = function(_self) return disconnect_fn() end,
                Write = function(self, data)
                    if data == nil and type(self) ~= 'table' then data = self end
                    return write_fn(data)
                end,
                Read = function(self, n)
                    if n == nil and type(self) ~= 'table' then n = self end
                    return read_fn(n)
                end,
                ReadLine = function(self, eol, custom)
                    if type(self) ~= 'table' then eol, custom = self, eol end
                    return readline_fn(eol, custom)
                end,
            }
            setmetatable(t, {
                __index = function(self, key)
                    local m = methods[key]
                    if m ~= nil then return m end
                    return idx_fn(self, key)
                end,
                __newindex = function(self, key, value)
                    return ni_fn(self, key, value)
                end
            })
            return t
        end
        """)
        setup_mt(wrapped, index, newindex, connect, disconnect, write, read, readline, search)

        self._wrapped_tcp_sockets[tcp_id] = wrapped
        return wrapped

    def _inject_network_api(self, g) -> None:
        """Inject Network global.

        Many plugins call Network.Interfaces() at startup.
        Returns an empty table to prevent nil-call crashes.
        """
        g.Network = self._lua.table()
        g.Network.Interfaces = self._ref(lambda: self._lua.table())

    def _py_to_lua(self, obj, null=None):
        """Recursively convert a Python JSON value to a Lua table/scalar.
        JSON null becomes `null` (rapidjson.null for rapidjson.decode, nil
        for json.decode)."""
        if obj is None:
            return null
        if isinstance(obj, dict):
            return self._lua.table_from({k: self._py_to_lua(v, null) for k, v in obj.items()})
        if isinstance(obj, (list, tuple)):
            return self._lua.table_from([self._py_to_lua(v, null) for v in obj])
        return obj

    def _lua_to_py(self, obj):
        """Recursively convert a Lua table/scalar back to a Python value.

        json.null / rapidjson.null become None. A table marked by
        rapidjson.array() / rapidjson.object() keeps that JSON type. Otherwise
        a Lua table with contiguous 1..n integer keys becomes a list and any
        other table a dict; empty tables become dicts (so encode({}) == "{}")."""
        kind = self._json_kind(obj) if getattr(self, "_json_kind", None) else None
        if kind == "null":
            return None
        if hasattr(obj, "items") and not isinstance(obj, (str, bytes)):
            items = list(obj.items())
            keys = [k for k, _ in items]
            if kind == "object":
                return {str(k): self._lua_to_py(v) for k, v in items}
            if kind == "array" or (keys and all(isinstance(k, int) for k in keys) and
                                   sorted(keys) == list(range(1, len(keys) + 1))):
                return [self._lua_to_py(v) for _, v in sorted(items, key=lambda kv: kv[0])]
            return {str(k): self._lua_to_py(v) for k, v in items}
        return obj

    def _inject_rapidjson_shim(self, g) -> None:
        """Inject the json module (JSON.htm) and rapidjson (RapidJSON.htm),
        both backed by Python's json module with Lua-table conversion.

        json: available as a global and through require("json"); json.null
        is a function that returns itself and encodes as null.
        rapidjson: require("rapidjson"); rapidjson.null decodes from and
        encodes to null; rapidjson.array(t) / rapidjson.object(t) mark a
        table's JSON type through the `__jsontype` metatable field;
        encode(value, {pretty=, sort_keys=}) honours both options; decode
        returns nil plus an error message on invalid JSON.
        """
        import json as _json

        json_null, rj_null, rj_array, rj_object, json_kind = self._lua.eval("""
        function()
            local json_null
            json_null = function() return json_null end
            local rj_null = setmetatable({}, {__tostring = function() return "null" end})
            local function settype(t, kind)
                if t == nil then t = {} end
                local mt = getmetatable(t)
                if mt == nil then
                    mt = {}
                    setmetatable(t, mt)
                end
                mt.__jsontype = kind
                return t
            end
            local function kind(v)
                if v == json_null or v == rj_null then return "null" end
                if type(v) == "table" then
                    local mt = getmetatable(v)
                    if type(mt) == "table" then return mt.__jsontype end
                end
                return nil
            end
            return json_null, rj_null,
                function(t) return settype(t, "array") end,
                function(t) return settype(t, "object") end,
                kind
        end
        """)()
        self._json_kind = json_kind
        self._closure_refs.extend([json_null, rj_null, rj_array, rj_object, json_kind])

        def _dumps(t, opts=None):
            pretty = bool(opts["pretty"]) if opts is not None else False
            sort_keys = bool(opts["sort_keys"]) if opts is not None else False
            value = self._lua_to_py(t)
            if pretty:
                return _json.dumps(value, indent=4, sort_keys=sort_keys)
            # Compact separators match rapidjson's default output.
            return _json.dumps(value, separators=(",", ":"), sort_keys=sort_keys)

        def json_decode(s):
            try:
                return self._py_to_lua(_json.loads(s))
            except Exception:
                return None

        def json_encode(t):
            try:
                return _dumps(t)
            except Exception:
                return None

        def rj_decode(s):
            try:
                return self._py_to_lua(_json.loads(s), null=rj_null)
            except Exception as e:
                return None, str(e)

        def rj_encode(t, opts=None):
            try:
                return _dumps(t, opts)
            except Exception:
                return None

        self._closure_refs.extend([json_decode, json_encode, rj_decode, rj_encode])

        json_tbl = self._lua.table()
        json_tbl.decode = json_decode
        json_tbl.encode = json_encode
        json_tbl.null = json_null
        g.json = json_tbl
        g.package.loaded["json"] = json_tbl

        rapid_tbl = self._lua.table()
        rapid_tbl.decode = rj_decode
        rapid_tbl.encode = rj_encode
        rapid_tbl.null = rj_null
        rapid_tbl.array = rj_array
        rapid_tbl.object = rj_object
        g.package.loaded["rapidjson"] = rapid_tbl

    def _inject_reflect_global(self, g) -> None:
        """Inject Reflect global.

        Reflect is used by some plugins for cross-plugin control binding.
        Current behavior (nil) works by accident but should be explicit.
        Reflect is not supported in mock.
        """
        g.Reflect = None   # explicitly nil -- Reflect not supported in mock

    def _inject_stub_apis(self, g) -> None:
        """Inject stub APIs to prevent nil-call crashes.

        These APIs return empty results. Plugins that use them need stubs
        to avoid nil-call crashes when loading in the mock runtime.

        PinStyle: pin EXPOSURE (does a pin render, does a wire
        drive it) is still unmodeled. But the Both-pin write-echo class is
        now testable: QSYSRuntime(writes_fire_both_handlers=True) +
        apply_control_schema() fires EventHandlers on Lua writes to
        PinStyle="Both" controls, matching behaviour observed on a Core
        (Designer 10.3). QPDK035 is no longer the
        only defense.
        """
        self._inject_http_client_api(g)

        # Component: Component.New(name) returns a chainable control-group proxy
        # so `Component.New("Amp")["gain"].Value = x` works. Controls are
        # auto-created on access; writes are sandboxed (never touch hardware).
        # GetComponents() / GetControls(name) exist (Component.htm) and return
        # empty tables: the mock design has no named components.
        component_table = self._lua.table()
        component_table.New = self._ref(lambda name: self._make_component_instance())
        component_table.GetComponents = self._ref(lambda *_a: self._lua.table())
        component_table.GetControls = self._ref(lambda *_a: self._lua.table())
        g.Component = component_table

        # NamedControl: class statics GetValue/SetValue/GetString/SetString/
        # GetPosition/SetPosition/Trigger (NamedControl.htm), backed by a
        # dedicated auto-creating control group, separate from Controls.
        # The optional ramp-time argument is accepted and ignored. Unlike a
        # Core, an unknown name is created on demand instead of raising.
        self._named_controls = MockControlGroup(error_sink=self.errors)
        nc = self._named_controls
        named_control = self._lua.table()
        named_control.GetValue = self._ref(lambda name: nc[name].value)
        named_control.GetString = self._ref(lambda name: nc[name].string)
        named_control.GetPosition = self._ref(lambda name: nc[name].position)

        def _nc_set_value(name, val, _ramp=None):
            nc[name].Value = val

        def _nc_set_string(name, val):
            nc[name].String = val

        def _nc_set_position(name, val, _ramp=None):
            nc[name].Position = val

        def _nc_trigger(name):
            nc[name].Trigger()

        named_control.SetValue = self._ref(_nc_set_value)
        named_control.SetString = self._ref(_nc_set_string)
        named_control.SetPosition = self._ref(_nc_set_position)
        named_control.Trigger = self._ref(_nc_trigger)
        g.NamedControl = named_control

        # Uci: Uci.OpenPage() as no-op
        uci = self._lua.table()
        uci.OpenPage = self._ref(lambda page: None)
        g.Uci = uci

        # Snapshot: Snapshot.Load() as no-op
        snapshot = self._lua.table()
        snapshot.Load = self._ref(lambda bank, slot: None)
        snapshot.Save = self._ref(lambda bank, slot: None)
        g.Snapshot = snapshot

        # Mixer: All methods as no-ops
        mixer = self._lua.table()
        mixer.New = self._ref(lambda name: self._make_mixer_instance())
        g.Mixer = mixer

        # Audio: All methods as no-ops
        audio = self._lua.table()
        g.Audio = audio

        # SerialPorts: Empty table
        g.SerialPorts = self._lua.table()

        # Gpio: Empty table
        g.Gpio = self._lua.table()

        # UdpSocket: Same pattern as TcpSocket
        udp_table = self._lua.table()
        udp_table.New = self._ref(lambda: self._make_udp_socket_instance())
        g.UdpSocket = udp_table

    def _make_component_instance(self):
        """A Component.New() result: a control-group proxy whose controls are
        auto-created on access, so chained access like
        ``Component.New("Amp")["gain"].Value = x`` works instead of crashing.
        Each call returns an independent component namespace."""
        group = MockControlGroup(error_sink=self.errors)
        proxy = self._lua.table()
        self._setup_control_group_proxy(proxy, group)
        return proxy

    # Verb statics per the official HttpClient doc (HttpClient.htm): static functions taking a request
    # table; EventHandler signature (table, code, data, error, headers).
    _HTTP_VERBS = ("Download", "Upload", "Get", "Post", "Put", "Patch", "Delete")

    def _inject_http_client_api(self, g) -> None:
        """Inject the real statics-shaped HttpClient (HttpClient.htm).

        The previous mock exposed only `.New` — a constructor the official
        doc does not list — so every real call (HttpClient.Get{...},
        HttpClient.Download{...}) crashed on a nil static.
        Verb calls are captured into self.http_requests as (VERB, lua_table)
        so a test can assert the request and drive the response by invoking
        the captured table's EventHandler. Pure helpers (CreateUrl,
        EncodeParams, EncodeString, DecodeString) are behavioral, matching
        the doc's own examples (lowercase percent-escapes).
        """
        import urllib.parse

        http_client = self._lua.table()

        def _encode(s: str) -> str:
            quoted = urllib.parse.quote(str(s), safe="")
            return re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), quoted)

        def _capture(verb):
            def call(tbl):
                self.http_requests.append((verb.upper(), tbl))
            return call

        for verb in self._HTTP_VERBS:
            fn = _capture(verb)
            self._closure_refs.append(fn)
            http_client[verb] = fn

        def encode_string(s):
            return _encode(s)

        def decode_string(s):
            return urllib.parse.unquote(str(s))

        def encode_params(tbl):
            pairs = []
            for k, v in tbl.items():
                pairs.append(f"{_encode(k)}={_encode(v)}")
            return "&".join(pairs)

        def create_url(tbl):
            url = str(tbl["Host"])
            port = tbl["Port"]
            if port is not None:
                url += f":{port}"
            path = tbl["Path"]
            if path is not None:
                # Encode the path but keep the / separators, per doc examples.
                url += "/" + "/".join(_encode(seg) for seg in str(path).split("/"))
            query = tbl["Query"]
            if query is not None:
                url += "?" + encode_params(query)
            return url

        for name, fn in (("EncodeString", encode_string),
                         ("DecodeString", decode_string),
                         ("EncodeParams", encode_params),
                         ("CreateUrl", create_url)):
            self._closure_refs.append(fn)
            http_client[name] = fn

        # Back-compat only: not in the real API; kept so older harness code
        # touching HttpClient.New does not start nil-crashing.
        http_client.New = self._ref(lambda: self._make_http_client_instance())
        g.HttpClient = http_client

    def _make_http_client_instance(self):
        """Create a stub HttpClient instance (back-compat; not in real API)."""
        instance = self._lua.table()
        instance.Download = self._ref(lambda *args: None)
        instance.Upload = self._ref(lambda *args: None)
        instance.Request = self._ref(lambda *args: None)
        return instance

    def _make_mixer_instance(self):
        """Create a stub Mixer instance."""
        instance = self._lua.table()
        instance.SetCrossPointGain = self._ref(lambda *args: None)
        instance.SetCrossPointMute = self._ref(lambda *args: None)
        instance.SetCrossPointDelay = self._ref(lambda *args: None)
        instance.SetCrossPointSolo = self._ref(lambda *args: None)
        instance.SetInputGain = self._ref(lambda *args: None)
        instance.SetInputMute = self._ref(lambda *args: None)
        instance.SetOutputGain = self._ref(lambda *args: None)
        instance.SetOutputMute = self._ref(lambda *args: None)
        return instance

    def _make_udp_socket_instance(self):
        """Create a stub UdpSocket instance."""
        instance = self._lua.table()
        instance.Open = self._ref(lambda *args: None)
        instance.Close = self._ref(lambda *args: None)
        instance.Send = self._ref(lambda *args: None)
        instance.JoinMulticast = self._ref(lambda *args: None)
        # Callbacks (set by plugin, no-op by default)
        return instance

    def load_plugin(self, path: str | Path) -> None:
        """Load and execute a plugin file."""
        path = Path(path)
        content = path.read_text(encoding="utf-8")
        self.load_string(content, str(path))

    def load_string(self, code: str, name: str = "<string>") -> None:
        """Load and execute plugin code from a string."""
        if self._lua is None:
            self._init_lua()

        try:
            self._lua.execute(code)
        except Exception as e:
            raise RuntimeError(f"Error loading plugin: {e}") from e

        # Extract PluginInfo if defined
        g = self._lua_globals
        if g.PluginInfo:
            info = g.PluginInfo
            self.plugin_info = {
                "Name": info.Name if hasattr(info, "Name") else None,
                "Version": info.Version if hasattr(info, "Version") else None,
                "Id": info.Id if hasattr(info, "Id") else None,
            }

    # Definition functions Designer may call at design time, in the order the
    # community plugin framework documents them. The exact call set/order on
    # real hardware is NOT verified — what IS verified is that these run at
    # design time with Controls nil (the reproduced drag-in crash class).
    _DESIGN_TIME_FUNCTIONS = (
        "GetPrettyName", "GetColor", "GetPages", "GetControls",
        "GetControlLayout", "GetPins", "GetComponents", "GetModel",
    )

    def run_design_time(self, code: str, name: str = "<design-time>",
                        prop_overrides: dict | None = None) -> "DesignTimeReport":
        """Execute plugin code the way Designer does at drag-in.

        Requires design_time=True (Controls nil). Executes the chunk, then
        calls GetProperties(), RectifyProperties(props), and each definition
        function in _DESIGN_TIME_FUNCTIONS that the plugin defines, passing a
        props table built from GetProperties' declared entries (each property
        dict is passed through whole, so props["Name"].Value resolves to the
        declared default).

        Errors are COLLECTED, not raised — surfacing the design-time crash
        class is the entire point of this mode. Each error is prefixed with
        the phase that produced it ("chunk", "GetControlLayout", ...).
        """
        if not self.design_time:
            raise RuntimeError(
                "run_design_time requires QSYSRuntime(design_time=True) — a "
                "runtime with Controls injected cannot reproduce the "
                "design-time-nil crash class."
            )
        report = DesignTimeReport()
        if self._lua is None:
            self._init_lua()

        try:
            self._lua.execute(code)
        except Exception as e:
            report.errors.append(f"chunk: {e}")
            return report

        g = self._lua_globals

        # GetProperties -> build the props table Designer passes onward.
        props_table = self._lua.table()
        get_properties = g.GetProperties
        if get_properties is not None:
            try:
                declared = get_properties()
                report.properties = declared
                if declared is not None:
                    try:
                        for entry in declared.values():
                            prop_name = entry["Name"] if entry is not None else None
                            if prop_name is not None:
                                props_table[prop_name] = entry
                    except Exception as e:
                        report.errors.append(f"props-table: {e}")
            except Exception as e:
                report.errors.append(f"GetProperties: {e}")

        # Evaluate a non-default configuration (a user changing properties in
        # Designer): override the declared entries' Value before Rectify runs.
        for prop_name, value in (prop_overrides or {}).items():
            entry = props_table[prop_name]
            if entry is not None:
                entry["Value"] = value

        # Some QSC-published plugins read props["plugin_show_debug"] inside
        # RectifyProperties without ever declaring it — the best explanation
        # is that Designer injects it (INFERENCE, same class as page_index). Synthesize false unless declared.
        if props_table["plugin_show_debug"] is None:
            props_table["plugin_show_debug"] = self._lua.table(Value=False)

        rectify = g.RectifyProperties
        if rectify is not None:
            try:
                rectified = rectify(props_table)
                if rectified is not None:
                    props_table = rectified
            except Exception as e:
                report.errors.append(f"RectifyProperties: {e}")

        # Paged plugins read props["page_index"].Value in GetControlLayout —
        # a property Designer supplies, not one the plugin declares (evidence:
        # a field-deployed paged plugin relies on it and runs in Designer). Synthesize page 1 unless the plugin declared its own;
        # _run_layout_pages advances it per page.
        if g.GetPages is not None and props_table["page_index"] is None:
            props_table["page_index"] = self._lua.table(Value=1)

        n_pages = 0
        for fn_name in self._DESIGN_TIME_FUNCTIONS:
            fn = g[fn_name]
            if fn is None:
                continue
            if fn_name == "GetControlLayout":
                self._run_layout_pages(fn, props_table, n_pages, report)
                continue
            try:
                result = fn(props_table)
            except Exception as e:
                report.errors.append(f"{fn_name}: {e}")
                continue
            if fn_name == "GetControls":
                report.controls = result
            elif fn_name == "GetPages":
                # Array-like pages table: its length is the page count.
                try:
                    n_pages = len(result) if result is not None else 0
                except Exception:
                    n_pages = 0

        return report

    def _run_layout_pages(self, fn, props_table, n_pages: int,
                          report: "DesignTimeReport") -> None:
        """Call GetControlLayout once per page (single call when unpaged).

        Designer renders every page, so a layout crash or geometry that only
        exists on page 2+ was invisible while only page 1 ran. Each page gets
        a report.pages entry; page 1 also fills report.layout/graphics for
        back-compat. A page crash is recorded with a page-tagged prefix and
        does not stop the remaining pages.
        """
        paged = n_pages > 0
        for page in range(1, max(1, n_pages) + 1):
            if paged:
                props_table["page_index"] = self._lua.table(Value=page)
            layout = graphics = None
            try:
                result = fn(props_table)
                # unpack_returned_tuples: (layout, graphics) arrives as tuple.
                if isinstance(result, tuple):
                    layout = result[0] if len(result) > 0 else None
                    graphics = result[1] if len(result) > 1 else None
                else:
                    layout = result
            except Exception as e:
                tag = f"GetControlLayout (page {page})" if paged else "GetControlLayout"
                report.errors.append(f"{tag}: {e}")
            report.pages.append(
                {"index": page, "layout": layout, "graphics": graphics})
            if page == 1:
                report.layout = layout
                report.graphics = graphics

    def declare_controls(self, rows) -> None:
        """Restrict plugin-side Controls lookups to GetControls' names.

        rows: GetControls schema rows (Name, Count). Count>1 controls declare
        their array base name (served by qpdk.testing's array proxy) and the
        flat element names "name 1".."name N" the proxy resolves through.
        """
        names: set[str] = set()
        for row in rows:
            name = row.get("Name")
            if not name:
                continue
            try:
                count = int(row.get("Count") or 1)
            except (TypeError, ValueError):
                count = 1
            names.add(name)
            if count > 1:
                names.update(f"{name} {i}" for i in range(1, count + 1))
        self.controls.declare(names)

    def apply_control_schema(self, rows) -> None:
        """Stamp GetControls metadata (PinStyle/ButtonType/UserPin) onto controls.

        rows: iterable of plain dicts with at least "Name"; optional "Count",
        "PinStyle", "ButtonType", "UserPin" (qpdk.testing.arrays harvests these
        from a design-time GetControls run). Count>1 controls stamp their flat
        namespace elements ("name 1".."name N" — the proven array-proxy naming).

        Under writes_fire_both_handlers=True this ARMS every PinStyle="Both"
        control to fire its EventHandler on Lua writes (probe-proven real-Core
        behavior). Call BEFORE load_plugin so controls exist armed before the
        plugin registers handlers and starts writing.
        """
        for row in rows:
            name = row.get("Name")
            if not name:
                continue
            try:
                count = int(row.get("Count") or 1)
            except (TypeError, ValueError):
                count = 1
            names = [name] if count <= 1 else [f"{name} {i}" for i in range(1, count + 1)]
            for n in names:
                ctrl = self.controls.get_or_create(n)
                ctrl.pin_style = row.get("PinStyle")
                ctrl.button_type = row.get("ButtonType")
                ctrl.user_pin = bool(row.get("UserPin"))
                if self.writes_fire_both_handlers and ctrl.pin_style == "Both":
                    ctrl.writes_fire_handlers = True

    def tick(self, duration: float) -> int:
        """Advance virtual time and process timer events."""
        return self._scheduler.tick(duration)

    def run_until(self, time: float) -> int:
        """Run until a specific virtual time."""
        return self._scheduler.run_until(time)

    @property
    def current_time(self) -> float:
        """Get the current virtual time."""
        return self._scheduler.current_time

    def get_print_output(self) -> list[str]:
        """Get captured print output."""
        return self._print_output.copy()

    def clear_print_output(self) -> None:
        """Clear captured print output."""
        self._print_output.clear()

    def reset(self) -> None:
        """Reset the runtime state."""
        # Clear and REWIRE the async error sink. A bare `MockControls()`
        # (no error_sink) would mean post-reset EventHandler crashes were swallowed and had_errors() went
        # stale. Clear the existing list in place (so any held reference stays
        # valid) and pass it back into the fresh MockControls.
        self.errors.clear()
        self._wrapped_controls.clear()
        self._wrapped_timers.clear()
        self._wrapped_tcp_sockets.clear()
        self._wrapped_ssh_objs.clear()
        # NOTE: _closure_refs is the process-lifetime pin — never cleared
        # (clearing it re-opens the address-reuse hazard for still-live
        # Lua universes; see __init__).
        self.controls = MockControls(error_sink=self.errors)
        self.properties = MockProperties()
        self.log.clear()
        self._print_output.clear()
        reset_global_scheduler()
        self._scheduler = get_global_scheduler()
        self.timer_api = TimerAPI()
        self.ssh_api = SshAPI()
        self.tcp_api = TcpSocketAPI()
        self.plugin_info = {}
        self.http_requests = []
        self._lua = None
        self._lua_globals = None
