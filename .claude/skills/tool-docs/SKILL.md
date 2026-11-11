---
name: tool-docs
description: Write or refresh the GitHub documentation page docs/tools/<script>.md for a GMD CO Processing tool script in gmd_processing_tools/ (quick start, a "How it works" flowchart and step-by-step explanation of the processing, rules, outputs, troubleshooting), and keep docs/README.md and the tool's in-window Help tab in step. Use when the user asks to document a tool, generate/update docs or a README for a script, asks "how does this process/tool work?" for a tool, or after adding or changing a tool's buttons, fields, outputs or workflow.
---

# Tool documentation for GMD CO Processing

Each tool script in `gmd_processing_tools/` has one GitHub page in `docs/tools/<script_name>.md`
(same base name as the `.py`, e.g. `mbi_ea_selector.py` → `docs/tools/mbi_ea_selector.md`), listed in
`docs/README.md`. Readers are GMD staff running the tools in QGIS, plus developers maintaining them.

## Where things go

- Pages go in `docs/tools/`, **never** in `gmd_processing_tools/` — that folder is scanned for tools, and
  only `gmd_processing_tools/README.md` (the script-writing guide) belongs there.
- Scripts starting with `_` and the `_coprocessing/` package are helpers, not tools: no page of their own
  (document them inside the page of the tool that uses them, e.g. `earf_post_processing.md`).
- After adding a page, add a row to the table in `docs/README.md` (Tool · Script · What it does).

## Agent workflow

Run the steps below through three subagents, in order. Give each one the script path and this skill's path.

1. **`docs-researcher`** (read-only) does step 1. It returns a fact sheet: labels, fields, constants
   with their values, outputs, stages, and any mismatch between `HELP_HTML` and the code.
2. **`docs-writer`** gets the fact sheet and does steps 2, 3, 5 and 6. It writes `docs/tools/<script>.md`,
   the row in `docs/README.md`, and `HELP_HTML` plus the docstring when they are out of date.
3. **`docs-verifier`** (read-only) does step 4 and the Style checks on what the writer produced.
   If it reports FAIL items, send them back to `docs-writer`, then run the verifier again (2 rounds at most).

After that, give the user a summary: which files changed, the verifier's verdict, and the release
warning from "Before committing" if it applies. For a one-line docs fix, skip the agents and edit directly.

## Steps

1. **Read the source of truth**, in this order: the script's module docstring, its `HELP_HTML`
   (the in-window Help tab, if any), its `TOOL_NAME` / `TOOL_GROUP`, the UI-building code (button,
   checkbox and label texts), and the constants for fields it reads / writes and files it exports.
2. **Write the page** with this outline (drop sections that do not apply):

   ```markdown
   # <TOOL_NAME>

   **Script:** [`gmd_processing_tools/<file>.py`](../../gmd_processing_tools/<file>.py)
   **Menu:** GMD CO Processing › <TOOL_GROUP> › <TOOL_NAME>

   <2–3 sentences: what it does, what comes before / after it.>

   ## Quick start          numbered steps, exact UI labels in bold
   ## How it works         answers "How does this process work?" — required, see below
   ## <how it decides>     the rules users must understand, as tables
   ## What gets written    fields written (name · contents), layers added, export files
   ## Troubleshooting      Problem | Fix table
   ```

3. **Write "How it works"** — every page must answer *"How does this process work?"*:
   - A ` ```mermaid ` `flowchart LR` (GitHub renders it): inputs → processing stages → outputs, about
     6–10 nodes, using the names users see (layers, fields, buttons).
   - Then numbered steps, one per stage (**Prepare / Detect / Match / Decide / Write** …). Each step says
     what is done to the data, in plain words, with the real rules and numbers from the code:
     thresholds (m², m, %), CRS used for measuring, matching keys (e.g. first 8 geocode digits),
     tie-breaks / ordering (e.g. "first ticked keeps a shared piece"), and what is skipped.
   - Take every number and rule from the code (constants, function bodies), not from memory or from
     other pages — grep the constant (e.g. `SLIVER_M2`, `MATCH_MIN_M2`) and quote its value.
   - End with what is written or added and what is never edited.
4. **Verify every UI label and field name** against the code before finishing — grep for each bold
   label, e.g. `grep -on 'button("[^"]*"\|QCheckBox("[^"]*"' gmd_processing_tools/<file>.py`. Qt `&&` in
   source is a single `&` on screen. Fix any label that does not match exactly.
5. **Keep the Help tab in step.** If the change came from a code change, the script's `HELP_HTML` and module
   docstring must say the same thing as the page (the user requires the Help tab to be updated with every
   feature change).
6. **Cross-link** related tools with relative links (`[MBI EA Selector](mbi_ea_selector.md)`).

## Style

- GitHub-flavoured Markdown only (no HTML). Tables for rules, fields and outputs; short sentences.
- Exact UI labels in **bold**, field / file / layer names in `code`, menus as `Menu › Submenu › Item`.
- Describe current behaviour only — no change history ("now", "no longer", "new in").
- Geocodes: 14 digits; the barangay is the first 8 digits, the EA number the last 6
  (`02709007000000` barangay, `02709007001000` EA). Reuse the table in `docs/README.md` rather than repeat it.
- State what is never edited and how to undo (QGIS edit session: Save Edits / Edit › Undo / Discard Edits)
  for any tool that edits layers.

## Before committing

Pushing to `main` triggers an automatic plugin release unless the changed paths are in `paths-ignore` of
`.github/workflows/release.yml` (currently `README.md`, `.gitignore`, `.gitattributes`). Tell the user when
a docs-only push would publish a release.
