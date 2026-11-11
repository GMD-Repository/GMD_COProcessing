# End-User Instructional Manual Generator

A reusable Google Antigravity Agent Skill for creating end-user manuals for any application or software system.

## Contents
- `SKILL.md` — primary skill instructions and workflow.
- `references/` — manual structure, visual design standard, writing standards, screenshot rules, workflow capture, workflow-diagram standards, and QA checklist.
- `templates/manual-template.md` — reusable manual outline with workflow index and traceability.
- `templates/workflow-inventory.csv` — inventory of end-to-end, role-specific, and exception workflows.
- `templates/workflow-traceability-matrix.csv` — mapping of workflow activities to procedures, screenshots, and outputs.
- `examples/sample-workflow.mmd` — illustrative Mermaid workflow source.
- `examples/sample-instructions.md` — sample procedure writing style (demonstration only).
- `scripts/build_manual.py` — optional Markdown-to-Word/PDF generator.

## Install in Antigravity

### Workspace installation
Copy the `end-user-manual-generator` folder into your project:

```text
<workspace-root>/
└── .agents/
    └── skills/
        └── end-user-manual-generator/
            ├── SKILL.md
            ├── references/
            ├── templates/
            ├── examples/
            └── scripts/
```

### Global installation
Copy the skill folder to the global skills directory supported by your Antigravity surface:
- Antigravity App / IDE: `~/.gemini/config/skills/`
- Antigravity CLI: `~/.gemini/antigravity-cli/skills/` (the CLI also supports other documented global locations).

Restart or reload skills if needed. Invoke using `/end-user-manual-generator` where slash commands are supported, or ask the agent to use the skill by name.

## Generate Word and PDF

1. Create or approve a Markdown manual source file.
2. Ensure screenshot image paths are relative to that Markdown file.
3. Install dependencies:

```bash
pip install python-docx reportlab pillow
```

4. Run:

```bash
python scripts/build_manual.py manual-source.md --outdir output --title "System User Manual"
```

5. Inspect both files and run the QA checklist in `references/quality-assurance.md`. Apply `references/visual-design-standard.md` as the layout specification; the Markdown source is content input, not a guarantee of publication-quality layout.

## Important limitations
- The script is a lightweight converter, not a full publishing engine. For a polished publication layout—including an automatic clickable TOC, branded cover, complex callout blocks, and advanced page control—use Word styles/templates or a dedicated publishing workflow and perform visual QA.
- The script does not capture application screenshots or automatically discover UI workflows. Antigravity must use available browser/desktop access, project evidence, and authorized test environments.
- Numbered screenshot annotations must be created using an image editor or an approved annotation utility, then checked against the procedure steps.
- Do not use production-changing actions merely to capture screenshots.
- Never publish unverified behavior as fact.

## Recommended release workflow
Scope and workflow inventory → Evidence review → End-to-end/role/exception diagrams → Detailed procedures and screenshots → Traceability review → DOCX/PDF generation → Visual QA → System-owner review → Approved release.
