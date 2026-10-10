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

## Rulesets

`--ruleset` picks which rules run. The default is `default`: errors and
warnings only, with a count of the INFO advisories it hid. Use `full` to
list those advisories too.

| Ruleset | What runs | Typical use |
|---------|-----------|-------------|
| `P0` | Crash-preventing rules only | A minimal CI gate |
| `default` | Every rule that reports errors or warnings | Pre-merge review |
| `full` | Everything, including INFO advisories | Development |

```bash
qpdk validate --ruleset P0 --strict MyPlugin.qplug
qpdk validate MyPlugin.qplug                      # default
qpdk validate --ruleset full MyPlugin.qplug
```

The layout geometry rules QPDK040 to QPDK046 need the evaluated layout, so
`qpdk design-check` reports them, not `qpdk validate`.

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
