---
name: end-user-manual-generator
description: Analyzes any application, website, desktop program, workflow, or software system and produces complete end-user instructional manuals in editable Word and publication-ready PDF formats, with verified step-by-step procedures, actual screenshots, numbered annotations, callouts, troubleshooting, and quality assurance. Use when creating, updating, or reviewing user guides, SOPs, tutorials, operating manuals, or application documentation.
---

# End-User Instructional Manual Generator

## Mission

Create accurate, complete, accessible, workflow-driven instructional manuals that enable a person with ordinary end-user knowledge to complete real tasks in the target application or system without needing a developer to guide them. Workflows are a required documentation layer, not an optional visual enhancement.

Deliver **both**:
1. An editable Microsoft Word document (`.docx`).
2. A publication-ready PDF (`.pdf`) generated from the same approved content.

Use actual screenshots from the target application wherever possible. Add numbered markers and callouts that correspond exactly to the written steps. Never substitute invented interface screenshots or AI-generated depictions for evidence of the actual application.

## When to use

Use this skill when asked to create, update, expand, standardize, or quality-check:
- End-user manuals, user guides, operating manuals, quick-start guides, tutorials, and SOPs.
- Instructions for web applications, desktop software, mobile applications, GIS tools, plugins, dashboards, portals, and enterprise systems.
- Manuals for a specific role, module, process, or end-to-end workflow.

## Required behavior

- Treat the target application as the source of truth. Inspect available application access, source files, project documentation, screenshots, sample data, and user-provided instructions.
- Do not infer undocumented behavior from labels, code names, or assumptions.
- Do not fabricate buttons, menus, fields, permissions, outputs, validation rules, screenshots, error messages, or successful outcomes.
- Clearly distinguish **verified**, **inferred**, and **unverified** information. Only verified information may be presented as fact in a procedure.
- If a critical detail cannot be verified, mark it `[VERIFY WITH SYSTEM OWNER]` in the working draft and list it in an open-issues register. Do not silently fill gaps.
- Never expose credentials, tokens, personal data, confidential records, or sensitive production information in screenshots or document examples.
- Never change production data, submit transactions, delete records, or alter system configuration merely to obtain screenshots. Use a safe test environment or read-only walkthrough whenever possible.
- Do not claim a task was tested unless it was actually executed and observed.
- Use plain, professional English. Explain technical terms on first use.
- Make a workflow inventory and workflow diagrams mandatory for every major in-scope module or end-to-end business process. Where user roles have materially different tasks or permissions, document separate role-specific flows. Where errors, rejection, validation failure, cancellation, or recovery create a different path, document the relevant exception flow.
- Map every workflow activity to a detailed procedure step and, where useful, a screenshot/figure. Maintain traceability between workflow IDs, procedure IDs, screenshot IDs, and outputs.
- Write for end users, not developers. Avoid implementation details unless they directly affect user actions.
- Preserve exact interface labels, capitalization, field names, and menu paths as shown in the application.
- Ask the user only for essential missing information. If the application can be inspected, proceed with discovery before asking broad questions.

## Inputs and discovery

At the start, identify what is available:
1. Application/system name and version/build, if discoverable.
2. Application type: web, desktop, mobile, command-line, plugin, or hybrid.
3. Intended audience and user roles.
4. Scope: whole system, selected modules, or one workflow.
5. Available access: running application, test account, repository/source code, screenshots, existing manuals, requirements, sample files, or recordings.
6. Required terminology, branding, document template, language, and accessibility requirements.
7. Known constraints: permissions, network, device, browser, operating system, data restrictions, and approval steps.

If the user has not specified the audience, assume a first-time end user with basic computer literacy and state that assumption in the manual.

### Evidence priority

Use evidence in this order:
1. Direct observation of the running application in a safe environment.
2. Current approved product documentation or system-owner instructions.
3. Current source code/configuration when needed to confirm behavior.
4. User-provided screenshots, recordings, and workflow descriptions.
5. Reasonable inference, which must be labeled and must not be used to invent instructions.

Check version consistency. If screenshots, source code, and documentation disagree, record the discrepancy and seek confirmation or label the affected content as requiring verification.

## End-to-end workflow

### Phase 1 — Scope and plan

- Define the manual's title, audience, purpose, scope, prerequisites, and exclusions.
- Identify user roles and permissions.
- Create a workflow inventory covering start-to-finish user goals for every major module and process.
- Identify dependencies, decision points, optional paths, handoffs, user/system responsibilities, exception paths, and expected outputs.
- Separate workflows by user role when screens, permissions, approvals, or responsibilities differ.
- Identify normal, alternate, validation-failure, rejection, cancellation, and recovery paths where applicable.
- Create a coverage matrix mapping each task to a workflow ID, procedure ID, manual section, screenshot/figure, output, and evidence source.
- Do not begin drafting a supposedly complete manual until the workflow inventory and required workflow diagrams are sufficiently understood.

### Phase 2 — Inspect the application

