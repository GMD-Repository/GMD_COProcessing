# Screenshot Capture, Numbering, and Callout Standard

## Objective
Screenshots must show the real application state and help the reader locate the controls described in the procedure.

## Capture requirements
- Capture from the actual application, preferably in a test or demonstration environment.
- Record application name/version, date, screen/page, user role (not account identity), and workflow step.
- Use a consistent viewport and zoom level.
- Capture enough surrounding context to identify the screen.
- Use a lossless format such as PNG for interface screenshots.
- Avoid blurry, compressed, cropped, or unnecessarily wide images.
- Do not use AI-generated interface images as evidence of the real application.
- If only a supplied screenshot exists, preserve its provenance and do not imply it was captured during the current test.

## Privacy and security
Before inserting an image:
- Inspect for names, email addresses, phone numbers, IDs, addresses, account details, tokens, passwords, private records, and confidential operational data.
- Mask sensitive information irreversibly in the delivered image (not merely with a translucent overlay).
- Do not include browser profile/account identifiers or unrelated desktop notifications.
- Use synthetic examples where possible.
- Never include passwords, access tokens, API keys, or session cookies.

## Numbered annotation convention
- Number markers in the order the reader should act: 1, 2, 3...
- Reset numbering for each screenshot unless a workflow-wide sequence is explicitly clearer.
- Use a consistent marker style: high-contrast circular number badge with readable digits.
- Use arrows or leader lines for small or crowded controls.
- Use rectangular outlines sparingly to highlight a region.
- Place labels/callouts outside the control when possible.
- Never cover the actual control label or essential data.
- Ensure each marker maps to exactly one written step or an explicitly referenced cluster of steps.
- Use the same number in the image and the corresponding instruction.
- Include a caption: figure number, screen name, and concise purpose.
- Add alt text describing the screen and the highlighted controls.

## Screenshot register
Maintain a register with:
| ID | Filename | Screen/state | Procedure/step | App version | Captured date | Source | Masked? | Annotated? | Verified? |
|---|---|---|---|---|---|---|---|---|---|

## Naming
Use stable descriptive names:
`S03_P02_01_request-form.png`
where S = section, P = procedure, and final number = screenshot sequence.
Avoid filenames such as `image1.png`, `final2.png`, or `screenshot-new.png`.

## When no screenshot is available
Use an explicit placeholder:
`[SCREENSHOT REQUIRED: Request form showing the required fields and Submit control]`
Do not create a fabricated screenshot or present a conceptual mockup as the actual interface.

## Annotation review
- Confirm every marker is visible at normal document zoom.
- Confirm numbers are sequential and unambiguous.
- Confirm arrows point to the intended target.
- Confirm no sensitive information remains recoverable.
- Confirm image quality after Word/PDF export.
