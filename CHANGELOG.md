# Changelog

QPDK is alpha software (0.x). Rules, severities and mock behaviour may change
between minor versions.

## 0.16.0

A polish pass before the first wider release. Some changes below alter
behaviour that tests may rely on; they are marked **Changed**.

### Licence

- QPDK is now dual licensed under the MIT License or the Apache License 2.0,
  at your option (was Apache 2.0 only).

### Validator rules

- **Changed:** without `--ruleset`, `qpdk validate` and `qpdk-validate`
  still run every rule but list only errors and warnings, with a one-line
  count of the INFO advisories not listed. `--ruleset full` lists them, as
  the old default did. `--quiet` now hides INFO rows too.
- **Changed:** under `--strict`, a plugin with warnings but no errors shows
  a red FAILED banner instead of a green VALID one, matching its exit code 2.
- `qpdk rules` explains the Phase column and no longer drops text such as
  `[i]` from descriptions. Several rule descriptions and finding messages
  are reworded to drop internal jargon.
- luacheck `W113` (reading an undefined global) is now INFO when the plugin
  assigns that name somewhere, and stays a WARNING otherwise.
- QPDK024 (reserved name shadowing) matches names by exact case.
- QPDK011 (Trigger button with an input pin) is now INFO.
- QPDK025 (missing runtime guard) recognises more guard idioms.
- A rule that crashes inside the validator is reported as an error instead
  of being skipped.
- `Status` and `StatusState` are no longer treated as Q-SYS globals, in the
  validator or in the mock runtime. A plugin reports status through its own
  declared `Status` control.
- QPDK006 and QPDK007, reserved placeholders that never fired, no longer
  count as live rules.

### Mock runtime and QRC

- Plugin code can no longer reach the host Python through lupa's `python`
  global, and `os.execute`, `os.exit`, `io.popen` and `package.loadlib` are
  removed, in the mock runtime and in `qsc-eval`. QPDK is still not a
  security sandbox: only check plugins you trust (see README).
- Plugin `print()` and `Log.Message()` output goes to stderr, so it no longer
  corrupts `--format json` or TAP output on stdout.
- EventHandlers written in Lua now receive the Lua timer or control object,
  so identity checks (`if timer == timer1`) and `ctl.Index` on control arrays
  work as in the official examples.
- `require("json")` works, and `json.null` exists.
- rapidjson gains `null`, `array()`, `object()` and the `pretty` and
  `sort_keys` options, and `decode` returns nil plus a message on bad JSON.
- `TcpSocket:Search`, `Component.GetComponents` and `Component.GetControls`
  are available.
- Writes to `Choices`, `IsIndeterminate`, `CssClass` and
  `Ssh.IsInteractive` now persist.
- **Changed:** Lua `NamedControl.Get`/`Set` are replaced by the documented
  `GetValue`/`SetValue`, plus `Trigger`.
- **Changed:** the `Design.Name`/`Design.Platform` fields are replaced by the
  documented `Design.GetStatus()` (with `DesignName`) and `Design.GetInventory()`.
- **Changed:** the Python `QSYSRuntime.status` attribute is removed, along
  with the invented `Status`/`StatusState` Lua globals.
- QRC: `ChangeGroup.Poll` reports component controls as `Component` plus
  `Name`, as the QRC documentation shows. `StatusGet` includes the `Status`
  block, `Component.Set` honours `ResponseValues`, and `Snapshot.Save` is
  handled.
- **Changed:** QRC `Control.Get` on an unknown control name returns an error
  instead of inventing a default value.
- QRC: a request with params of the wrong shape gets error -32602 instead of
  a dropped connection.
- `qpdk qsc-eval` says plainly when QSC's evaluator needs a function the
  plugin does not define, instead of printing a crash with an install path.

### Packaging, docs and examples

- The GitHub Action handles plugin paths that contain spaces, and validates
  all matched plugins in one run so duplicate plugin Ids are caught.
- The `structure.controls` count in `validate` JSON no longer counts
  `table.insert(ctls, { Name = ... })` twice.
- New AGENTS.md: a one-page guide for coding agents adding QPDK to CI.
- pytest is now installed with QPDK, so `qpdk test` and the test harness
  work straight after install.
- The good example (`examples/good/simple-button.qplug`) now passes
  `validate`, `design-check` and `qsc-eval`, including with `--strict` and
  `--ruleset full`, and runs in the mock runtime. CI runs `qsc-eval` on it.
- README: install note (QPDK is not on PyPI), a "Writing a test suite"
  section, a working mock runtime example that fires EventHandlers, and a
  note that `qsc-eval` is stricter than Designer.
- New `docs/rules.md`: rulesets, luacheck severity mapping and the
  `-- qpdk:ignore` suppression syntax.
- README: an exit code table for every command, a note that the Action
  fails on errors only, and the standalone `qpdk-validate` and `qpdk-qrc`
  commands. `qpdk-validate` and `qpdk-qrc` help keeps example line breaks,
  and `qrc` help shows the default host and port.
- `validate`, `design-check` and `qsc-eval` give a usage error for a
  directory argument instead of a traceback.
- Packaging: SPDX licence metadata, the unused `qrc` extra removed, the sdist
  now ships the test fixtures and examples, and the unused `.luacheckrc-qsys`
  is gone.
- Comments that pointed at private notes were replaced with the facts they
  stood for.

## 0.15.0

First public release.

- `qpdk validate`: static rules for Lua syntax, plugin structure, control and
  layout coupling, design-time crash patterns, timer and socket lifecycle,
  and more, with optional luacheck integration.
- `qpdk design-check`: runs the plugin's definition functions the way
  Designer does at drag-in.
- `qpdk qsc-eval`: runs QSC's official Plugin-Evaluation-Script.
- `qpdk.mock_runtime` and `qpdk.testing`: a Lua 5.3 mock of the Q-SYS APIs
  and a pytest harness that builds the control set from the plugin.
- `qpdk qrc`: a mock QRC server.
- A GitHub Action that runs the checks on every push.
- Fixes for cases where 0.14 passed a plugin it should have failed:
  references to undeclared controls (QPDK049), single controls used as
  arrays (QPDK050), the `Timer` API surface, suppression comments inside
  string literals, callback errors hidden from tests, and luacheck failures
  read as a clean pass.