- Navigate the application using the available browser, desktop, emulator, or test environment.
- Inspect the landing page, navigation, modules, forms, dialogs, tables, maps, filters, upload/download controls, reports, and relevant settings.
- Record exact labels and observed behavior.
- For each workflow, capture preconditions, actions, system response, success criteria, and possible failure paths.
- Inspect role-dependent behavior using authorized test roles where available.
- Use source code to corroborate behavior, not to replace user-facing observation.
- If access is unavailable, produce a clearly labeled draft based on supplied evidence and list the missing verification work.

### Phase 3 — Model and diagram the workflow

Workflow documentation is mandatory for every major module or end-to-end process. For each workflow:

- Assign a unique workflow ID and title.
- Create a visual diagram showing start/end, user actions, system actions, decision points, handoffs, outputs, and exception paths.
- Use separate diagrams for distinct user roles when their processes differ materially.
- Show validation failure, rejection, cancellation, retry, and recovery branches when those paths exist.
- Label activities with stable IDs (for example, WF01-A01) and map them to procedures and screenshots.
- Use Mermaid flowcharts as the preferred editable source. Render them as clear images for Word/PDF when a local renderer is available, and retain the source.
- Never invent branches or outcomes. Mark unverified details and add them to the open-issues register.
- Split complex diagrams into overview and sub-process diagrams rather than shrinking text.

For each user task, document:
- Task name and user goal.
- Responsible role and required permission.
- Preconditions and required input/data.
- Entry point and navigation path.
- Numbered actions in execution order.
- Expected screen/system response after each meaningful action.
- Validation rules and decision branches.
- Completion indicator and final output.
- Recovery or cancellation path.
- Related tasks and next step.

Use a decision tree when a workflow branches. Use a table only when it improves comparison or reference lookup.

### Phase 4 — Capture actual screenshots

- Capture screenshots from the actual application at the relevant step, preferably at a readable resolution.
- Capture the whole relevant window or page and retain enough context to orient the user.
- Use consistent zoom, window size, and visual scale within a manual.
- Remove or mask credentials, personal data, confidential records, browser profile details, and unrelated notifications.
- Capture a separate screenshot for a distinct screen or major state change; avoid one oversized screenshot for many unrelated steps.
- Use original screenshots as evidence. Do not redraw the interface or use generated mockups as if they were real.
- Annotate screenshots with numbered markers, arrows, boxes, or callouts. Each number must map to the same numbered action in the procedure.
- Keep annotations outside or adjacent to important controls when possible. Do not obscure labels, values, or the control being identified.
- Provide a legend/caption explaining the markers.
- If a screenshot cannot be captured, use a clearly labeled placeholder such as `[SCREENSHOT REQUIRED: exact screen/state]`; do not invent an image.
- Maintain a screenshot register containing image filename, screen/state, workflow step, capture date, application version, masking status, and annotation status.

See `references/screenshot-guidelines.md`.

### Phase 5 — Write procedures

Use the standard procedure pattern:

**Purpose** — What the user will accomplish.

**Before you begin** — Required role, permission, data, and prerequisites.

**Procedure**
1. Start with one clear action.
2. Identify the exact screen, menu, field, or control.
3. State what to enter, select, upload, or confirm.
4. Explain the expected response where useful.
5. Add a screenshot reference where it materially helps.

**Expected result** — What confirms success and where the result appears.

**If something goes wrong** — Explain verified errors and safe recovery steps.

Rules:
- Use imperative verbs: Open, Select, Enter, Choose, Review, Click, Save, Submit, Download.
- One primary action per numbered step wherever practical.
- Use exact UI labels in **bold**.
- Put literal values and example inputs in `monospace` where supported.
- Explain whether a field is required or optional only when verified.
- Explain destructive or irreversible actions before the user performs them.
- Distinguish Save, Submit, Approve, Publish, Export, and Download; do not treat them as interchangeable.
- Include the expected result, not just the click sequence.
- Avoid vague directions such as "fill out the necessary details" or "click the appropriate button."

### Phase 6 — Build the manual

Use `templates/manual-template.md` as the baseline and `references/visual-design-standard.md` as the mandatory layout specification. Adapt the outline to the actual application; remove irrelevant sections rather than filling them with generic content. The final manual must use a professional, task-oriented page architecture: clean cover, front matter, clickable contents, role/task finder, chapter openers, repeatable procedure pages, workflow maps, expected-result sections, and concise reference appendices. Avoid text-heavy pages and generic report styling.

Required workflow content for each major module/process:
1. Workflow overview and user goal.
2. Role(s) involved and responsibilities.
3. Preconditions, triggers, inputs, and dependencies.
4. Main end-to-end workflow diagram.
5. Role-specific sub-flow diagrams where needed.
6. Exception/alternate flow diagrams where applicable.
7. Detailed numbered instructions linked to workflow activity IDs.
8. Expected outputs, completion criteria, and next handoff.
9. Recovery, escalation, or rollback instructions where verified.

Include a workflow index and traceability matrix linking workflow IDs → activity IDs → procedure IDs → screenshot IDs → outputs.

