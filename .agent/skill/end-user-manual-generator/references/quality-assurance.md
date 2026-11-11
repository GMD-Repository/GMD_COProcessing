# Quality Assurance Checklist

Use this checklist before delivery. Record Pass / Fail / Not Applicable and evidence or notes.

## A. Scope and coverage
- [ ] Audience and user roles are stated.
- [ ] Scope, exclusions, assumptions, and system version are stated.
- [ ] All in-scope user goals are listed in a workflow inventory.
- [ ] Each major module/process has an end-to-end workflow diagram.
- [ ] Role-specific diagrams are included where roles have materially different tasks or permissions.
- [ ] Applicable exception paths (validation failure, rejection, cancellation, retry, recovery) are diagrammed.
- [ ] Each workflow has a corresponding detailed procedure.
- [ ] Workflow activities are traceable to procedure steps, screenshots, and expected outputs.
- [ ] Each procedure begins with prerequisites and ends with an expected result.
- [ ] Required handoffs, optional branches, and role differences are covered.

## B. Factual accuracy
- [ ] Every UI label matches the observed application.
- [ ] Every instruction and diagram node/branch is supported by direct observation or authoritative evidence.
- [ ] Workflow diagrams and written procedures agree on sequence, decisions, roles, and completion states.
- [ ] No controls, outputs, permissions, validation rules, or errors were invented.
- [ ] Conflicting or unverified information is logged.
- [ ] Destructive actions and irreversible consequences are clearly warned about.
- [ ] The manual does not claim testing or approval that did not occur.

## C. Screenshot integrity
- [ ] Screenshots are actual application captures or clearly identified user-supplied images.
- [ ] No generated mockup is represented as a real screenshot.
- [ ] Screenshots match the documented version and workflow state.
- [ ] Sensitive information is irreversibly masked.
- [ ] Each screenshot is legible at normal viewing size.
- [ ] Number markers correspond to the written instructions.
- [ ] Arrows/callouts point to the correct controls.
- [ ] Captions, figure numbers, and alt text are present.
- [ ] Screenshot register is complete.

## D. Usability and writing
- [ ] Language is clear for the intended end user.
- [ ] Steps are actionable and in the correct order.
- [ ] Each step identifies the control/location and action.
- [ ] Expected system responses are included where useful.
- [ ] Acronyms and technical terms are explained.
- [ ] Terminology is consistent.
- [ ] Warnings, notes, and tips are used appropriately.
- [ ] No ambiguous directions such as "click the appropriate button" remain.

## E. Word document
- [ ] DOCX opens successfully.
- [ ] Heading styles are semantic and navigation-friendly.
- [ ] Table of contents is present or can be updated in Word.
- [ ] Page numbers, headers/footers, and captions are consistent.
- [ ] Tables and screenshots fit within page margins.
- [ ] No clipped text, blank pages, broken images, or awkward page breaks.
- [ ] Links work and have meaningful labels.
- [ ] Document properties and version metadata are correct.

## F. PDF document
- [ ] PDF opens successfully.
- [ ] PDF page count and page size are correct.
- [ ] Text is selectable/searchable where expected.
- [ ] Screenshots are sharp and not stretched.
- [ ] Fonts render correctly.
- [ ] No clipped content, missing glyphs, or broken links.
- [ ] Page numbering and contents references are correct.

## G. Cross-format consistency
- [ ] DOCX and PDF contain the same approved text and figures.
- [ ] Headings, tables, screenshots, captions, and numbering match.
- [ ] Final output filenames and versions are consistent.

## H. Release readiness
- [ ] Open issues and verification gaps are listed.
- [ ] Draft/review/approval status is accurate.
- [ ] No secrets or sensitive personal data are included.
- [ ] Deliverables are named clearly and stored in the agreed location.

## Defect severity
- Critical: incorrect instruction likely to cause data loss, security/privacy breach, or serious operational error. Must fix before release.
- Major: workflow cannot be completed or important branch is missing. Must fix before release.
- Minor: clarity, formatting, or cosmetic issue. Fix where practical.
- Observation: improvement suggestion that does not block use.

## Final QA report
Record:
- Manual title/version:
- Application version/environment:
- Reviewer/date:
- Procedures checked:
- Screenshots checked:
- DOCX inspection:
- PDF inspection:
- Critical/major/minor defects:
- Unverified items:
- Release recommendation: Draft for Review / Ready for Approval / Approved (only with actual approval).
