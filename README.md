# QPDK: Q-SYS Plugin Development Kit

Check and test Q-SYS plugins without Q-SYS Designer, a Core or hardware.

QPDK reads a `.qplug` file and tells you, before it goes anywhere near a real
system, whether it will load, whether it is wired correctly, and whether it
behaves when it runs. It was built to keep AI-written plugins honest, and it
works just as well on plugins written by hand.

QPDK is alpha software (0.x): rules, severities and mock behaviour may change
between minor versions.

Coding agents: read [AGENTS.md](AGENTS.md) first.

## What it checks, and what it doesn't

QPDK makes sure what you deploy is **functional**: it loads in Designer, its
controls and layout match, it doesn't break known platform rules, and its Lua
runs in a simulated Q-SYS.

It will **not** tell you that your logic is right, or that your SSH or TCP
handling won't upset a device. A plugin can pass every QPDK check and still do
the wrong thing in a real room. Treat a clean QPDK run as the floor, not the
finish line: test on real hardware before you ship.

## What's inside

- **Validator** (`qpdk validate`): rules covering Lua syntax, plugin
  structure, control and layout coupling, design-time crash patterns, timer and
  socket lifecycle, logging API use and more. Optional
  luacheck integration with a Q-SYS globals list. `qpdk rules` lists every
  rule with its severity and rulesets; see [docs/rules.md](docs/rules.md) for
  rulesets, luacheck codes and how to silence a finding.
- **Designer drag-in check** (`qpdk design-check`): runs the plugin's
  definition functions the way Designer does at drag-in, where `Controls` does
  not exist yet, and reports anything that would crash, plus layout geometry
  findings (overlaps, zero sizes, off-page controls).
- **QSC's official evaluator** (`qpdk qsc-eval`): runs QSC's own
  Plugin-Evaluation-Script as an extra check. See below.
- **Mock runtime** (`qpdk.mock_runtime`): a Lua 5.3 environment with mock Q-SYS APIs
  (Controls, Timer, TcpSocket, Ssh, json and more) so you can drive a plugin
  from Python tests and advance virtual time.
- **Test harness** (`qpdk test`, `qpdk.testing`): pytest support that builds
  the control set from the plugin itself, so tests don't rot when controls
  change.
- **QRC mock server** (`qpdk qrc`): a stand-in Core that speaks QRC, for
  testing external control clients.
- **Standalone commands**: `qpdk-validate` and `qpdk-qrc` are installed too.
  They do the same as `qpdk validate` and `qpdk qrc`; `qpdk-qrc` also takes
  `--add-control`, `--add-component` and `--user`.
- **GitHub Action**: gate every push to a plugin repo.

### Only check plugins you trust

`validate`, `design-check`, `qsc-eval` and `test` run the plugin's Lua code on
your machine, with your user's permissions. QPDK removes the Lua calls that
start programs or load native code, but it is not a security sandbox: plugin
code can still read and write files. Treat a `.qplug` from an unknown source
like any other program: if you do not trust it, run QPDK in a container or
a throwaway VM.

## Install

Requires Python 3.10 or newer.

QPDK is not on PyPI. `pip install qpdk` installs an unrelated package, so
install from git (or use the GitHub Action below):

```bash
pip install "git+https://github.com/NerdBase-by-Stark/qpdk.git"
```

This also installs pytest, which `qpdk test` and the test harness use.

