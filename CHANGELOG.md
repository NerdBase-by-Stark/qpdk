# Changelog

QPDK is alpha software (0.x). Rules, severities and mock behaviour may change
between minor versions.

## Unreleased

A polish pass before the first wider release.

### Validator rules

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

- Fixes to the mock runtime API so plugins written from the official
  examples behave as they would on a Core.
- Fixes to the QRC mock server's wire format.

### Packaging, docs and examples

- The good example (`examples/good/simple-button.qplug`) now passes
  `validate`, `design-check` and `qsc-eval`, and runs in the mock runtime.
  CI runs `qsc-eval` on it.
- README: install note (QPDK is not on PyPI), a "Writing a test suite"
  section, a working mock runtime example that fires EventHandlers, and a
  note that `qsc-eval` is stricter than Designer.
- `docs/rules.md` replaces the old migration plan: rulesets, luacheck
  severity mapping and the `-- qpdk:ignore` suppression syntax.
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
