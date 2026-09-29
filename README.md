# QPDK: Q-SYS Plugin Development Kit

Check and test Q-SYS plugins without Q-SYS Designer, a Core or hardware.

QPDK reads a `.qplug` file and tells you, before it goes anywhere near a real
system, whether it will load, whether it is wired correctly, and whether it
behaves when it runs. It was built to keep AI-written plugins honest, and it
works just as well on plugins written by hand.

## What it checks, and what it doesn't

QPDK makes sure what you deploy is **functional**: it loads in Designer, its
controls and layout match, it doesn't break known platform rules, and its Lua
runs in a simulated Q-SYS.

It will **not** tell you that your logic is right, or that your SSH or TCP
handling won't upset a device. A plugin can pass every QPDK check and still do
the wrong thing in a real room. Treat a clean QPDK run as the floor, not the
finish line: test on real hardware before you ship.

## What's inside

- **Validator** (`qpdk validate`): 42 rules covering Lua syntax, plugin
  structure, control and layout coupling, design-time crash patterns, timer and
  socket lifecycle, logging API use, layout geometry and more. Optional
  luacheck integration with a Q-SYS globals list.
- **Designer drag-in check** (`qpdk design-check`): runs the plugin's
  definition functions the way Designer does at drag-in, where `Controls` does
  not exist yet, and reports anything that would crash.
- **QSC's official evaluator** (`qpdk qsc-eval`): runs QSC's own
  Plugin-Evaluation-Script as an extra check. See below.
- **Mock runtime** (`qpdk.mock_runtime`): a Lua 5.3 sandbox with Q-SYS APIs
  (Controls, Timer, TcpSocket, Ssh, json and more) so you can drive a plugin
  from Python tests and advance virtual time.
- **Test harness** (`qpdk test`, `qpdk.testing`): pytest support that builds
  the control set from the plugin itself, so tests don't rot when controls
  change.
- **QRC mock server** (`qpdk qrc`): a stand-in Core that speaks QRC, for
  testing external control clients.
- **GitHub Action**: gate every push to a plugin repo.

## Install

Requires Python 3.10 or newer.

```bash
pip install "git+https://github.com/NerdBase-by-Stark/qpdk.git"

# optional extras
pip install "qpdk[qrc] @ git+https://github.com/NerdBase-by-Stark/qpdk.git"   # QRC mock server
```

For the `LUACHECK-*` rules, install [luacheck](https://github.com/lunarmodules/luacheck)
(`luarocks install luacheck`). Validation still runs without it, but is weaker.

## Quick start

```bash
qpdk validate MyPlugin.qplug              # static rules
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

## Writing tests against the mock runtime

```python
from qpdk.mock_runtime import QSYSRuntime

runtime = QSYSRuntime()
runtime.properties.set("IP Address", "192.168.1.100")
runtime.controls.add_input("Connect", value=0)
runtime.controls.add_output("Status", string="Disconnected")

runtime.load_plugin("MyPlugin.qplug")

runtime.controls.Inputs["Connect"].Boolean = True
runtime.tick(1.0)   # advance virtual time so Timer callbacks fire

assert runtime.controls.Outputs["Status"].String == "Connected"
```

For whole test suites, `qpdk.testing.PluginHarness` derives the controls
(including arrays) from the plugin's own `GetControls`, and fails a test when
the plugin raised an error inside a callback.

To reproduce Designer's drag-in in a test:

```python
from qpdk.mock_runtime.runtime import QSYSRuntime

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
pytest harness.

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

```bash
qpdk qsc-eval MyPlugin.qplug                 # default configuration
qpdk qsc-eval MyPlugin.qplug --maxprops      # every property at its maximum
qpdk qsc-eval MyPlugin.qplug -p "Inputs=16"  # a specific configuration
```

Exit codes: `0` clean, `1` QSC errors (or warnings with `--strict`), `2` the
plugin crashed at design time.

## QRC mock server

```bash
qpdk qrc                                  # default port 1710
qpdk qrc --port 1711                      # another port
qpdk qrc --record session.json            # record responses to a file
qpdk qrc --playback session.json          # replay recorded responses
```

Supports Logon, NoOp, StatusGet/EngineStatus, Control.Get/Set,
Component.Get/Set, ChangeGroup.* and Snapshot.Load. It is a simple stand-in,
not a full Core: a client that works against it can still fail against real
hardware.

## Limitations

- No visual preview of the plugin UI.
- HttpClient, Component and NamedControl are stubs; Ssh, TcpSocket, Timer and
  json have behavioural mocks.
- The mock does not enforce Core CPU limits.
- Design-time mode removes only `Controls`, the difference confirmed on
  hardware. Other globals at design time are not verified.
- Everything in "What it checks, and what it doesn't" above.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check qpdk/
```

## Licence

Apache License 2.0, see `LICENSE`. QSC's Plugin-Evaluation-Script, included
under `qpdk/qsc_eval/vendor/`, keeps its own MIT licence.

QPDK is an independent project. It is not made, endorsed or supported by QSC.
