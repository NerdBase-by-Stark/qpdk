# Validator rules

`qpdk rules` prints the rule catalogue: every rule code with its severity,
the rulesets it belongs to, its category and a description. Use
`qpdk rules --format json` for the full, untruncated text. The catalogue is
generated from the validator's own metadata, so it is always current; this
page covers what the catalogue does not.

## Severities and exit codes

Each finding is an `ERROR`, a `WARNING` or an `INFO`.

| Exit code | Meaning |
|-----------|---------|
| `0` | No errors (and, with `--strict`, no warnings) |
| `1` | At least one error |
| `2` | Warnings found under `--strict`, or a usage error such as a bad argument |

`--format json` and `--format tap` give machine-readable output.
`--qsys-version 7` makes QPDK009 flag APIs added in Q-SYS 9 or later
(the default is 9).

## Rulesets

`--ruleset` picks which rules run and what is listed. Every rule has a
phase, shown by `qpdk rules`: 1 = crash-preventing, 2 = quality,
3 = advisory.

| Flag | Rules that run | Listed | Typical use |
|------|----------------|--------|-------------|
| (none) | every rule | errors and warnings, plus a count of INFO notes not shown | CI gate, day to day |
| `--ruleset P0` | phase 1 only | everything those rules find, INFO included | a minimal gate |
| `--ruleset default` | phases 1 and 2 | everything those rules find, INFO included | a gate without the advisory rules |
| `--ruleset full` | every rule | everything, INFO included | development |

`--ruleset default` is not the same as leaving the flag off: it skips the
phase 3 rules (including some warnings, such as QPDK029) and it lists INFO
rows. luacheck findings appear under every ruleset. `qpdk rules` shows which
rulesets each rule belongs to.

```bash
qpdk validate --ruleset P0 --strict MyPlugin.qplug
qpdk validate MyPlugin.qplug                      # every rule, INFO counted
qpdk validate --ruleset full MyPlugin.qplug
```

The layout geometry rules QPDK040 to QPDK046 need the evaluated layout, so
`qpdk design-check` reports them, not `qpdk validate`. They are advisory:
`design-check` prints them, but its exit code only reflects design-time
crashes.

## luacheck findings

When [luacheck](https://github.com/lunarmodules/luacheck) is installed,
`qpdk validate` runs it with a Q-SYS globals list and reports its findings as
`LUACHECK-<code>`:

| luacheck code | QPDK severity |
|---------------|---------------|
| `E*` (syntax and other errors) | ERROR |
| `W111`, `W112` (setting or mutating a non-standard global) | INFO |
| `W113` (reading an undefined global), when the plugin assigns that name somewhere | INFO |
| Every other `W*`, including `W113` for a name the plugin never assigns | WARNING |

`QPDK000` reports a problem with the analysis itself: luacheck not installed
(INFO, validation continues without it), or luacheck failing or timing out
(ERROR, because the analysis is incomplete).

To declare extra globals your plugin relies on, put a `.luacheckrc` next to
the plugin with a `globals = { "Name", "OtherName" }` table of double-quoted
names. QPDK adds those names to its own list and ignores the rest of the
file.

## Silencing a finding

Add a `-- qpdk:ignore` comment **on the same line** as the finding:

```lua
sock:Write("xCommand " .. cmd)  -- qpdk:ignore QPDK029

-- several codes, separated by commas or spaces
local status = 1  -- qpdk:ignore QPDK023, QPDK024

-- text after the codes is ignored, so you can give a reason
local status = 1  -- qpdk:ignore QPDK024 shadowing is deliberate here

-- luacheck findings use their LUACHECK- code
print(helperFromAnotherFile)  -- qpdk:ignore LUACHECK-W113
```

How it behaves:

1. Only the line carrying the comment is affected. A comment on the line
   above does nothing.
2. The directive must be in a real Lua comment. Text inside a string literal
   is ignored.
3. A suppressed finding is removed from the results entirely: it does not
   appear in text, JSON or TAP output and does not count towards the exit
   code.
4. Syntax errors (`QPDK001` and luacheck `E` codes) cannot be suppressed.
5. Findings about the whole file rather than one line (they show no line
   number) cannot be suppressed this way.
