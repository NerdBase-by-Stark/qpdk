"""PluginHarness — one object that knows how to build correct runtimes.

Construction order merges two field-used per-plugin conftest
implementations and is regression-locked by tests/test_testing_harness.py:
  1. QSYSRuntime() + _init_lua()
  2. seed properties (defaults, then per-call overrides)
  3. seed pre-load controls (bool -> .Boolean, str -> .String, else .Value)
  4. inject array proxies (BEFORE load — see arrays.inject_array_proxies)
  5. load_plugin
"""

from pathlib import Path
from typing import Any

from qpdk.mock_runtime.runtime import QSYSRuntime

from .arrays import derive_with_runtime, inject_array_proxies


class PluginHarness:
    def __init__(
        self,
        plugin_path: str | Path,
        property_defaults: dict[str, Any] | None = None,
        control_seeds: dict[str, Any] | None = None,
        readline: bool = False,
        writes_fire_both_handlers: bool = False,
        strict_controls: bool = True,
    ):
        self.plugin_path = Path(plugin_path)
        self.property_defaults = dict(property_defaults or {})
        self.control_seeds = dict(control_seeds or {})
        self.readline = readline
        # Both-handler mode: arm PinStyle="Both" controls (from the plugin's
        # own GetControls, via the derivation run) to fire EventHandlers on
        # Lua writes — probe-proven real-Core behavior. Echo-loop defects
        # (missing write guards) become testable. Default OFF.
        self.writes_fire_both_handlers = writes_fire_both_handlers
        # Plugin lookups of controls GetControls never declared return nil,
        # as on a Core. Default ON: a typo'd control name must fail here, not
        # at load on hardware. Opt out only for legacy suites being migrated.
        self.strict_controls = strict_controls
        self._source: str | None = None
        # Derivations keyed by the effective property configuration. Each
        # derivation runtime is PINNED for the harness lifetime (see arrays).
        self._derived: dict[tuple, tuple[dict[str, int], list[dict]]] = {}
        self._derivation_rts: list = []

    @property
    def source(self) -> str:
        if self._source is None:
            self._source = self.plugin_path.read_text(encoding="utf-8")
        return self._source

    def _derive(self, props: dict[str, Any]) -> tuple[dict[str, int], list[dict]]:
        """(arrays, schema) from GetControls evaluated at these properties.

        Cached per configuration, so a session derives each configuration once.
        """
        key = tuple(sorted((k, repr(v)) for k, v in props.items()))
        if key not in self._derived:
            arrays, rt, schema = derive_with_runtime(self.source, props or None)
            self._derivation_rts.append(rt)
            self._derived[key] = (arrays, schema)
        return self._derived[key]

    @property
    def arrays(self) -> dict[str, int]:
        """Array-control map at the harness property defaults.

        Derived from the plugin's own GetControls, so it cannot drift from the
        source. The derivation runtime is PINNED on the harness (usually
        session-scoped): discarding it while test runtimes keep being built
        is the dead-runtime cyclic-GC scenario behind the lupa address-reuse
        hazard.
        """
        return self._derive(self.property_defaults)[0]

    @property
    def schema(self) -> list[dict]:
        """GetControls schema rows (Name/Count/PinStyle/ButtonType/UserPin)
        at the harness property defaults."""
        return self._derive(self.property_defaults)[1]

    def make_runtime(self, control_seeds: dict[str, Any] | None = None,
                     **property_overrides: Any) -> QSYSRuntime:
        """Build a fresh runtime with the plugin loaded and ready to test."""
        if self.readline:
            raise NotImplementedError(
                "PluginHarness(readline=True) is not implemented in this release."
            )
        # Derive arrays BEFORE building the runtime: the first derivation
        # constructs a design-time QSYSRuntime whose __init__ resets the
        # GLOBAL timer scheduler — doing that mid-construction would strand
        # the runtime under construction on a dead scheduler (its plugin's
        # timers would register on the new one and never fire via tick()).
        # The control set is derived for the EFFECTIVE configuration (harness
        # defaults plus this test's overrides), so a test at 16 channels gets
        # 16 channel controls, not the plugin's declared default count.
        props = dict(self.property_defaults)
        props.update(property_overrides)
        arrays, schema = self._derive(props)
        rt = QSYSRuntime(
            writes_fire_both_handlers=self.writes_fire_both_handlers)
        rt._init_lua()
        if self.writes_fire_both_handlers:
            # Arm Both-pin controls BEFORE load: the plugin registers handlers
            # and starts writing at load time. Schema stamping is gated on the
            # mode so default-OFF harnesses keep their exact control population
            # (stamping pre-creates every declared control).
            rt.apply_control_schema(schema)

        for key, value in props.items():
            rt.properties.set(key, value)

        seeds = self.control_seeds if control_seeds is None else control_seeds
        for name, value in seeds.items():
            if isinstance(value, bool):
                rt.controls[name].Boolean = value
            elif isinstance(value, str):
                rt.controls[name].String = value
            else:
                rt.controls[name].Value = value

        if self.strict_controls:
            rt.declare_controls(schema)
        inject_array_proxies(rt, arrays)
        rt.load_plugin(self.plugin_path)
        return rt
