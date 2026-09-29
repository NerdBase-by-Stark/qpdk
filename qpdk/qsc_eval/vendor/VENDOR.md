# Vendored: Q-SYS Plugin-Evaluation-Script

Source: https://github.com/Q-SYS-Communities-for-Developers/Plugin-Evaluation-Script
Commit: 975358441cac94abc4ea08e4d593f56a54960c06
Vendored: 2026-07-09 · License: MIT (see LICENSE in this dir)
Files: evaluateDesignTime.lua, json.lua (unmodified). The upstream bash driver
(EvaluatePlugin.sh) is NOT vendored — its jq-layer checks are reimplemented in
../runner.py for portability (no Windows lua53.exe, no jq dependency).
