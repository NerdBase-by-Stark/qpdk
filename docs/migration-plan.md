# QPDK Validator Migration Plan

> Phased rollout strategy for introducing 24 new validator rules without
> breaking existing plugin workflows.

## Problem

Adding 24 new validator rules simultaneously will cause every existing plugin
to fail validation. This creates developer frustration and tool rejection.
Rules must be phased in gradually.

## Phased Rollout

### Phase 1: Crash-Preventing Rules (Immediate)

**Severity: ERROR** -- These block deployment.

| Rule     | Description                          | Rationale                              |
|----------|--------------------------------------|----------------------------------------|
| QPDK001  | Lua syntax error                     | Plugin will not load                   |
| QPDK002  | Missing PluginInfo table             | Plugin will not load                   |
| QPDK003  | Missing required PluginInfo field    | Plugin will not load                   |
| QPDK005  | Library object GC risk (local scope) | Timer/SSH silently stops working       |
| QPDK020  | Invalid Log API (Warning/Debug)      | Crash on real hardware                 |
| QPDK025  | Missing `if Controls then` guard     | Crash in Designer design-time context  |

**Action:** Run `qpdk validate --ruleset P0 plugin.qplug` to check only these.

### Phase 2: Quality Rules (Sprint 2)

**Severity: WARNING** -- Flagged but do not block deployment.

| Rule     | Description                          | Rationale                              |
|----------|--------------------------------------|----------------------------------------|
| QPDK004  | Invalid PluginInfo field value       | Cosmetic/correctness                   |
| QPDK006  | Undefined global (via luacheck)      | Possible typo or missing global        |
| QPDK008  | Duplicate plugin ID                  | Conflicts in multi-plugin designs      |
| QPDK009  | Version-specific API usage           | Fails on older Q-SYS versions          |
| QPDK021  | rapidjson without require()          | nil error at runtime                   |
| QPDK022  | tonumber() without nil guard         | Arithmetic on nil crashes              |
| QPDK023  | Status value out of range (0-5)      | Unpredictable UI behavior              |
| QPDK024  | Reserved name shadowing              | Subtle bugs from API shadowing         |
| QPDK028  | SSH handler incompleteness           | Silent failure on lifecycle events     |

**Action:** Run `qpdk validate --ruleset default plugin.qplug` to include these.

### Phase 3: Advisory Rules (Sprint 3+)

**Severity: INFO -> WARNING** -- Start as informational, promote after warm-up.

| Rule     | Description                          | Warm-up |
|----------|--------------------------------------|---------|
| QPDK010  | Control naming suggestion            | 2 weeks |
| QPDK026  | Missing GetControlLayout             | 2 weeks |
| QPDK027  | Duplicate property name              | 2 weeks |
| QPDK029  | SSH/TCP Write without pcall          | 2 weeks |
| QPDK030  | EventHandler closure captures loop   | 2 weeks |
| QPDK031  | Timer EventHandler missing Stop()    | 2 weeks |
| QPDK032  | Poll without connection guard        | 2 weeks |
| QPDK033  | xAPI string injection risk           | 2 weeks |
| QPDK034  | Monolithic function (>200 lines)     | 2 weeks |
| QPDK035  | PinStyle Both + Lua write conflict   | 2 weeks |
| QPDK036  | Dual-context write to indicator      | 2 weeks |
| QPDK037  | Missing monitor mode guard           | 2 weeks |

**Action:** Run `qpdk validate --ruleset full plugin.qplug` to see all advisories.

## Suppression Mechanism

Plugins can suppress known warnings with inline comments:

```lua
-- Suppress a single rule on this line:
sendData("xCommand " .. Controls.x.String)  -- qpdk:ignore QPDK033

-- Suppress multiple rules:
Controls.Outputs["status"].Value = 99  -- qpdk:ignore QPDK023,QPDK029

-- Suppress with explanation (recommended):
-- qpdk:ignore QPDK034 (parseData is known monolithic, tracked as tech debt)
```

### Suppression Rules

1. Suppression applies to the **line** where the comment appears (not the block)
2. Multiple codes can be suppressed with spaces or commas
3. The comment must start with `-- qpdk:ignore`
4. Suppression does NOT affect other lines
5. Suppressed warnings are still counted for tracking purposes

## Versioned Rule Sets

The `--ruleset` CLI flag controls which rules run:

| Ruleset   | Scope                               | Use Case            |
|-----------|-------------------------------------|---------------------|
| `P0`      | Phase 1 only (crash-preventing)     | CI/CD gate          |
| `default` | Phase 1 + Phase 2 (ERROR + WARNING) | Pre-merge review    |
| `full`    | All phases (ERROR + WARNING + INFO)  | Development audit   |

```bash
# CI/CD pipeline: only block on crash-causing issues
qpdk validate --ruleset P0 --strict plugin.qplug

# Developer workflow: see quality warnings too
qpdk validate --ruleset default plugin.qplug

# Full audit: see everything including advisories
qpdk validate --ruleset full plugin.qplug
```

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Regex heuristics produce excessive false positives | Developers ignore all warnings | Suppression comments, warm-up period as WARNING before ERROR |
| New rules fail all existing plugins on first run | Developer frustration, tool rejection | Phased rollout: crash-causing first (ERROR), quality later (WARNING -> ERROR) |
| Mock runtime changes break existing test suites | CI regressions | Run existing tests before/after each mock change |
| `--std` flag wrong (lua53 vs luajit) | Luacheck false errors or missed real errors | Test both flags against known-good Q-SYS plugins before committing |
| Cross-function regex analysis unreliable (QPDK035/036) | False negatives on real bugs | Document limitations; consider AST-based analysis as Phase 2 |

## Timeline

| Milestone | Date | Action |
|-----------|------|--------|
| Phase 1 deploy | Immediate | P0 rules as ERROR in CI/CD |
| Phase 2 deploy | Sprint 2 | Quality rules as WARNING |
| Phase 2 promote | Sprint 2 + 2 weeks | Selected WARNING -> ERROR |
| Phase 3 deploy | Sprint 3 | Advisory rules as INFO |
| Phase 3 promote | Sprint 3 + 2 weeks | Selected INFO -> WARNING |
