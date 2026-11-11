# Visual Design Standard — Professional End-User Manual

## 1. Design objective

Create a clean, modern, task-oriented manual that feels like a published product guide. The reader should be able to scan a page, identify the task, follow the numbered actions, compare them with the screenshot, and confirm the result.

Use a restrained visual system. Avoid dense paragraphs, decorative graphics, oversized headings, clip-art, and unnecessary color blocks.

## 2. Page and typography system

- Default page: A4 portrait; use landscape only for wide tables, complex diagrams, or full-width screen captures.
- Margins: 18–20 mm. Keep a consistent text column and align screenshots/tables to it.
- Typeface: Aptos, Arial, or another widely available sans-serif. Use one family throughout.
- Body: 10–10.5 pt with comfortable line spacing; captions and metadata: 8–9 pt.
- Heading hierarchy: chapter 20–22 pt; section 14–16 pt; subsection 11–12 pt. Do not skip heading levels.
- Use dark charcoal for body text, one primary brand/accent color for headings and navigation, and neutral grey for rules, captions, and table fills. Do not invent official brand colors or logos.
- Use real Word heading styles, automatic page numbers, clickable table of contents, and descriptive image alt text where supported.

## 3. Page architecture

### Cover page
- System/application name and manual title.
- Short subtitle describing the user audience or task scope.
- Product screenshot or approved system mark only if available and appropriate.
- System version, manual version, publication date, document status, and owner (only if known).
- Keep the cover uncluttered; do not place the full document-control table on the cover.

### Front matter
- Document control and revision history.
- How to use this manual and icon/callout legend.
- Clickable table of contents.
- Role-based “Where should I start?” task index when multiple user roles exist.

### Chapter opener
- Chapter number and concise title.
- One- or two-sentence orientation explaining what the reader will accomplish.
- Optional workflow overview diagram for a process chapter.

### Procedure page
Use a repeatable task card structure:
1. **Task title** written as an action (for example, “Submit a Geospatial Data Request”).
2. Compact task summary: goal, role, estimated duration only if measured, and expected outcome.
3. **Before you begin**: permissions, prerequisites, inputs.
4. **Workflow at a glance**: small diagram or numbered stage strip.
5. **Steps**: numbered actions, one primary action per step.
6. **Screenshot**: actual application image close to the relevant steps; markers match the step numbers.
7. **Expected result**: visible confirmation and where to find the output.
8. **Next step / related task** and exception guidance.

Do not force every procedure into a single page. Split at logical points and repeat a short task title in the page header if the procedure continues.

## 4. Screenshot and callout treatment

- Use genuine application screenshots only. Preserve enough screen context to identify the page and navigation location.
- Crop irrelevant whitespace, but do not crop away labels or context needed to orient the user.
- Use numbered circular markers with a consistent accent fill and high-contrast numerals. Keep marker numbers in the same order as the procedure.
- Use arrows or thin outlines sparingly. Never cover the control label, data value, or important UI content.
- If the screenshot contains many targets, split it into two or more figures rather than adding too many markers.
- Caption format: `Figure 4-2. Request Details page — required fields and submission control.`
- Add a short legend when markers do not directly correspond one-to-one with numbered steps.
- Screenshot IDs must be unique and traceable in the screenshot register.

## 5. Workflow diagrams

- Place an overview diagram before detailed steps, not as a replacement for them.
- Keep diagrams readable at normal page zoom. Split complex processes into overview and subprocess diagrams.
- Use consistent shapes: rounded rectangles for start/end, rectangles for actions, diamonds for decisions, and document/output shapes only when useful.
- Label decision branches explicitly (Yes/No, Approved/Returned, Valid/Invalid).
- Include role swimlanes only when responsibility handoffs are important and the diagram remains readable.
- Caption each diagram and provide a short narrative for non-obvious branches.

## 6. Callout components

Use these labels consistently and only when relevant:
- **IMPORTANT** — requirement that affects completion or data integrity.
- **WARNING** — risk of loss, disclosure, or irreversible action.
- **NOTE** — clarification or system behavior the user should understand.
- **TIP** — optional shortcut or efficiency improvement.
- **EXPECTED RESULT** — observable confirmation after an action or task.

Keep callouts short (ideally one to three sentences). Do not use color alone to communicate meaning.

## 7. Tables and reference content

- Use tables for comparisons, field definitions, role permissions, status meanings, and troubleshooting—not for long narrative instructions.
- Repeat table header rows across pages. Avoid splitting a single row across pages where possible.
- Keep column headings short and meaningful. Use alternating subtle row shading only when it improves scanning.
- Move wide tables to landscape pages or appendices rather than shrinking text below 8 pt.

## 8. Navigation and consistency

- Use stable chapter, procedure, workflow, and figure numbering.
- Use cross-references such as “See Procedure 4.3” rather than vague “see above” references.
- Use consistent names for UI controls throughout the manual.
- Add running header with short system/manual title; footer with confidentiality/status label only if applicable, plus page number.
- Use descriptive links; avoid raw long URLs in body text.
- Use a glossary for domain-specific terms and abbreviations.

## 9. Accessibility and usability

- Ensure readable contrast and sufficient font size.
- Do not rely on color alone for status or meaning.
- Provide meaningful captions and alt text for screenshots and diagrams.
- Keep instructions in plain language and use active verbs.
- Avoid orphaned headings, isolated screenshot captions, and steps separated from the image they reference.
- Check the PDF at 100% zoom and in grayscale where possible.

## 10. Final visual review

Review the cover, table of contents, a typical procedure page, a screenshot-heavy page, a workflow diagram page, a table-heavy page, and the final page. Check alignment, page breaks, text wrapping, image clarity, marker legibility, header/footer consistency, and whitespace balance.