For the `LUACHECK-*` rules, install [luacheck](https://github.com/lunarmodules/luacheck)
(`luarocks install luacheck`). Validation still runs without it, but is weaker.

## Quick start

```bash
qpdk validate MyPlugin.qplug              # rules: errors and warnings
qpdk validate MyPlugin.qplug -r full      # also list INFO advisories
qpdk validate MyPlugin.qplug --strict     # warnings count as failures
qpdk validate MyPlugin.qplug --format json

qpdk design-check MyPlugin.qplug          # would Designer crash at drag-in?
qpdk qsc-eval MyPlugin.qplug              # QSC's official design-time checks
qpdk test MyPlugin.qplug --test-dir test  # run the plugin's pytest suite

qpdk rules                                # list every rule
qpdk --help
```

`qpdk test` fails when no test directory is found: a plugin with no tests is
not a passing plugin.

Leaving `--ruleset` off runs every rule. `--ruleset default` is not the same
thing: it runs fewer rules and lists INFO rows. See
[docs/rules.md](docs/rules.md#rulesets).

## Exit codes

| Command | 0 | 1 | 2 |
|---|---|---|---|
| `validate` | no errors (and no warnings under `--strict`) | at least one error | warnings under `--strict`, or a usage error |
| `design-check` | no design-time crash | a plugin would crash Designer | usage error |
| `qsc-eval` | no QSC errors (and no warnings under `--strict`) | QSC errors, or warnings under `--strict` | the plugin crashed at design time, or a usage error |
| `test` | pytest passed | pytest's own code (1 = test failures) | no test directory, or a usage error |

Layout findings from `design-check` (QPDK040 to QPDK046) are printed but do
not change its exit code.

## Writing a test suite

`qpdk test` and the Action's `test-dir` run an ordinary pytest suite. QPDK
installs a pytest plugin that gives each test a fresh mock runtime with your
plugin loaded. You tell it which plugin to load by overriding the
`qsys_harness` fixture in your `conftest.py`:

```python
# test/conftest.py
from pathlib import Path

import pytest
from qpdk.testing import PluginHarness

PLUGIN = Path(__file__).parent.parent / "release" / "MyPlugin-v1.0.0.qplug"


@pytest.fixture(scope="session")
def qsys_harness():
    return PluginHarness(PLUGIN, property_defaults={"Initial State": False})
```

`PluginHarness` builds the control set (including arrays) from the plugin's
own `GetControls`, so tests don't rot when controls change, and the plugin's
lookup of a control it never declared returns nil. That applies to the
plugin's Lua. Indexing `rt.controls[...]` from Python still creates the
control on demand, so a typo in a test does not fail by itself; assert on a
value the plugin wrote.
`property_defaults` sets the property values every test starts from; any
property you don't list is missing in the mock, so list the ones your
runtime code reads.

Tests then ask for the `rt` fixture:

```python
# test/test_plugin.py
import pytest


def test_toggle_drives_state(rt):
    rt.tick(1.0)                                   # run Timer callbacks
    rt.controls["Toggle"].simulate_external_write(1)
    assert rt.controls["State"].Boolean is True


@pytest.mark.qsys_props(**{"Initial State": True})
def test_initial_state_property(rt):
    rt.tick(1.0)
    assert rt.controls["State"].Boolean is True
```

- `@pytest.mark.qsys_props(...)` overrides properties for one test.
- If the plugin raises an error inside a callback (timer, socket,
  EventHandler) during a test, the test fails at teardown even when its own
  assertions pass. Mark a test `@pytest.mark.qsys_expect_errors` when the
  crash is what you are testing.
- If a test asks for `rt` without the conftest override, it fails with a
  message telling you to add one.

Run it with `qpdk test release/MyPlugin-v1.0.0.qplug --test-dir test`, or
plain `pytest test`.

## Using the mock runtime directly

```python
from qpdk.mock_runtime import QSYSRuntime

runtime = QSYSRuntime()
runtime.properties.set("Initial State", False)
runtime.load_plugin("examples/good/simple-button.qplug")
runtime.tick(1.0)   # advance virtual time so Timer callbacks fire

runtime.controls["Toggle"].simulate_external_write(1)
assert runtime.controls["State"].Boolean is True
assert not runtime.had_errors()
```

Writing a control from Python (`runtime.controls["Toggle"].Value = 1`) does
not fire the plugin's EventHandler, and by default neither does a write from
the plugin's own Lua. Use `simulate_external_write(value)` or
`simulate_trigger()` to stand in for a user or a wired pin. One exception has
been observed on a Core: a Lua write to a `PinStyle = "Both"` control can fire
that control's own EventHandler. To test for echo loops this could cause, pass
`writes_fire_both_handlers=True` to `PluginHarness` or `QSYSRuntime`. A bare `QSYSRuntime()` also creates any control
name on demand; `PluginHarness` above is stricter and is the better default.

To reproduce Designer's drag-in in a test:

```python
from pathlib import Path

from qpdk.mock_runtime import QSYSRuntime

plugin_source = Path("MyPlugin.qplug").read_text(encoding="utf-8")
runtime = QSYSRuntime(design_time=True)   # Controls stays nil, as in Designer
report = runtime.run_design_time(plugin_source)
assert report.errors == []
```

## GitHub Action

```yaml
jobs:
  plugin-ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - uses: NerdBase-by-Stark/qpdk@v0
        with:
          plugin: release/MyPlugin-v1.0.0.qplug
          test-dir: test        # optional
```

The action runs `validate`, `design-check` and, if you give a `test-dir`, the
pytest harness. It fails the build on errors only. Add your own step with
`qpdk validate --strict MyPlugin.qplug` if warnings should fail it too.

It installs luacheck with apt (which needs sudo on the runner) unless you set
`install-luacheck: 'false'`.

## QSC's evaluator

QSC publishes an official
[Plugin-Evaluation-Script](https://github.com/Q-SYS-Communities-for-Developers/Plugin-Evaluation-Script).
QPDK includes it unmodified (MIT licence, see `qpdk/qsc_eval/vendor/`) and runs
it in the same Lua 5.3 runtime as the mock, with the upstream shell checks
rewritten in Python so it needs no jq or Windows Lua binary.

It checks the plugin's design-time output against the official SDK vocabulary,
reserved names and PluginInfo conventions. QPDK's own rules cover different
ground: runtime behaviour, lifecycle patterns and layout geometry. Running both
gives the widest net.

It is stricter than Designer: plugins that load and run fine in the field can
fail it, mostly on layout fields such as a missing `Style`.

```bash
qpdk qsc-eval MyPlugin.qplug                 # default configuration
qpdk qsc-eval MyPlugin.qplug --maxprops      # every property at its maximum
qpdk qsc-eval MyPlugin.qplug -p "Inputs=16"  # a specific configuration
```

Exit codes: `0` clean, `1` QSC errors (or warnings with `--strict`), `2` the
plugin crashed at design time or a usage error.

## QRC mock server

```bash
qpdk qrc                                  # listens on 127.0.0.1:1710
qpdk qrc --port 1711                      # another port
qpdk qrc --record session.json            # record responses to a file
qpdk qrc --playback session.json          # replay recorded responses
```

Supports Logon, NoOp, StatusGet/EngineStatus, Control.Get/Set,
Component.Get/Set/GetComponents/GetControls, ChangeGroup.*, Snapshot.Load and
Snapshot.Save. It is a simple stand-in, not a full Core: a client that works
against it can still fail against real hardware.

## Limitations

- No visual preview of the plugin UI.
- Component, NamedControl and HttpClient are stubs. Component has `New`,
  `GetComponents` and `GetControls`, and the last two return empty tables.
  NamedControl has `GetValue`, `SetValue`, `GetString`, `SetString`,
  `GetPosition`, `SetPosition` and `Trigger`, and creates an unknown name
  instead of raising an error as a Core does. Ssh, TcpSocket, Timer and json
  have behavioural mocks.
- `Timer.Now()` returns virtual seconds starting at 0, not seconds since the
  epoch, so do not compare it with `os.time()` or pass it to `os.date()`.
- `Timer:Start(0)` schedules nothing, and `IsRunning()` stays false.
- `Control:Trigger()` called from Lua does nothing in the mock. Use
  `simulate_trigger()` to simulate a press.
- TcpSocket has no single `EventHandler` and no `TcpSocket.Events`, and
  `ReconnectTimeout` defaults to 0 instead of the documented 5 seconds.
- The QRC mock does not require Logon, and it closes an idle connection after
  65 seconds.
- Data passed between Lua and Python is text. A socket write or read with
  bytes that are not valid UTF-8 (many binary device protocols) raises an
  error in the mock, so binary protocols cannot be tested yet.
- The mock does not enforce Core CPU limits.
- Design-time mode removes only `Controls`, the difference confirmed on
  hardware. Other globals at design time are not verified.
- Everything in "What it checks, and what it doesn't" above.

## Development

Use a fresh virtual environment. Do not reuse one that has QPDK installed from
git: leftover files from that install can hide the editable copy.

```bash
pip install -e ".[dev]"
pytest
ruff check qpdk tests
```

Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Licence

Licensed under either of the [MIT License](LICENSE-MIT) or the
[Apache License 2.0](LICENSE-APACHE), at your option. QSC's
Plugin-Evaluation-Script, included under `qpdk/qsc_eval/vendor/`, keeps its own
MIT licence.

Unless you say otherwise, any contribution you submit for inclusion in QPDK is
dual licensed as above, without any additional terms or conditions.

QPDK is an independent project. It is not made, endorsed or supported by QSC.
