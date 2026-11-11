---
name: check-tool
description: Check, validate and test a GMD CO Processing tool script (gmd_processing_tools/<script>.py) or the plugin core (plugin.py, script_registry.py, ui/, _coprocessing/) before committing or opening a PR. Runs three subagents in parallel — tool-code-reviewer (bugs, PyQGIS pitfalls), tool-validator (house conventions, metadata, docs/Help sync) and tool-tester (compile, import and headless QGIS smoke tests) — then merges their reports. Use when the user says "check / validate / test / review this tool", "is it ready to commit", or after a big change to a tool.
---

# Check a tool (review + validate + test)

## 1. Pick the target
- A script name or path the user gave → that file.
- Otherwise the changed files: `git diff --name-only main...HEAD` plus `git status --porcelain`.
  Keep `.py` files under `gmd_processing_tools/`, `ui/`, `plugin.py`, `script_registry.py`.
- Nothing found → ask which tool to check.

## 2. Launch the three agents in ONE message (parallel)
Give each the same target list and, if relevant, a one-line summary of what changed.

| Agent | Job |
|---|---|
| `tool-code-reviewer` | Correctness bugs, PyQGIS/Qt pitfalls, edit-buffer safety, crashes |
| `tool-validator` | House conventions: TOOL_NAME/GROUP/ICON, docstring, MBI EA Selector UI layout, multi-layer tick lists, Help tab + `docs/tools/<script>.md` + `docs/README.md` in sync |
| `tool-tester` | `py_compile`, pyflakes, import under QGIS Python, headless smoke tests with memory layers |

## 3. Merge the reports
Output one report:

```
## Check: <script>
Verdict: READY / NEEDS FIXES / BLOCKED
### Must fix   (bugs, failing tests, broken conventions)
### Should fix (docs drift, risky patterns)
### Tests      (what ran, pass/fail, command used, what was not tested)
```
- De-duplicate findings reported by more than one agent; cite `file:line`.
- Do not apply fixes unless the user asks. If they do, fix, then re-run only `tool-tester`.
- If the Help tab or docs are out of date, offer to run the `tool-docs` skill.