Recommended structure:
1. Cover page and document control.
2. Table of contents.
3. Introduction: purpose, scope, audience, conventions.
4. System overview and navigation.
5. Requirements, access, roles, and permissions.
6. Getting started / first-time use.
7. End-to-end procedures, organized by user goal or module.
8. Reports, exports, downloads, and outputs.
9. Validation, errors, and troubleshooting.
10. Frequently asked questions.
11. Glossary and appendices.
12. Open issues / verification notes (include in draft; remove only when resolved or approved for publication).

Include document metadata: title, system name/version, manual version, publication date, owner/prepared by (only if supplied), review/approval status, and change history. Do not invent authors, approvers, dates, or official logos.

### Phase 7 — Generate Word and PDF

- Generate the `.docx` and `.pdf` from the same approved source content to prevent divergence.
- Use a consistent page size (default A4 unless the user or organization specifies otherwise), margins, heading hierarchy, page numbering, headers/footers, and caption styles.
- Use real Word heading styles so the document supports navigation and an automatic table of contents.
- Keep screenshots legible and proportionate; do not stretch them. Place each image close to its matching steps, use consistent numbered markers, and give each figure a descriptive caption.
- Keep each screenshot near the procedure it supports.
- Avoid splitting a numbered instruction from its screenshot/caption when practical.
- Use page breaks before major chapters when appropriate.
- Use accessible contrast, descriptive captions, meaningful link text, and alt text for screenshots.
- Confirm fonts are embedded or available in the PDF where possible.
- Prefer the supplied generation script `scripts/build_manual.py` when Python and its dependencies are available. Run `python scripts/build_manual.py --help` first. If the script cannot be used, use another reliable document-generation method and validate both outputs.
- Required deliverables: editable `.docx`, PDF `.pdf`, and optionally the Markdown source plus screenshot assets. Include an open-issues register if anything remains unverified.

### Phase 8 — Quality assurance

Run all checks in `references/quality-assurance.md`. At minimum:
- Verify every procedure against the application or authoritative evidence.
- Check that all in-scope tasks are covered from start to finish.
- Confirm every major module/process has an end-to-end workflow diagram.
- Confirm role-specific diagrams exist wherever role responsibilities or permissions materially differ.
- Confirm applicable exception paths (validation failure, rejection, cancellation, retry, recovery) are represented.
- Confirm every diagram activity maps to written instructions and that diagram paths agree with the application.
- Confirm workflow IDs, activity IDs, procedure IDs, screenshot IDs, and outputs are traceable.
- Confirm screenshot authenticity, privacy masking, legibility, and numbered cross-references.
- Check headings, page numbering, captions, links, tables, spelling, terminology, and document metadata.
- Open both Word and PDF outputs and inspect representative pages, including pages with screenshots, tables, and long procedures.
- Confirm that both formats contain the same approved content and that the visual hierarchy, page breaks, tables, diagrams, screenshots, and callouts render cleanly.
- Report unresolved issues and test limitations honestly.

## Deliverables and completion report

Provide:
- `System_User_Manual.docx`
- `System_User_Manual.pdf`
- `manual-source.md` (recommended)
- `screenshots/` (when screenshots are captured)
- `verification-register.md` (recommended)
- A concise completion report stating:
  - What was inspected and the version/environment.
  - Workflows covered.
  - Files produced.
  - QA checks completed.
  - Items not verified or requiring system-owner approval.

Do not call the manual "final" or "approved" unless the required review/approval actually occurred. Otherwise label it "Draft for Review".

## Decision rules

- **Application access available:** inspect and capture actual screenshots, then create a verified manual.
- **Only source code/docs available:** inspect evidence, create a source-backed draft, and mark UI behavior requiring live verification.
- **Only screenshots available:** create a screenshot-based draft; do not infer unshown workflow states.
- **Only a verbal workflow available:** document it as user-supplied information and mark it for system-owner validation.
- **Scope too broad:** propose a phased manual by role/module and start with the highest-priority end-to-end workflows.
- **Conflicting evidence:** stop short of asserting the disputed behavior; log the conflict.
- **Sensitive/production environment:** do not perform state-changing actions; request a safe test environment or approved demonstration data.

## Reference files

Read only the references needed for the task:
- `references/manual-structure.md`
- `references/visual-design-standard.md`
- `references/writing-guidelines.md`
- `references/screenshot-guidelines.md`
- `references/quality-assurance.md`
- `references/workflow-capture.md`
- `references/workflow-diagram-guidelines.md`
- `templates/workflow-inventory.csv`
- `templates/workflow-traceability-matrix.csv`
- `templates/manual-template.md`
- `examples/sample-instructions.md`

## Invocation examples

- "Create an end-user manual for this application using the end-user-manual-generator skill."
- "Document the complete workflow for submitting and approving a request, with overview, role-specific, and exception flow diagrams plus screenshots."
- "Create a workflow inventory and traceability matrix for every module in this application."
- "Update the existing user guide to match the current application interface."
- "Generate an editable Word manual and matching PDF for the QGIS plugin."
- "Review this manual for missing steps, inaccurate screenshots, and usability issues."
