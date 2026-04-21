"""
pdf_report.py  –  Structured summary dict → polished PDF report (ReportLab)
Improvements over v1:
  • Stats summary bar (action items, decisions, risks, attendees)
  • Color-coded priority badges in action items table
  • Sentiment indicator in header
  • Risks section with likelihood color coding
  • Speaker contributions section
  • Page numbers in footer via canvas callback
  • Meeting type badge in header
  • Visual section dividers with icons (unicode)
  • Context column in action items
  • Impact column in decisions
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ── Brand palette ──────────────────────────────────────────────────────────
NAVY    = colors.HexColor("#0F2044")
BLUE    = colors.HexColor("#1D4ED8")
LBLUE   = colors.HexColor("#EFF6FF")
DBLUE   = colors.HexColor("#1E3A8A")
MGREY   = colors.HexColor("#64748B")
LGREY   = colors.HexColor("#F8FAFC")
DGREY   = colors.HexColor("#334155")
BORDER  = colors.HexColor("#CBD5E1")

GREEN   = colors.HexColor("#15803D")
LGREEN  = colors.HexColor("#DCFCE7")
AMBER   = colors.HexColor("#B45309")
LAMBER  = colors.HexColor("#FEF3C7")
RED     = colors.HexColor("#B91C1C")
LRED    = colors.HexColor("#FEE2E2")
PURPLE  = colors.HexColor("#6D28D9")
LPURPLE = colors.HexColor("#EDE9FE")
TEAL    = colors.HexColor("#0F766E")
LTEAL   = colors.HexColor("#CCFBF1")

# Priority → (text color, background)
PRIORITY_COLORS: dict[str, tuple] = {
    "high":   (RED,    LRED),
    "medium": (AMBER,  LAMBER),
    "low":    (GREEN,  LGREEN),
}

# Sentiment → badge color
SENTIMENT_COLORS: dict[str, tuple] = {
    "positive":   (GREEN,  LGREEN),
    "neutral":    (MGREY,  LGREY),
    "mixed":      (AMBER,  LAMBER),
    "tense":      (RED,    LRED),
    "unresolved": (PURPLE, LPURPLE),
}

# Likelihood → color
LIKELIHOOD_COLORS: dict[str, tuple] = {
    "high":    (RED,    LRED),
    "medium":  (AMBER,  LAMBER),
    "low":     (GREEN,  LGREEN),
    "unknown": (MGREY,  LGREY),
}

# Meeting type → friendly label
MEETING_TYPE_LABELS: dict[str, str] = {
    "standup":      "Stand-up",
    "planning":     "Planning",
    "retrospective":"Retrospective",
    "review":       "Review",
    "brainstorm":   "Brainstorm",
    "1-on-1":       "1-on-1",
    "all-hands":    "All Hands",
    "client-call":  "Client Call",
    "interview":    "Interview",
    "other":        "Meeting",
}

PAGE_W, PAGE_H = A4
LEFT_M = RIGHT_M = 22 * mm
TOP_M = BOTTOM_M = 20 * mm
CONTENT_W = PAGE_W - LEFT_M - RIGHT_M


# ── Style factory ──────────────────────────────────────────────────────────

def _styles() -> dict[str, ParagraphStyle]:
    def s(name, **kw) -> ParagraphStyle:
        defaults = dict(fontName="Helvetica", fontSize=10,
                        textColor=DGREY, leading=14)
        defaults.update(kw)
        return ParagraphStyle(name, **defaults)

    return {
        "title":    s("title",   fontSize=22, fontName="Helvetica-Bold",
                       textColor=NAVY, spaceAfter=4, leading=26),
        "subtitle": s("sub",     fontSize=10, textColor=MGREY, spaceAfter=2),
        "section":  s("section", fontSize=13, fontName="Helvetica-Bold",
                       textColor=NAVY, spaceBefore=16, spaceAfter=8),
        "body":     s("body",    fontSize=10, spaceAfter=4, leading=15),
        "item":     s("item",    fontSize=10, spaceAfter=3, leading=13,
                       leftIndent=10),
        "small":    s("small",   fontSize=8,  textColor=MGREY, leading=11),
        "badge":    s("badge",   fontSize=8,  fontName="Helvetica-Bold",
                       alignment=TA_CENTER, leading=10),
        "th":       s("th",      fontSize=9,  fontName="Helvetica-Bold",
                       textColor=colors.white, alignment=TA_LEFT, leading=12),
        "td":       s("td",      fontSize=9,  leading=12, spaceAfter=0),
        "stat_num": s("stat_n",  fontSize=20, fontName="Helvetica-Bold",
                       textColor=NAVY, alignment=TA_CENTER, leading=22),
        "stat_lbl": s("stat_l",  fontSize=8,  textColor=MGREY,
                       alignment=TA_CENTER, leading=10),
        "footer":   s("footer",  fontSize=8,  textColor=MGREY,
                       alignment=TA_CENTER),
    }


# ── Helpers ────────────────────────────────────────────────────────────────

def _priority_badge(priority: str, badge_style: ParagraphStyle) -> Table:
    """Render a colored pill for High / Medium / Low."""
    key = priority.lower()
    fg, bg = PRIORITY_COLORS.get(key, (MGREY, LGREY))
    cell = Paragraph(f"<font color='#{_hex(fg)}'><b>{priority.title()}</b></font>",
                     badge_style)
    tbl = Table([[cell]], colWidths=[18 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), bg),
        ("ROUNDEDCORNERS", [3]),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return tbl


def _likelihood_badge(likelihood: str, badge_style: ParagraphStyle) -> Table:
    key = likelihood.lower()
    fg, bg = LIKELIHOOD_COLORS.get(key, (MGREY, LGREY))
    cell = Paragraph(f"<font color='#{_hex(fg)}'><b>{likelihood.title()}</b></font>",
                     badge_style)
    tbl = Table([[cell]], colWidths=[18 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), bg),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return tbl


def _hex(c: colors.Color) -> str:
    """Return 6-char hex string for a ReportLab color (no '#')."""
    r, g, b = int(c.red * 255), int(c.green * 255), int(c.blue * 255)
    return f"{r:02X}{g:02X}{b:02X}"


def _section(title: str, icon: str, st: dict) -> list:
    """Section header with icon prefix and a thin rule."""
    return [
        Paragraph(f"{icon}  {title}", st["section"]),
        HRFlowable(width="100%", thickness=0.6, color=BORDER, spaceAfter=6),
    ]


def _table_base_style() -> list:
    return [
        ("BACKGROUND",    (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, LGREY]),
        ("GRID",          (0, 0), (-1, -1), 0.4, BORDER),
        ("LEFTPADDING",   (0, 0), (-1, -1), 7),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 7),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, LGREY]),
    ]


# ── Page number footer ─────────────────────────────────────────────────────

def _make_footer(doc_title: str):
    """Returns an onPage callback that draws a footer on every page."""
    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MGREY)
        page_num = f"Page {doc.page}"
        canvas.drawString(LEFT_M, BOTTOM_M - 8, doc_title)
        canvas.drawRightString(PAGE_W - RIGHT_M, BOTTOM_M - 8, page_num)
        canvas.restoreState()
    return _footer


# ── Stats bar ─────────────────────────────────────────────────────────────

def _build_stats_bar(stats: dict, st: dict) -> Table:
    """4-cell summary bar: action items | decisions | open questions | risks."""
    def cell(n: int, label: str) -> list:
        return [
            Paragraph(str(n), st["stat_num"]),
            Paragraph(label,  st["stat_lbl"]),
        ]

    data = [[
        cell(stats.get("action_item_count", 0),   "Action Items"),
        cell(stats.get("decision_count", 0),       "Decisions"),
        cell(stats.get("open_question_count", 0),  "Open Questions"),
        cell(stats.get("risk_count", 0),           "Risks"),
    ]]
    col_w = CONTENT_W / 4
    tbl = Table(data, colWidths=[col_w] * 4, rowHeights=[46])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), LBLUE),
        ("BOX",          (0, 0), (-1, -1), 0.5, BLUE),
        ("LINEBEFORE",   (1, 0), (3, 0), 0.5, BLUE),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
    ]))
    return tbl


# ── Header badge (sentiment + meeting type) ────────────────────────────────

def _build_header_badges(meeting_type: str, sentiment: str,
                         duration: str, st: dict) -> Table:
    type_label = MEETING_TYPE_LABELS.get(meeting_type.lower(), "Meeting")
    s_fg, s_bg = SENTIMENT_COLORS.get(sentiment.lower(), (MGREY, LGREY))

    type_cell = Paragraph(
        f"<font color='#{_hex(BLUE)}'><b>{type_label}</b></font>",
        st["badge"])
    sent_cell = Paragraph(
        f"<font color='#{_hex(s_fg)}'><b>{sentiment.title()}</b></font>",
        st["badge"])
    dur_cell  = Paragraph(
        f"<font color='#{_hex(MGREY)}'>{duration}</font>",
        st["badge"])

    type_tbl = Table([[type_cell]], colWidths=[22 * mm])
    type_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), LBLUE),
        ("BOX",          (0, 0), (-1, -1), 0.5, BLUE),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    sent_tbl = Table([[sent_cell]], colWidths=[22 * mm])
    sent_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), s_bg),
        ("BOX",          (0, 0), (-1, -1), 0.5, s_fg),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    dur_tbl = Table([[dur_cell]], colWidths=[22 * mm])
    dur_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), LGREY),
        ("BOX",          (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))

    row = Table([[type_tbl, sent_tbl, dur_tbl, ""]], 
                colWidths=[26*mm, 28*mm, 32*mm, CONTENT_W - 86*mm])
    row.setStyle(TableStyle([
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",(0, 0), (-1, -1), 4),
        ("TOPPADDING",  (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    return row


# ── Main generator ────────────────────────────────────────────────────────

def generate_pdf_report(summary: dict[str, Any],
                        meeting_date: str | None = None) -> bytes:
    """
    Generate a polished PDF report from the structured summary dict.

    Args:
        summary:      Output of summarize_transcript().
        meeting_date: Optional date string to show in the header.

    Returns:
        Raw PDF bytes suitable for st.download_button or file I/O.
    """
    buffer = io.BytesIO()
    title  = summary.get("meeting_title", "Meeting Summary")
    footer = _make_footer(title)

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=RIGHT_M, leftMargin=LEFT_M,
        topMargin=TOP_M, bottomMargin=BOTTOM_M + 8,
        title=title,
        author="AI Meeting Summarizer",
    )

    st    = _styles()
    story = []

    # ── Header ────────────────────────────────────────────────────────────
    date_str = meeting_date or datetime.now().strftime("%B %d, %Y")
    story.append(Paragraph(title, st["title"]))
    story.append(Paragraph(
        f"Generated {date_str}  ·  AI Meeting Summarizer", st["subtitle"]))
    story.append(Spacer(1, 6))

    # Meeting type + sentiment + duration badges
    m_type   = summary.get("meeting_type", "other")
    sentiment= summary.get("sentiment", "neutral")
    duration = summary.get("duration_estimate", "Unknown")
    story.append(_build_header_badges(m_type, sentiment, duration, st))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=2, color=BLUE, spaceAfter=10))

    # ── Stats bar ─────────────────────────────────────────────────────────
    stats = summary.get("stats", {})
    story.append(_build_stats_bar(stats, st))
    story.append(Spacer(1, 14))

    # ── Attendees ─────────────────────────────────────────────────────────
    attendees = summary.get("attendees", [])
    if attendees:
        story += _section("Attendees", "👥", st)
        story.append(Paragraph(", ".join(attendees), st["body"]))
        story.append(Spacer(1, 6))

    # ── Summary ───────────────────────────────────────────────────────────
    story += _section("Meeting Summary", "📋", st)
    for para in summary.get("summary", "").split("\n\n"):
        if para.strip():
            story.append(Paragraph(para.strip(), st["body"]))
    story.append(Spacer(1, 6))

    # ── Action Items ──────────────────────────────────────────────────────
    action_items = summary.get("action_items", [])
    if action_items:
        story += _section("Action Items", "✅", st)
        # Header row
        header = [
            Paragraph("Task", st["th"]),
            Paragraph("Owner", st["th"]),
            Paragraph("Due Date", st["th"]),
            Paragraph("Priority", st["th"]),
            Paragraph("Context", st["th"]),
        ]
        rows = [header]
        for item in action_items:
            rows.append([
                Paragraph(item.get("task", ""), st["td"]),
                Paragraph(item.get("owner", "Unassigned"), st["td"]),
                Paragraph(item.get("due_date", "TBD"), st["td"]),
                _priority_badge(item.get("priority", "Medium"), st["badge"]),
                Paragraph(item.get("context", ""), st["td"]),
            ])
        tbl = Table(rows, colWidths=[
            55*mm,   # task
            30*mm,   # owner
            25*mm,   # due date
            20*mm,   # priority
            CONTENT_W - 130*mm,   # context
        ])
        tbl.setStyle(TableStyle(_table_base_style()))
        story.append(tbl)
        story.append(Spacer(1, 10))

    # ── Decisions ─────────────────────────────────────────────────────────
    decisions = summary.get("decisions", [])
    if decisions:
        story += _section("Key Decisions", "⚖️", st)
        header = [
            Paragraph("Decision", st["th"]),
            Paragraph("Decided By", st["th"]),
            Paragraph("Rationale", st["th"]),
            Paragraph("Impact", st["th"]),
        ]
        rows = [header]
        for d in decisions:
            rows.append([
                Paragraph(d.get("decision", ""), st["td"]),
                Paragraph(d.get("decided_by", "—"), st["td"]),
                Paragraph(d.get("rationale", "Not mentioned"), st["td"]),
                Paragraph(d.get("impact", "Not mentioned"), st["td"]),
            ])
        tbl = Table(rows, colWidths=[
            55*mm,
            28*mm,
            CONTENT_W * 0.35,
            CONTENT_W * 0.25,
        ])
        tbl.setStyle(TableStyle(_table_base_style()))
        story.append(tbl)
        story.append(Spacer(1, 10))

    # ── Open Questions / Blockers ─────────────────────────────────────────
    questions = summary.get("open_questions", [])
    if questions:
        story += _section("Open Questions & Blockers", "❓", st)
        header = [
            Paragraph("Question / Blocker", st["th"]),
            Paragraph("Assigned To", st["th"]),
            Paragraph("Urgency", st["th"]),
        ]
        rows = [header]
        for q in questions:
            rows.append([
                Paragraph(q.get("question", ""), st["td"]),
                Paragraph(q.get("assigned_to", "Team"), st["td"]),
                _priority_badge(q.get("urgency", "Medium"), st["badge"]),
            ])
        tbl = Table(rows, colWidths=[
            CONTENT_W - 70*mm,
            38*mm,
            20*mm,
        ])
        tbl.setStyle(TableStyle(_table_base_style()))
        story.append(tbl)
        story.append(Spacer(1, 10))

    # ── Risks ─────────────────────────────────────────────────────────────
    risks = summary.get("risks", [])
    if risks:
        story += _section("Risks & Concerns", "⚠️", st)
        header = [
            Paragraph("Risk", st["th"]),
            Paragraph("Likelihood", st["th"]),
            Paragraph("Mitigation", st["th"]),
        ]
        rows = [header]
        for r in risks:
            rows.append([
                Paragraph(r.get("risk", ""), st["td"]),
                _likelihood_badge(r.get("likelihood", "Unknown"), st["badge"]),
                Paragraph(r.get("mitigation", "None discussed"), st["td"]),
            ])
        tbl = Table(rows, colWidths=[
            CONTENT_W * 0.38,
            22*mm,
            CONTENT_W * 0.45,
        ])
        tbl.setStyle(TableStyle(_table_base_style()))
        story.append(tbl)
        story.append(Spacer(1, 10))

    # ── Speaker Contributions ─────────────────────────────────────────────
    speakers = summary.get("speaker_contributions", [])
    if speakers:
        story += _section("Speaker Contributions", "🎙️", st)
        for sp in speakers:
            name = sp.get("speaker", "Unknown")
            role = sp.get("role", "")
            label = f"<b>{name}</b>" + (f" <i>({role})</i>" if role and role != "Unknown" else "")
            story.append(Paragraph(label, st["body"]))
            for pt in sp.get("key_points", []):
                story.append(Paragraph(f"• {pt}", st["item"]))
            owned = sp.get("items_owned", [])
            if owned:
                story.append(Paragraph(
                    f"<i>Owns: {'; '.join(owned)}</i>", st["small"]))
            story.append(Spacer(1, 4))
        story.append(Spacer(1, 6))

    # ── Next Steps ────────────────────────────────────────────────────────
    next_steps = summary.get("next_steps", [])
    if next_steps:
        story += _section("Next Steps", "🚀", st)
        for i, step in enumerate(next_steps, 1):
            story.append(Paragraph(f"{i}. {step}", st["item"]))
        story.append(Spacer(1, 10))

    # ── Footer topics bar ─────────────────────────────────────────────────
    topics = summary.get("key_topics", [])
    if topics:
        story.append(HRFlowable(width="100%", thickness=0.8,
                                color=BORDER, spaceAfter=4))
        story.append(Paragraph(
            f"<b>Key Topics:</b>  {' · '.join(topics)}",
            ParagraphStyle("ft", fontSize=8, fontName="Helvetica",
                           textColor=MGREY, leading=11)))

    # ── Build ─────────────────────────────────────────────────────────────
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()