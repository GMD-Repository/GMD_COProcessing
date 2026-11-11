#!/usr/bin/env python3
"""
Build an editable DOCX and matching PDF from Markdown using python-docx and
ReportLab. This utility intentionally supports a practical subset of Markdown:
headings, paragraphs, unordered/ordered lists, tables, bold, italic, inline code,
and local images. Review both outputs after generation.

Run:
  python scripts/build_manual.py --help

Dependencies:
  pip install python-docx reportlab pillow
"""
from pathlib import Path
import argparse
import re
import sys

def parse_inline(text, paragraph, docx):
    from docx.shared import Pt
    # Simple inline parser for **bold**, *italic*, and `code`.
    pattern = re.compile(r'(\*\*.*?\*\*|\*.*?\*|`.*?`)')
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            paragraph.add_run(text[pos:m.start()])
        token = m.group(0)
        if token.startswith("**"):
            run = paragraph.add_run(token[2:-2]); run.bold = True
        elif token.startswith("*"):
            run = paragraph.add_run(token[1:-1]); run.italic = True
        else:
            run = paragraph.add_run(token[1:-1]); run.font.name = "Consolas"; run.font.size = Pt(9)
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])

def build_docx(md_path, out_path, title):
    from docx import Document
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.section import WD_SECTION
    doc = Document()
    sec = doc.sections[0]
    sec.top_margin = Inches(0.7); sec.bottom_margin = Inches(0.7)
    sec.left_margin = Inches(0.8); sec.right_margin = Inches(0.8)
    styles = doc.styles
    styles["Normal"].font.name = "Arial"; styles["Normal"].font.size = Pt(10)
    styles["Normal"].paragraph_format.space_after = Pt(6)
    for nm, size in [("Title", 24), ("Heading 1", 18), ("Heading 2", 14), ("Heading 3", 12)]:
        styles[nm].font.name = "Arial"; styles[nm].font.size = Pt(size)
    lines = md_path.read_text(encoding="utf-8").splitlines()
    in_code = False
    for line in lines:
        s = line.rstrip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if not s.strip():
            continue
        if s.strip() == "---":
            doc.add_page_break(); continue
        if s.startswith("# "):
            p = doc.add_heading(level=1); parse_inline(s[2:], p, doc); continue
        if s.startswith("## "):
            p = doc.add_heading(level=2); parse_inline(s[3:], p, doc); continue
        if s.startswith("### "):
            p = doc.add_heading(level=3); parse_inline(s[4:], p, doc); continue
        if s.startswith("> "):
            p = doc.add_paragraph(style="Quote"); parse_inline(s[2:], p, doc); continue
        if s.startswith("|") and s.endswith("|"):
            # Consecutive markdown table rows are parsed as a simple Word table.
            rows = []
            while lines and lines[0].strip().startswith("|"):
                row = lines.pop(0)
                cells = [c.strip() for c in row.strip().strip("|").split("|")]
                if all(re.fullmatch(r':?-{3,}:?', c) for c in cells):
                    continue
                rows.append(cells)
            if rows:
                cols = max(map(len, rows))
                table = doc.add_table(rows=1, cols=cols); table.style = "Table Grid"
                for ri, row in enumerate(rows):
                    cells = table.rows[0].cells if ri == 0 else table.add_row().cells
                    for ci in range(cols):
                        cells[ci].text = row[ci] if ci < len(row) else ""
            continue
        if s.startswith("- ") or s.startswith("* "):
            p = doc.add_paragraph(style="List Bullet"); parse_inline(s[2:], p, doc); continue
        m = re.match(r"^\d+[.)]\s+(.*)", s)
        if m:
            p = doc.add_paragraph(style="List Number"); parse_inline(m.group(1), p, doc); continue
        if s.startswith("![") and "](" in s:
            m = re.match(r"!\[(.*?)\]\((.*?)\)", s)
            if m:
                img = (md_path.parent / m.group(2)).resolve()
                if not img.exists():
                    alt = (md_path.parent / "docs" / m.group(2)).resolve()
                    if alt.exists():
                        img = alt
                if img.exists():
                    doc.add_picture(str(img), width=Inches(6.2))
                    cap = doc.add_paragraph(m.group(1)); cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                else:
                    doc.add_paragraph(f"[Image not found: {m.group(2)}]")
            continue
        p = doc.add_paragraph()
        parse_inline(s, p, doc)
    # Footer with page field.
    for section in doc.sections:
        fp = section.footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        fp.add_run(f"{title}  |  Page ")
        fld = OxmlField = None
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        fld = OxmlElement("w:fldSimple"); fld.set(qn("w:instr"), "PAGE")
        fp._p.append(fld)
    doc.save(out_path)

