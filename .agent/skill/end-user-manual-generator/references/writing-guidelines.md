# Writing Guidelines for End-User Manuals

## Voice and readability
- Use clear, direct, respectful language.
- Address the reader as "you" when natural.
- Prefer short sentences and familiar words.
- Define acronyms on first use.
- Avoid unexplained jargon, internal developer terminology, and marketing language.
- Keep one main idea per paragraph.
- Use active voice and action verbs.

## Step construction
Preferred: "1. Select **New Request** in the upper-right corner. The request form opens."

Avoid: "1. The user should then proceed to click on the appropriate button in order to create a request."

Every step should tell the reader:
1. Where they are.
2. What to do.
3. What to enter or select.
4. What happens next (when it matters).

## UI references
- Match the exact capitalization and spelling of interface labels.
- Use bold for visible buttons, tabs, menus, and field labels.
- Use `monospace` for literal paths, filenames, commands, and sample values.
- Quote example data and clearly label it as sample data.
- Distinguish required fields from optional fields only when confirmed.
- Explain disabled controls only when the reason is known.

## Terminology
Maintain a terminology table with preferred term, definition, and prohibited alternatives if needed. Use the same term throughout. Preserve official agency/system terminology where supplied.

## Inclusive and accessible writing
- Do not rely on color alone ("click the green button"); include the control label or location.
- Use meaningful headings and descriptive link text.
- Provide alt text for meaningful screenshots and diagrams.
- Avoid dense all-caps paragraphs.
- Explain keyboard alternatives where known and relevant.
- Do not assume a specific device, browser, or assistive technology unless the manual states it.

## Localization
Use the requested language and locale. Preserve exact UI labels even if the explanatory text is translated. Explain untranslated labels where helpful. Use consistent date, time, number, and measurement formats.

## Examples
Good: "Select **Upload File**, choose the approved CSV file, and select **Open**. The filename appears below the upload control."

Weak: "Upload the file."

Good: "Review the summary. If the total is correct, select **Submit**. A confirmation message appears."

Weak: "Submit when done."
