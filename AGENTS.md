# QPDK for coding agents

QPDK checks Q-SYS `.qplug` plugins without Q-SYS Designer. README.md has the
full picture; this page is the short version for adding QPDK to a plugin
repository's CI and writing its first test.

## Install

    pip install "git+https://github.com/NerdBase-by-Stark/qpdk.git"

QPDK is not on PyPI: `pip install qpdk` installs an unrelated package. The
install includes pytest, which `qpdk test` needs. Install
[luacheck](https://github.com/lunarmodules/luacheck) too if you can; without
it the `LUACHECK-*` rules do not run.

## Commands and exit codes

    qpdk validate PLUGIN.qplug             # every rule; lists errors and warnings, counts INFO
    qpdk validate PLUGIN.qplug --strict    # warnings fail too
    qpdk validate PLUGIN.qplug --format json
    qpdk design-check PLUGIN.qplug         # would Designer crash at drag-in?
    qpdk qsc-eval PLUGIN.qplug             # QSC's own evaluator
    qpdk test PLUGIN.qplug --test-dir test # runs pytest in that directory
    qpdk rules --format json               # the rule catalogue

| Command | 0 | 1 | 2 |
|---|---|---|---|
| validate | no errors | errors | warnings under `--strict`, or usage error |
| design-check | no crash | would crash Designer | usage error |
| qsc-eval | no QSC errors | QSC errors, or warnings under `--strict` | plugin crashed at design time, or usage error |
| test | pytest passed | pytest failures | no test directory, or usage error |

`--ruleset` is optional. Leaving it off runs every rule and hides INFO notes.
`--ruleset default` is not the same thing: it runs fewer rules and lists INFO.
Layout findings QPDK040 to QPDK046 come from `design-check` and never change
its exit code.

## JSON shapes

- `validate --format json` (one file): `valid`,
  `summary.{errors,warnings,info,info_hidden}`,
  `issues[].{code,severity,message,line,column,source}`. With several files:
  `{total, valid, invalid, results[]}`.
- `design-check --format json`: `clean`,
  `results[].{plugin,errors[],ui_findings[].{code,severity,name,page,message}}`.
- `qsc-eval --format json`: `ok`,
  `results[].{plugin,ok,crash,errors[],warnings[],notices[]}`.

## CI

Use the GitHub Action (README, "GitHub Action"). It runs `validate` and
`design-check` on the plugin and, when `test-dir` is set, pytest. It fails on
errors only. Add a step with `qpdk validate --strict PLUGIN.qplug` if warnings
should fail the build.

## First test

Create `test/conftest.py` overriding the `qsys_harness` fixture with a
`PluginHarness(path, property_defaults={...})`, then write tests that take the
`rt` fixture. The exact files are in README.md, "Writing a test suite"; copy
them as written. Rules that matter:

- `rt.tick(seconds)` advances virtual time; Timer callbacks do not run until
  you call it.
- Use `simulate_external_write(value)` or `simulate_trigger()` to act as a
  user. Setting `.Value` from Python does not fire the plugin's EventHandler.
- A plugin error inside any callback fails the test at teardown unless the
  test is marked `@pytest.mark.qsys_expect_errors`.
- `@pytest.mark.qsys_props(**{"Prop Name": value})` overrides a property for
  one test.
- The plugin's Lua gets nil for a control it never declared. Python-side
  `rt.controls[...]` creates controls on demand, so assert on values the
  plugin wrote.

Run with `qpdk test PLUGIN.qplug --test-dir test` or `pytest test`.