def build_pdf(md_path, out_path, title):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image
    from xml.sax.saxutils import escape
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ManualH1", parent=styles["Heading1"], fontSize=18, leading=22, spaceBefore=12, spaceAfter=8))
    styles.add(ParagraphStyle(name="ManualH2", parent=styles["Heading2"], fontSize=14, leading=18, spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(name="ManualH3", parent=styles["Heading3"], fontSize=12, leading=15, spaceBefore=8, spaceAfter=4))
    styles.add(ParagraphStyle(name="ManualBody", parent=styles["BodyText"], fontSize=9.5, leading=13, spaceAfter=6))
    styles.add(ParagraphStyle(name="Caption", parent=styles["Italic"], fontSize=9, leading=12, alignment=TA_CENTER, spaceBefore=4, spaceAfter=8))
    story=[]
    lines=md_path.read_text(encoding="utf-8").splitlines()
    def fmt(s):
        s=escape(s)
        s=re.sub(r'\*\*(.+?)\*\*',r'<b>\1</b>',s)
        s=re.sub(r'\*(.+?)\*',r'<i>\1</i>',s)
        s=re.sub(r'`(.+?)`',r'<font name="Courier">\1</font>',s)
        return s
    i=0
    while i < len(lines):
        s=lines[i].strip()
        if not s: i+=1; continue
        if s=="---": story.append(PageBreak()); i+=1; continue
        if s.startswith("```"):
            i+=1
            code=[]
            while i<len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i]); i+=1
            story.append(Paragraph("<br/>".join(escape(x) for x in code), styles["Code"])); i+=1; continue
        if s.startswith("|") and s.endswith("|"):
            rows=[]
            while i<len(lines) and lines[i].strip().startswith("|"):
                cells=[c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r':?-{3,}:?', c) for c in cells):
                    rows.append([Paragraph(fmt(c), styles["BodyText"]) for c in cells])
                i+=1
            if rows:
                t=Table(rows, repeatRows=1, hAlign="LEFT")
                t.setStyle(TableStyle([
                    ("GRID",(0,0),(-1,-1),0.4,colors.grey),
                    ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#E8EDF3")),
                    ("VALIGN",(0,0),(-1,-1),"TOP"),
                    ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
                    ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
                ]))
                story.extend([t,Spacer(1,6)])
            continue
        if s.startswith("# "): story.append(Paragraph(fmt(s[2:]), styles["Title"])); i+=1; continue
        if s.startswith("## "): story.append(Paragraph(fmt(s[3:]), styles["ManualH1"])); i+=1; continue
        if s.startswith("### "): story.append(Paragraph(fmt(s[4:]), styles["ManualH2"])); i+=1; continue
        if s.startswith("> "): story.append(Paragraph("<i>"+fmt(s[2:])+"</i>", styles["ManualBody"])); i+=1; continue
        if s.startswith("![") and "](" in s:
            m=re.match(r"!\[(.*?)\]\((.*?)\)",s)
            if m:
                img=(md_path.parent/m.group(2)).resolve()
                if not img.exists():
                    alt=(md_path.parent/"docs"/m.group(2)).resolve()
                    if alt.exists():
                        img=alt
                if img.exists():
                    im=Image(str(img)); im._restrictSize(165*mm,220*mm); story.extend([im,Paragraph(fmt(m.group(1)),styles["Caption"])])
                else: story.append(Paragraph(fmt(f"[Image not found: {m.group(2)}]"),styles["ManualBody"]))
            i+=1; continue
        m=re.match(r"^[-*]\s+(.*)",s)
        if m: story.append(Paragraph("&#8226; "+fmt(m.group(1)),styles["ManualBody"])); i+=1; continue
        m=re.match(r"^\d+[.)]\s+(.*)",s)
        if m: story.append(Paragraph(fmt(m.group(1)),styles["ManualBody"], bulletText=m.group(0).split()[0])); i+=1; continue
        story.append(Paragraph(fmt(s),styles["ManualBody"])); i+=1
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica",8)
        canvas.drawCentredString(A4[0]/2, 10*mm, f"{title}  |  Page {doc.page}")
        canvas.restoreState()
    doc=SimpleDocTemplate(str(out_path),pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=17*mm,bottomMargin=18*mm,title=title)
    doc.build(story,onFirstPage=footer,onLaterPages=footer)

def main():
    ap=argparse.ArgumentParser(description="Generate DOCX and PDF from a Markdown manual.")
    ap.add_argument("markdown", type=Path, help="Path to Markdown source file")
    ap.add_argument("--outdir", type=Path, default=Path("output"), help="Output directory")
    ap.add_argument("--title", default="End-User Manual", help="Document title for footer/metadata")
    args=ap.parse_args()
    if not args.markdown.exists():
        ap.error(f"Markdown file not found: {args.markdown}")
    args.outdir.mkdir(parents=True,exist_ok=True)
    stem=args.markdown.stem
    docx=args.outdir/f"{stem}.docx"
    pdf=args.outdir/f"{stem}.pdf"
    try:
        build_docx(args.markdown,docx,args.title)
        build_pdf(args.markdown,pdf,args.title)
    except ImportError as e:
        print(f"Missing dependency: {e}. Install with: pip install python-docx reportlab pillow",file=sys.stderr)
        return 2
    print(f"Created: {docx}\nCreated: {pdf}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
