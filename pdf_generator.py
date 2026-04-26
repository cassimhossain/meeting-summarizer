"""
pdf_generator.py — Bilingual PDF report generator (English + Urdu)

Features:
• Professional styling with section dividers, color accents, and clear hierarchy
• Full Urdu (RTL) support via arabic-reshaper + python-bidi
• Auto-detects whether to use LTR or RTL layout based on summary['_output_language']
• 'Both' mode: prints English first, then a parallel Urdu section
• Urdu font (Noto Nastaliq) with graceful fallback to system default if missing
• Color-coded priority badges for action items
• Clean tables for action items, decisions, and risks
• Page numbers and metadata footer
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table,
    TableStyle, PageBreak, HRFlowable, KeepTogether,
)

# Optional Urdu shaping libraries
try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    _URDU_LIBS_OK = True
except ImportError:
    _URDU_LIBS_OK = False
    print("[pdf] WARNING: arabic-reshaper / python-bidi not installed. "
          "Urdu output will not render correctly. "
          "Run: pip install arabic-reshaper python-bidi")


# ── Theme ──────────────────────────────────────────────────────────────────

PRIMARY = colors.HexColor("#1E40AF")        # deep blue
ACCENT = colors.HexColor("#0EA5E9")         # sky blue
DARK = colors.HexColor("#1F2937")           # near-black
MUTED = colors.HexColor("#6B7280")          # gray
LIGHT_BG = colors.HexColor("#F3F4F6")       # very light gray
SUCCESS = colors.HexColor("#10B981")        # green
WARNING = colors.HexColor("#F59E0B")        # amber
DANGER = colors.HexColor("#EF4444")         # red

PRIORITY_COLORS = {
    "High": DANGER,
    "Medium": WARNING,
    "Low": SUCCESS,
}

# ── Font registration ──────────────────────────────────────────────────────

URDU_FONT_NAME = "NotoNastaliq"
URDU_FONT_REGISTERED = False


def _register_urdu_font() -> bool:
    """
    Try to register the Urdu font from common locations. Returns True if
    successfully registered. Falls back to Helvetica if not found, which
    will render Urdu as boxes — but at least the PDF won't crash.
    """
    global URDU_FONT_REGISTERED
    if URDU_FONT_REGISTERED:
        return True

    candidates = [
        "fonts/NotoNastaliqUrdu-Regular.ttf",
        "fonts/NotoNastaliqUrdu.ttf",
        "/usr/share/fonts/truetype/noto/NotoNastaliqUrdu-Regular.ttf",
        "/Library/Fonts/NotoNastaliqUrdu-Regular.ttf",
        os.path.expanduser("~/.fonts/NotoNastaliqUrdu-Regular.ttf"),
    ]

    for path in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(URDU_FONT_NAME, path))
                URDU_FONT_REGISTERED = True
                print(f"[pdf] Registered Urdu font: {path}")
                return True
            except Exception as e:
                print(f"[pdf] Failed to register {path}: {e}")

    print("[pdf] WARNING: No Urdu font found. Urdu text may render as boxes.")
    print("[pdf] Download from: https://fonts.google.com/noto/specimen/Noto+Nastaliq+Urdu")
    print("[pdf] Place in ./fonts/NotoNastaliqUrdu-Regular.ttf")
    return False


# ── Urdu shaping helper ────────────────────────────────────────────────────

def _shape_urdu(text: str) -> str:
    """Reshape Urdu text and apply BiDi algorithm for correct rendering."""
    if not text or not _URDU_LIBS_OK:
        return text or ""
    try:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except Exception:
        return text


def _has_urdu_chars(text: str) -> bool:
    """Check if string contains any Urdu/Arabic Unicode range characters."""
    if not text:
        return False
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


# ── Style sheet ────────────────────────────────────────────────────────────

def _build_styles() -> dict[str, ParagraphStyle]:
    """Build the full set of paragraph styles used across the report."""
    base = getSampleStyleSheet()
    urdu_font = URDU_FONT_NAME if URDU_FONT_REGISTERED else "Helvetica"

    styles = {
        # Cover / title
        "Title": ParagraphStyle(
            "Title", parent=base["Title"],
            fontName="Helvetica-Bold", fontSize=26, leading=32,
            textColor=PRIMARY, alignment=TA_LEFT, spaceAfter=4,
        ),
        "Subtitle": ParagraphStyle(
            "Subtitle", parent=base["Normal"],
            fontName="Helvetica", fontSize=11, leading=14,
            textColor=MUTED, alignment=TA_LEFT, spaceAfter=12,
        ),

        # Section heading
        "H1": ParagraphStyle(
            "H1", parent=base["Heading1"],
            fontName="Helvetica-Bold", fontSize=15, leading=20,
            textColor=PRIMARY, alignment=TA_LEFT,
            spaceBefore=14, spaceAfter=6,
        ),
        "H2": ParagraphStyle(
            "H2", parent=base["Heading2"],
            fontName="Helvetica-Bold", fontSize=12, leading=15,
            textColor=DARK, alignment=TA_LEFT,
            spaceBefore=8, spaceAfter=4,
        ),

        # Body
        "Body": ParagraphStyle(
            "Body", parent=base["Normal"],
            fontName="Helvetica", fontSize=10.5, leading=15,
            textColor=DARK, alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        "Bullet": ParagraphStyle(
            "Bullet", parent=base["Normal"],
            fontName="Helvetica", fontSize=10.5, leading=14,
            textColor=DARK, leftIndent=14, bulletIndent=4, spaceAfter=3,
        ),
        "Meta": ParagraphStyle(
            "Meta", parent=base["Normal"],
            fontName="Helvetica-Oblique", fontSize=9, leading=12,
            textColor=MUTED, alignment=TA_LEFT,
        ),

        # Urdu (RTL)
        "UrduTitle": ParagraphStyle(
            "UrduTitle", parent=base["Title"],
            fontName=urdu_font, fontSize=22, leading=34,
            textColor=PRIMARY, alignment=TA_RIGHT, spaceAfter=4,
            wordWrap="RTL",
        ),
        "UrduH1": ParagraphStyle(
            "UrduH1", parent=base["Heading1"],
            fontName=urdu_font, fontSize=14, leading=24,
            textColor=PRIMARY, alignment=TA_RIGHT,
            spaceBefore=14, spaceAfter=6, wordWrap="RTL",
        ),
        "UrduBody": ParagraphStyle(
            "UrduBody", parent=base["Normal"],
            fontName=urdu_font, fontSize=11, leading=22,
            textColor=DARK, alignment=TA_RIGHT,
            spaceAfter=6, wordWrap="RTL",
        ),
        "UrduBullet": ParagraphStyle(
            "UrduBullet", parent=base["Normal"],
            fontName=urdu_font, fontSize=11, leading=20,
            textColor=DARK, alignment=TA_RIGHT,
            rightIndent=14, spaceAfter=3, wordWrap="RTL",
        ),
    }
    return styles


# ── Page decoration (header / footer) ──────────────────────────────────────

def _draw_page_chrome(canvas, doc):
    """Draw header bar and footer (page number + generated timestamp)."""
    canvas.saveState()

    # Top accent bar
    canvas.setFillColor(PRIMARY)
    canvas.rect(0, A4[1] - 0.4 * cm, A4[0], 0.4 * cm, fill=1, stroke=0)

    # Footer line
    canvas.setStrokeColor(LIGHT_BG)
    canvas.setLineWidth(0.5)
    canvas.line(2 * cm, 1.5 * cm, A4[0] - 2 * cm, 1.5 * cm)

    # Footer text
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(2 * cm, 1.0 * cm,
                      f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    canvas.drawRightString(A4[0] - 2 * cm, 1.0 * cm, f"Page {doc.page}")
    canvas.drawCentredString(A4[0] / 2, 1.0 * cm, "Meeting Summarizer")

    canvas.restoreState()


# ── Section builders ──────────────────────────────────────────────────────

def _kv_table(rows: list[tuple[str, str]]) -> Table:
    """Compact key-value metadata table (left-aligned)."""
    t = Table(rows, colWidths=[3.5 * cm, 13 * cm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), PRIMARY),
        ("TEXTCOLOR", (1, 0), (1, -1), DARK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _stats_strip(stats: dict, attendees: int) -> Table:
    """Five-cell colored strip showing meeting statistics."""
    cells = [
        [str(attendees), str(stats.get("action_item_count", 0)),
         str(stats.get("decision_count", 0)),
         str(stats.get("open_question_count", 0)),
         str(stats.get("risk_count", 0))],
        ["Attendees", "Action Items", "Decisions", "Open Qs", "Risks"],
    ]
    t = Table(cells, colWidths=[3.4 * cm] * 5, rowHeights=[0.9 * cm, 0.6 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("BACKGROUND", (0, 1), (-1, 1), LIGHT_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("TEXTCOLOR", (0, 1), (-1, 1), MUTED),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, 0), 18),
        ("FONTSIZE", (0, 1), (-1, 1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.white),
    ]))
    return t


def _action_items_table(items: list[dict], styles: dict) -> Table | None:
    if not items:
        return None
    header = ["Task", "Owner", "Due", "Priority"]
    rows = [header]
    for item in items:
        priority = item.get("priority", "Medium")
        rows.append([
            Paragraph(item.get("task", ""), styles["Body"]),
            item.get("owner", "Unassigned"),
            item.get("due_date", "—"),
            priority,
        ])

    t = Table(rows, colWidths=[8 * cm, 3.5 * cm, 3 * cm, 2 * cm],
              repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("FONTSIZE", (0, 1), (-1, -1), 9.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("LINEBELOW", (0, 0), (-1, 0), 1, PRIMARY),
        ("ALIGN", (3, 1), (3, -1), "CENTER"),
    ]
    # Color-code priority cells
    for i, item in enumerate(items, start=1):
        color = PRIORITY_COLORS.get(item.get("priority", "Medium"), MUTED)
        style.append(("TEXTCOLOR", (3, i), (3, i), color))
        style.append(("FONTNAME", (3, i), (3, i), "Helvetica-Bold"))
    t.setStyle(TableStyle(style))
    return t


def _decisions_block(decisions: list[dict], styles: dict) -> list:
    out = []
    for d in decisions:
        out.append(Paragraph(f"<b>•</b> {d.get('decision', '')}", styles["Bullet"]))
        rationale = d.get("rationale") or "—"
        decided_by = d.get("decided_by") or "—"
        out.append(Paragraph(
            f"<font color='#6B7280'>Rationale: {rationale} &nbsp;|&nbsp; "
            f"Decided by: {decided_by}</font>",
            styles["Meta"],
        ))
        out.append(Spacer(1, 4))
    return out


def _risks_block(risks: list[dict], styles: dict) -> list:
    out = []
    for r in risks:
        likelihood = r.get("likelihood", "Unknown")
        color = PRIORITY_COLORS.get(likelihood, MUTED).hexval()[2:]
        out.append(Paragraph(
            f"<b>•</b> {r.get('risk', '')} "
            f"<font color='#{color}'><b>[{likelihood}]</b></font>",
            styles["Bullet"],
        ))
        mitigation = r.get("mitigation") or "None discussed"
        out.append(Paragraph(
            f"<font color='#6B7280'>Mitigation: {mitigation}</font>",
            styles["Meta"],
        ))
        out.append(Spacer(1, 4))
    return out


def _bullet_list(items: list[str], styles: dict) -> list:
    return [Paragraph(f"• {it}", styles["Bullet"]) for it in items]


def _urdu_bullet_list(items: list[str], styles: dict) -> list:
    return [Paragraph(_shape_urdu(f"• {it}"), styles["UrduBullet"]) for it in items]


# ── Section: English content ──────────────────────────────────────────────

def _english_sections(summary: dict, styles: dict) -> list:
    story: list = []

    # Title + subtitle
    story.append(Paragraph(summary.get("meeting_title", "Meeting Summary"),
                           styles["Title"]))
    story.append(Paragraph(
        f"{summary.get('meeting_type', 'meeting').title()} &nbsp;•&nbsp; "
        f"{summary.get('duration_estimate', 'Unknown duration')} &nbsp;•&nbsp; "
        f"Sentiment: {summary.get('sentiment', 'neutral').title()}",
        styles["Subtitle"],
    ))
    story.append(HRFlowable(width="100%", thickness=0.6, color=ACCENT,
                            spaceBefore=2, spaceAfter=10))

    # Stats strip
    story.append(_stats_strip(
        summary.get("stats", {}),
        attendees=len(summary.get("attendees", [])),
    ))
    story.append(Spacer(1, 14))

    # Executive summary
    story.append(Paragraph("Executive Summary", styles["H1"]))
    summary_text = summary.get("summary", "No summary available.")
    for para in summary_text.split("\n\n"):
        if para.strip():
            story.append(Paragraph(para.strip(), styles["Body"]))

    # Attendees
    if summary.get("attendees"):
        story.append(Paragraph("Attendees", styles["H1"]))
        story.append(Paragraph(", ".join(summary["attendees"]), styles["Body"]))

    # Key topics
    if summary.get("key_topics"):
        story.append(Paragraph("Key Topics", styles["H1"]))
        story.append(Paragraph(
            " &nbsp;·&nbsp; ".join(summary["key_topics"]),
            styles["Body"],
        ))

    # Action items
    action_table = _action_items_table(summary.get("action_items", []), styles)
    if action_table is not None:
        story.append(Paragraph("Action Items", styles["H1"]))
        story.append(action_table)

    # Decisions
    if summary.get("decisions"):
        story.append(Paragraph("Decisions Made", styles["H1"]))
        story.extend(_decisions_block(summary["decisions"], styles))

    # Open questions
    if summary.get("open_questions"):
        story.append(Paragraph("Open Questions", styles["H1"]))
        for q in summary["open_questions"]:
            story.append(Paragraph(
                f"• {q.get('question', '')} "
                f"<font color='#6B7280'>→ {q.get('assigned_to', 'Team')} "
                f"({q.get('urgency', 'Medium')})</font>",
                styles["Bullet"],
            ))

    # Risks
    if summary.get("risks"):
        story.append(Paragraph("Risks & Concerns", styles["H1"]))
        story.extend(_risks_block(summary["risks"], styles))

    # Next steps
    if summary.get("next_steps"):
        story.append(Paragraph("Next Steps", styles["H1"]))
        story.extend(_bullet_list(summary["next_steps"], styles))

    return story


# ── Section: Urdu content ─────────────────────────────────────────────────

def _urdu_sections(summary: dict, styles: dict, full_urdu: bool = False) -> list:
    """
    full_urdu=True  → entire report is in Urdu (output_language='urdu')
    full_urdu=False → bilingual mirror section (output_language='both')
    """
    story: list = []

    # Section header in English to mark the Urdu section in 'both' mode
    if not full_urdu:
        story.append(PageBreak())
        story.append(Paragraph("اردو خلاصہ &nbsp;/&nbsp; Urdu Summary",
                               styles["UrduTitle"]))
        story.append(HRFlowable(width="100%", thickness=0.6, color=ACCENT,
                                spaceBefore=2, spaceAfter=10))

    # Title
    title_ur = summary.get("meeting_title_ur") or summary.get("meeting_title", "")
    if title_ur:
        story.append(Paragraph(_shape_urdu(title_ur), styles["UrduTitle"]))
        story.append(Spacer(1, 8))

    # Summary
    story.append(Paragraph(_shape_urdu("خلاصہ"), styles["UrduH1"]))
    summary_ur = summary.get("summary_ur") or summary.get("summary", "")
    for para in summary_ur.split("\n\n"):
        if para.strip():
            story.append(Paragraph(_shape_urdu(para.strip()), styles["UrduBody"]))

    # Key topics (Urdu)
    topics_ur = summary.get("key_topics_ur") or summary.get("key_topics", [])
    if topics_ur:
        story.append(Paragraph(_shape_urdu("اہم موضوعات"), styles["UrduH1"]))
        story.append(Paragraph(
            _shape_urdu(" · ".join(topics_ur)),
            styles["UrduBody"],
        ))

    # Next steps (Urdu)
    next_ur = summary.get("next_steps_ur") or summary.get("next_steps", [])
    if next_ur:
        story.append(Paragraph(_shape_urdu("اگلے اقدامات"), styles["UrduH1"]))
        story.extend(_urdu_bullet_list(next_ur, styles))

    # In full-Urdu mode, also render action items, decisions, etc. in Urdu
    # (these come from the LLM already in Urdu when output_language='urdu')
    if full_urdu:
        if summary.get("action_items"):
            story.append(Paragraph(_shape_urdu("ایکشن آئٹمز"), styles["UrduH1"]))
            for item in summary["action_items"]:
                line = f"• {item.get('task', '')} — {item.get('owner', '')}"
                story.append(Paragraph(_shape_urdu(line), styles["UrduBullet"]))

        if summary.get("decisions"):
            story.append(Paragraph(_shape_urdu("فیصلے"), styles["UrduH1"]))
            for d in summary["decisions"]:
                story.append(Paragraph(_shape_urdu(f"• {d.get('decision', '')}"),
                                       styles["UrduBullet"]))

    return story


# ── Public API ─────────────────────────────────────────────────────────────

def generate_pdf(
    summary: dict[str, Any],
    output_path: str = "output/meeting_report.pdf",
    transcript: str | None = None,
) -> str:
    """
    Generate a PDF report from a structured summary dict.

    Args:
        summary:     Dict from summarizer.summarize_transcript().
        output_path: Where to write the PDF.
        transcript:  Optional — full raw transcript appended at the end.

    Returns:
        Absolute path to the generated PDF.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Determine layout language from summary metadata
    output_language = summary.get("_output_language", "english")

    # Register Urdu font if we'll need it
    if output_language in ("urdu", "both") or _has_urdu_chars(summary.get("summary", "")):
        _register_urdu_font()

    styles = _build_styles()

    # Set up document with custom page template
    doc = BaseDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=summary.get("meeting_title", "Meeting Summary"),
        author="Meeting Summarizer",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="default", frames=frame,
                                       onPage=_draw_page_chrome)])

    # Build story
    story: list = []

    if output_language == "urdu":
        # Full Urdu report
        story.extend(_urdu_sections(summary, styles, full_urdu=True))
    elif output_language == "both":
        # English first, then Urdu mirror
        story.extend(_english_sections(summary, styles))
        story.extend(_urdu_sections(summary, styles, full_urdu=False))
    else:
        # English only (default)
        story.extend(_english_sections(summary, styles))

    # Optional: appendix with raw transcript
    if transcript:
        story.append(PageBreak())
        story.append(Paragraph("Appendix: Full Transcript", styles["H1"]))
        story.append(HRFlowable(width="100%", thickness=0.4, color=MUTED,
                                spaceBefore=2, spaceAfter=10))
        # Render in chunks to avoid one giant paragraph
        for chunk in transcript.split("\n\n"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if _has_urdu_chars(chunk):
                _register_urdu_font()
                story.append(Paragraph(_shape_urdu(chunk), styles["UrduBody"]))
            else:
                story.append(Paragraph(chunk, styles["Body"]))

    # Render
    doc.build(story)
    print(f"[pdf] Report written to: {output_path}")
    return os.path.abspath(output_path)


# ── Backward-compat alias ─────────────────────────────────────────────────

def generate_report(*args, **kwargs):
    """Alias for older call sites."""
    return generate_pdf(*args, **kwargs)